"""Tests du cœur du pipeline (sans réseau, sans LLM)."""

import json
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quefaire.dedupe import dedupe
from quefaire.demo import demo_events
from quefaire.export import export
from quefaire.geocode import geocode
from quefaire.models import Event
from quefaire.normalize import enrich
from quefaire.registry import load_sector


def make(title="Rendez-vous du samedi", start=None, commune="Grenoble", **kw):
    start = start or (date.today() + timedelta(days=3)).isoformat()
    return Event(title=title, start=start, source_id="t", sector="villemoirieu", commune=commune, **kw)


def test_event_id_stable():
    a, b = make(), make()
    assert a.id == b.id
    assert a.id.startswith("rendez-vous-du-samedi-")


def test_enrich_category_audience_free():
    ev = enrich(make(description="Atelier poterie pour enfants dès 5 ans, entrée libre"))
    assert ev.category == "atelier"
    assert "enfants" in ev.audience
    assert ev.free is True


def test_enrich_price_detected():
    ev = enrich(make(title="Visite guidée", description="Tarif : 12 € par personne"))
    assert ev.free is False
    assert "12" in (ev.price_text or "")


def test_geocode_from_commune_table():
    ev = geocode(make(commune="villemoirieu"), "villemoirieu")
    assert ev.commune == "Villemoirieu"
    assert abs(ev.lat - 45.7192) < 0.01


def test_dedupe_keeps_richest():
    poor = make(description="")
    rich = make(description="Longue description " * 20, url="https://exemple.fr")
    kept = dedupe([poor, rich])
    assert len(kept) == 1
    assert kept[0].url == "https://exemple.fr"


def test_discover_oa_dedupes_and_ranks(monkeypatch):
    import quefaire.cli as cli

    fake = {
        "Grenoble": [
            {"uid": 1, "title": "Agenda de la Ville de Grenoble", "slug": "grenoble",
             "description": "", "url": "u", "official": True},
            {"uid": 2, "title": "Sorties métal underground", "slug": "metal",
             "description": "concerts à Grenoble", "url": "u", "official": False},
        ],
        "Vienne": [
            {"uid": 1, "title": "Agenda de la Ville de Grenoble", "slug": "grenoble",
             "description": "", "url": "u", "official": True},
            {"uid": 3, "title": "Ville de Vienne", "slug": "vienne",
             "description": "", "url": "u", "official": False},
        ],
    }
    monkeypatch.setattr(
        "quefaire.fetchers.openagenda.search_agendas", lambda q: fake.get(q, [])
    )
    monkeypatch.setattr(
        "quefaire.fetchers.openagenda.upcoming_count", lambda uid: {1: 12, 2: 3, 3: 0}[uid]
    )
    import yaml

    out = yaml.safe_load(cli.discover_openagenda("villemoirieu", ["Grenoble", "Vienne"], strict=False))
    uids = [e["url"] for e in out]
    assert sorted(uids) == [1, 2, 3]          # dédupliqué par UID
    assert out[0]["url"] == 1                  # l'agenda officiel sort en premier
    assert uids == [1, 2, 3]                   # puis tri par événements à venir (3 > 0)
    assert "Grenoble, Vienne" in out[0]["comment"]
    assert "12 événements à venir" in out[0]["comment"]
    assert all(e["enabled"] is False for e in out)  # validation humaine requise

    strict = yaml.safe_load(cli.discover_openagenda("villemoirieu", ["Grenoble", "Vienne"], strict=True))
    assert [e["url"] for e in strict] == [1, 3]  # strict : titre doit citer la commune


def test_nord_isere_communes_geocoded():
    for commune in ("Crémieu", "Morestel", "Saint-Chef", "La Verpillière", "Tignieu-Jameyzieu"):
        ev = geocode(make(commune=commune), "villemoirieu")
        assert ev.lat is not None, commune


# base_url → label logique, pour que les providers OpenAI-compatibles branchés
# via l'adaptateur openai (mistral, zai, kimi) restent identifiables côté test.
_BASE_URL_LABEL = {
    "https://api.mistral.ai/v1": "mistral",
    "https://api.z.ai/api/paas/v4": "zai",
    "https://api.moonshot.ai/v1": "kimi",
}


def _fake_autoagent(behaviors: dict):
    """Module autoagent factice.

    behaviors[label] = "texte" ou Exception (constant), ou une liste consommée
    appel par appel (le dernier élément se répète) pour simuler un quota qui
    meurt en cours de run. Le label est le nom logique du provider : le provider
    natif (from_model) ou, pour un provider OpenAI-compatible construit via
    create_provider(ModelConfig(...)), le nom déduit de base_url.
    """

    class FakeResult:
        def __init__(self, output):
            self.output = output

    class FakeModelConfig:
        def __init__(self, provider, model, base_url=None, api_key_env=None):
            self.provider = provider
            self.model = model
            self.base_url = base_url
            self.api_key_env = api_key_env

    class FakeProvider:
        def __init__(self, config):
            self.config = config
            self.label = _BASE_URL_LABEL.get(config.base_url, config.provider)

    class FakeAgent:
        def __init__(self, provider, **kwargs):
            # provider = nom natif (str, via from_model) ou FakeProvider.
            self.label = provider.label if isinstance(provider, FakeProvider) else provider

        @classmethod
        def from_model(cls, provider, model, **kwargs):
            return cls(provider)

        def run(self, prompt):
            behavior = behaviors[self.label]
            if isinstance(behavior, list):
                behavior = behavior.pop(0) if len(behavior) > 1 else behavior[0]
            if isinstance(behavior, Exception):
                raise behavior
            return FakeResult(behavior)

    module = types.ModuleType("autoagent")
    module.Agent = FakeAgent
    module.ModelConfig = FakeModelConfig
    module.create_provider = lambda config: FakeProvider(config)
    return module


def _reset_cache():
    """Cache de contenu vide et isolé du disque pour les tests."""
    import quefaire.cache as c

    c.cache._store = {}
    c.cache._used = set()
    c.cache._loaded = True  # empêche toute lecture de pipeline/cache/content.json
    return c.cache


def _reset_llm_cache():
    import quefaire.llm as llm

    for chain in (llm._CRAWL, llm._CLARIFY):
        chain._resolved = None
        chain._resolution_done = False
        chain._failed.clear()
    return llm


def test_llm_resolve_prefers_primary_when_it_answers(monkeypatch):
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(sys.modules, "autoagent", _fake_autoagent({"gemini": "ok", "deepseek": "ok"}))

    assert llm.resolve() == ("gemini", "gemini-3.5-flash")


def test_llm_resolve_falls_back_to_backup_on_quota_error(monkeypatch):
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({"gemini": RuntimeError("429 quota exceeded"), "deepseek": "ok"}),
    )

    assert llm.resolve() == ("deepseek", "deepseek-v4-flash")


def test_llm_resolve_none_when_both_fail(monkeypatch):
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({"gemini": RuntimeError("quota"), "deepseek": RuntimeError("quota")}),
    )

    assert llm.resolve() is None
    with pytest.raises(RuntimeError):
        llm.get_agent()


def test_llm_resolve_tests_connection_once_per_run(monkeypatch):
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.delenv("QUEFAIRE_LLM2", raising=False)
    calls = []

    class CountingAgent:
        def __init__(self, provider, **kwargs):
            self.provider = provider

        @classmethod
        def from_model(cls, provider, model, **kwargs):
            calls.append(provider)
            return cls(provider)

        def run(self, prompt):
            return types.SimpleNamespace(output="ok")

    module = types.ModuleType("autoagent")
    module.Agent = CountingAgent
    monkeypatch.setitem(sys.modules, "autoagent", module)

    llm.resolve()
    llm.resolve()
    assert calls == ["gemini"]  # un seul test de connexion pour tout le run


def test_llm_failover_mid_run_on_quota(monkeypatch):
    """Vécu en CI : le test de connexion passe (quota encore vivant), puis le
    quota Gemini meurt quelques sources plus loin — run_llm doit basculer sur
    le backup au lieu de laisser toutes les sources suivantes échouer en 429."""
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({
            # test de connexion OK, 1er appel réel OK, puis quota mort
            "gemini": ["ok", "extraction gemini", RuntimeError("HTTP 429 quota exceeded")],
            "deepseek": "extraction deepseek",
        }),
    )

    assert llm.resolve() == ("gemini", "gemini-3.5-flash")
    assert llm.run_llm("extrais").output == "extraction gemini"
    # Le quota meurt ici : bascule transparente, l'appel aboutit sur le backup.
    assert llm.run_llm("extrais").output == "extraction deepseek"
    # La décision est déclassée pour tout le reste du run.
    assert llm.resolve() == ("deepseek", "deepseek-v4-flash")


def test_llm_failover_does_not_swallow_other_errors(monkeypatch):
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({
            "gemini": ["ok", ValueError("réponse mal formée")],
            "deepseek": "ok",
        }),
    )

    with pytest.raises(ValueError):  # pas une erreur de quota : elle remonte
        llm.run_llm("extrais")
    assert llm.resolve() == ("gemini", "gemini-3.5-flash")  # pas déclassé


def test_llm_failover_exhausts_all_candidates(monkeypatch):
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({
            "gemini": ["ok", RuntimeError("429 quota")],
            "deepseek": ["ok", RuntimeError("rate limit reached")],
        }),
    )

    with pytest.raises(RuntimeError, match="Aucun LLM disponible"):
        llm.run_llm("extrais")


def test_make_agent_routes_native_vs_openai_compatible(monkeypatch):
    """Groq est natif (from_model) ; Mistral passe par l'adaptateur openai avec
    base_url + clé dédiée, car from_model ne permet pas de fixer base_url."""
    llm = _reset_llm_cache()
    recorded = {}

    class FakeAgent:
        def __init__(self, provider, **kwargs):
            recorded["agent_provider"] = provider

        @classmethod
        def from_model(cls, provider, model, **kwargs):
            recorded["from_model"] = (provider, model)
            return cls(provider)

    class FakeModelConfig:
        def __init__(self, provider, model, base_url=None, api_key_env=None):
            self.provider, self.model = provider, model
            self.base_url, self.api_key_env = base_url, api_key_env

    def fake_create_provider(config):
        recorded["config"] = config
        return config

    module = types.ModuleType("autoagent")
    module.Agent = FakeAgent
    module.ModelConfig = FakeModelConfig
    module.create_provider = fake_create_provider
    monkeypatch.setitem(sys.modules, "autoagent", module)

    # Groq : natif → from_model, aucune config OpenAI-compatible construite.
    llm._make_agent("groq", "llama-3.3-70b-versatile")
    assert recorded["from_model"] == ("groq", "llama-3.3-70b-versatile")
    assert "config" not in recorded

    # Mistral : adaptateur openai + base_url + clé Mistral.
    llm._make_agent("mistral", "mistral-small-latest")
    cfg = recorded["config"]
    assert (cfg.provider, cfg.model) == ("openai", "mistral-small-latest")
    assert cfg.base_url == "https://api.mistral.ai/v1"
    assert cfg.api_key_env == "MISTRAL_API_KEY"


def test_llm_backups_are_comma_separated_and_ordered(monkeypatch):
    """QUEFAIRE_LLM2 peut lister plusieurs backups : la chaîne est essayée dans
    l'ordre, un provider OpenAI-compatible (Mistral) inclus."""
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv(
        "QUEFAIRE_LLM2", "groq:llama-3.3-70b-versatile, mistral:mistral-small-latest"
    )
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({
            "gemini": RuntimeError("429 quota exceeded"),
            "groq": RuntimeError("rate limit reached"),
            "mistral": "ok",
        }),
    )

    # Gemini puis Groq épuisés → on retombe sur Mistral, dernier de la liste.
    assert llm.resolve() == ("mistral", "mistral-small-latest")


def test_llm_empty_response_falls_back_without_demoting(monkeypatch):
    """Réponse vide du principal (vécu : gemini rend du vide sur les grosses
    pages) → l'appel bascule sur un backup pour CET appel, sans déclasser le
    principal, qui reste utilisé ensuite."""
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "groq:llama-3.3-70b-versatile")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({
            # test de connexion OK, 1er appel vide, puis réponses normales
            "gemini": ["ok", "", "extraction gemini"],
            "groq": "extraction groq",
        }),
    )

    assert llm.resolve() == ("gemini", "gemini-3.5-flash")
    # Réponse vide de gemini → secours groq pour cet appel.
    assert llm.run_llm("extrais").output == "extraction groq"
    # gemini n'a PAS été déclassé : il reste principal et répond au suivant.
    assert llm.budget_healthy() is True
    assert llm.resolve() == ("gemini", "gemini-3.5-flash")
    assert llm.run_llm("extrais").output == "extraction gemini"


def test_llm_empty_everywhere_returns_blank_without_demoting(monkeypatch):
    """Si tous les candidats rendent du vide, run_llm renvoie la réponse vide
    (l'appelant gère 0 fiche) sans déclasser personne."""
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "groq:llama-3.3-70b-versatile")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({"gemini": ["ok", ""], "groq": ""}),
    )

    assert llm.resolve() == ("gemini", "gemini-3.5-flash")
    assert llm.run_llm("extrais").output == ""
    assert llm.budget_healthy() is True  # aucun déclassement
    assert llm.resolve() == ("gemini", "gemini-3.5-flash")


def test_llm_transient_error_falls_back_without_demoting(monkeypatch):
    """Erreur serveur transitoire (HTTP 503) → secours pour cet appel, sans
    déclasser le principal (vécu : Gemini 503 faisait perdre une source)."""
    llm = _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setenv("QUEFAIRE_LLM2", "deepseek:deepseek-v4-flash")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({
            # test OK, puis 503 sur le 1er appel réel, puis réponses normales
            "gemini": ["ok", RuntimeError("HTTP 503 from …generateContent"), "extraction gemini"],
            "deepseek": "extraction deepseek",
        }),
    )

    assert llm.resolve() == ("gemini", "gemini-3.5-flash")
    # 503 sur gemini → secours deepseek pour cet appel seulement
    assert llm.run_llm("extrais").output == "extraction deepseek"
    # gemini pas déclassé : reste principal et répond au suivant
    assert llm.budget_healthy() is True
    assert llm.resolve() == ("gemini", "gemini-3.5-flash")
    assert llm.run_llm("extrais").output == "extraction gemini"


def test_clarify_skipped_when_budget_unhealthy(monkeypatch):
    """Après une bascule quota (budget entamé), clarify n'appelle pas le LLM
    pour les nouveaux (préserve le quota) — le cache resterait servi."""
    import quefaire.llm as llm
    from quefaire.clarify import clarify

    _reset_cache()
    _reset_llm_cache()
    monkeypatch.delenv("QUEFAIRE_LLM_CLARIFY", raising=False)
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    monkeypatch.setitem(sys.modules, "autoagent", _fake_autoagent({"gemini": "{}"}))
    llm._CRAWL._failed.add("gemini:gemini-3.5-flash")  # une bascule a déjà eu lieu

    def boom(prompt):
        raise AssertionError("clarify ne doit pas appeler le LLM si le budget est entamé")

    monkeypatch.setattr(llm._CRAWL, "run", boom)
    events = [make(title="Cet été, faites-vous une terrasse")]
    out = clarify(events)
    assert out is events
    assert events[0].tldr in (None, "")


def test_clarify_fills_tldr_when_budget_healthy(monkeypatch):
    import quefaire.llm as llm  # noqa: F401
    from quefaire.clarify import clarify

    _reset_cache()
    _reset_llm_cache()
    monkeypatch.delenv("QUEFAIRE_LLM_CLARIFY", raising=False)
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    ev = make(title="Cet été, faites-vous une terrasse")
    phrase = "Dîners servis en terrasse jusqu'à minuit, réservation conseillée."
    monkeypatch.setitem(
        sys.modules, "autoagent", _fake_autoagent({"gemini": json.dumps({"0": phrase})})
    )

    out = clarify([ev])
    assert out[0].tldr == phrase


def test_clarify_drops_redundant_paraphrase(monkeypatch):
    """Une phrase qui ne fait que reprendre le titre/la description est écartée
    (elle n'apporte rien au visiteur)."""
    from quefaire.clarify import clarify

    _reset_cache()
    _reset_llm_cache()
    monkeypatch.delenv("QUEFAIRE_LLM_CLARIFY", raising=False)
    monkeypatch.setenv("QUEFAIRE_LLM", "gemini:gemini-3.5-flash")
    ev = make(title="Concert de jazz au parc", description="Un concert de jazz au parc municipal.")
    monkeypatch.setitem(
        sys.modules,
        "autoagent",
        _fake_autoagent({"gemini": json.dumps({"0": "Un concert de jazz au parc municipal."})}),
    )

    out = clarify([ev])
    assert out[0].tldr is None  # paraphrase redondante écartée


def test_clarify_uses_dedicated_chain_even_if_crawl_unhealthy(monkeypatch):
    """Avec un modèle dédié (QUEFAIRE_LLM_CLARIFY), clarify tourne sur SON budget
    même si la chaîne du crawl a déjà basculé."""
    import quefaire.llm as llm
    from quefaire.clarify import clarify

    _reset_cache()
    _reset_llm_cache()
    monkeypatch.setenv("QUEFAIRE_LLM", "deepseek:deepseek-v4-flash")
    monkeypatch.setenv("QUEFAIRE_LLM_CLARIFY", "mistral:mistral-small-latest")
    llm._CRAWL._failed.add("deepseek:deepseek-v4-flash")  # le crawl a basculé
    ev = make(title="Cet été, faites-vous une terrasse")
    phrase = "Dîners servis en terrasse jusqu'à minuit, ambiance guinguette."
    monkeypatch.setitem(
        sys.modules, "autoagent", _fake_autoagent({"mistral": json.dumps({"0": phrase})})
    )

    out = clarify([ev])
    assert out[0].tldr == phrase  # clarify a tourné sur sa chaîne mistral dédiée


def test_page_text_preserves_event_links():
    from quefaire.fetchers.html_llm import _page_text

    html = '<div>Concert de jazz le 5 août <a href="/agenda/jazz-42">en savoir plus</a></div>'
    text = _page_text(html, None)
    assert "/agenda/jazz-42" in text  # le href survit au nettoyage


def test_extract_events_llm_absolutizes_event_url(monkeypatch):
    """Le lien d'événement rendu par le LLM (souvent relatif) est résolu en
    absolu depuis la page source pour un lien profond."""
    import types as _types

    from quefaire.fetchers import html_llm
    from quefaire.models import Source

    _reset_cache()
    payload = json.dumps([
        {"title": "Concert jazz", "start": (date.today() + timedelta(days=5)).isoformat(),
         "url": "/agenda/jazz-42"},
        {"title": "Sans lien", "start": (date.today() + timedelta(days=6)).isoformat(),
         "url": None},
    ])
    monkeypatch.setattr(
        html_llm, "run_llm", lambda prompt: _types.SimpleNamespace(output=payload)
    )
    src = Source(id="html-x", name="X", type="html", url="https://ot-ville.fr/agenda/",
                 commune="Grenoble")
    events = html_llm.extract_events_llm("texte", src, "villemoirieu", "https://ot-ville.fr/agenda/")
    assert events[0].url == "https://ot-ville.fr/agenda/jazz-42"  # relatif → absolu
    assert events[1].url == "https://ot-ville.fr/agenda/"  # pas de lien → page source


def test_extraction_cache_hit_skips_llm(monkeypatch):
    """Contenu de page inchangé → réutilisé sans rappeler le LLM ; contenu
    différent → nouvel appel. Répétabilité + quota."""
    import types as _types

    from quefaire.fetchers import html_llm
    from quefaire.models import Source

    _reset_cache()
    calls = {"n": 0}
    payload = json.dumps(
        [{"title": "Concert", "start": (date.today() + timedelta(days=5)).isoformat()}]
    )

    def fake_run(prompt):
        calls["n"] += 1
        return _types.SimpleNamespace(output=payload)

    monkeypatch.setattr(html_llm, "run_llm", fake_run)
    src = Source(id="html-x", name="X", type="html", url="https://ex.fr/a/", commune="Grenoble")

    e1 = html_llm.extract_events_llm("même texte", src, "villemoirieu", "https://ex.fr/a/")
    e2 = html_llm.extract_events_llm("même texte", src, "villemoirieu", "https://ex.fr/a/")
    assert calls["n"] == 1  # 2ᵉ extraction servie par le cache
    assert [e.id for e in e1] == [e.id for e in e2]

    html_llm.extract_events_llm("texte modifié", src, "villemoirieu", "https://ex.fr/a/")
    assert calls["n"] == 2  # contenu changé → nouvel appel LLM


def test_cache_save_prunes_unused_keys(tmp_path, monkeypatch):
    """save() ne conserve que les clés vues ce run (élague les sources
    retirées / anciennes versions de page)."""
    import quefaire.cache as c

    _reset_cache()
    monkeypatch.setattr(c, "CACHE_PATH", tmp_path / "content.json")
    c.cache._store = {"extract:stale": ["vieux"], "extract:keep": ["gardé"]}
    c.cache._used = set()
    c.cache.get("extract:keep")  # seule clé touchée ce run
    c.cache.put("extract:new", ["neuf"])
    c.cache.save()

    saved = json.loads((tmp_path / "content.json").read_text(encoding="utf-8"))
    assert set(saved) == {"extract:keep", "extract:new"}  # 'stale' élaguée


def test_validate_public_url_accepts_public_rejects_unsafe():
    from quefaire.security import UnsafeUrlError, validate_public_url

    validate_public_url("https://93.184.216.34/agenda")  # IP publique littérale : OK

    unsafe = [
        "http://127.0.0.1/",                       # loopback
        "http://169.254.169.254/latest/meta-data/",  # métadonnées cloud
        "http://10.1.2.3/agenda",                  # réseau privé
        "http://[::1]/",                           # loopback IPv6
        "ftp://exemple.fr/",                        # schéma interdit
        "file:///etc/passwd",                      # schéma interdit
        "http://user:pass@93.184.216.34/",         # identifiants dans l'URL
        "https://93.184.216.34:8080/",             # port détourné
    ]
    for url in unsafe:
        with pytest.raises(UnsafeUrlError):
            validate_public_url(url)


def test_evaluate_url_counts_unique_events(monkeypatch):
    from quefaire import evaluate

    ev_new = make(title="Concert inédit au kiosque")
    ev_dup = make(title="Marché hebdomadaire", commune="Grenoble")
    monkeypatch.setattr(evaluate, "fetch_source", lambda source, sector: [ev_new, ev_dup])

    known = {ev_dup.dedupe_key()}
    report = evaluate.evaluate_url(
        "https://93.184.216.34/agenda", "villemoirieu", source_type="html", keys=known
    )
    assert report["fetched"] == 2
    assert report["unique"] == 1
    assert report["duplicates"] == 1
    assert report["events"][0]["title"] == "Concert inédit au kiosque"


def test_evaluate_url_rejects_internal_target(monkeypatch):
    from quefaire import evaluate
    from quefaire.security import UnsafeUrlError

    def must_not_fetch(*a, **k):
        raise AssertionError("le fetch ne doit pas avoir lieu pour une URL refusée")

    monkeypatch.setattr(evaluate, "fetch_source", must_not_fetch)
    with pytest.raises(UnsafeUrlError):
        evaluate.evaluate_url("http://169.254.169.254/", "villemoirieu", source_type="html")


def test_http_get_guard_blocks_redirect_to_internal(monkeypatch):
    """Sous garde-fou, une redirection vers une IP interne est bloquée avant
    d'être suivie (anti-SSRF par rebond)."""
    from quefaire.fetchers import base
    from quefaire.security import UnsafeUrlError

    calls = []

    class FakeResp:
        def __init__(self, code, location=None):
            self.status_code = code
            self.headers = {"Location": location} if location else {}
            self.is_redirect = code in (301, 302, 303, 307, 308)

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResp(302, "http://127.0.0.1/")  # redirige vers du loopback

    monkeypatch.setattr(base.requests, "get", fake_get)
    base.set_ssrf_guard(True)
    try:
        with pytest.raises(UnsafeUrlError):
            base.http_get("https://93.184.216.34/")
    finally:
        base.set_ssrf_guard(False)
    assert calls == ["https://93.184.216.34/"]  # l'URL interne n'a jamais été requêtée


_SAMPLE_SOURCES = """sector:
  name: Test
sources:
  # commentaire à préserver
  - id: html-a
    name: A
    type: html
    url: https://a.fr/agenda
    enabled: true
  - id: rss-b
    name: B
    type: rss
    url: https://b.fr/rss
    enabled: false
"""


def _sources_dir(tmp_path, monkeypatch):
    import quefaire.registry as registry

    (tmp_path / "test.yaml").write_text(_SAMPLE_SOURCES, encoding="utf-8")
    monkeypatch.setattr(registry, "SOURCES_DIR", tmp_path)
    return registry


def test_registry_append_source_preserves_comments_and_dedups(tmp_path, monkeypatch):
    registry = _sources_dir(tmp_path, monkeypatch)

    ok = registry.append_source(
        "test", {"id": "html-c", "name": "C", "type": "html", "url": "https://c.fr/a", "commune": "Vienne"}
    )
    assert ok is True
    text = (tmp_path / "test.yaml").read_text(encoding="utf-8")
    assert "commentaire à préserver" in text  # commentaires intacts
    assert "id: html-c" in text and "enabled: true" in text
    assert "commune: Vienne" in text
    # Rechargé, le secteur voit bien la nouvelle source active.
    assert "html-c" in {s.id for s in registry.load_sector("test").sources}
    # Doublon d'URL → ignoré.
    assert registry.append_source("test", {"id": "x", "name": "x", "type": "html", "url": "https://c.fr/a"}) is False


def test_registry_set_enabled_flips_only_target(tmp_path, monkeypatch):
    registry = _sources_dir(tmp_path, monkeypatch)

    assert registry.set_enabled("test", "html-a", False) is True
    text = (tmp_path / "test.yaml").read_text(encoding="utf-8")
    assert "commentaire à préserver" in text
    lines = text.splitlines()
    # html-a passe à false, rss-b reste false, aucun autre changement.
    assert any("id: html-a" in lines[i] and "enabled: false" in lines[i + 4] for i in range(len(lines) - 4))
    assert registry.set_enabled("test", "html-a", False) is False  # déjà dans l'état


def test_suggest_keeps_only_live_agendas_and_caps(monkeypatch):
    """Anti-spam : suggest n'ouvre d'issue que pour les agendas avec événements
    à venir, et plafonne le nombre (leçon : un run avait créé 540 issues)."""
    import yaml

    import quefaire.cli as cli
    import quefaire.registry as registry

    cands = []
    for i in range(40):  # i%3 == 0 → dormant (0 événement), sinon vivant
        cands.append({
            "id": f"oa-{i}", "name": f"Agenda {i}", "type": "openagenda", "url": str(i),
            "commune": "Grenoble", "comment": f"{i % 3} événements à venir, trouvé via : Grenoble",
        })
    cands.append({  # compte inconnu (pas de « à venir ») → écarté
        "id": "oa-x", "name": "X", "type": "openagenda", "url": "999", "comment": "trouvé via : Grenoble",
    })
    monkeypatch.setattr(cli, "discover_openagenda", lambda s, c, strict: yaml.safe_dump(cands, allow_unicode=True))
    monkeypatch.setattr(registry, "_existing_urls", lambda s: set())

    out = cli.suggest("villemoirieu", use_llm=False)
    assert len(out) == cli.MAX_SUGGESTIONS  # plafonné
    assert "999" not in {c["url"] for c in out}  # dormant/inconnu écarté


def test_health_flags_stale_sources():
    import quefaire.health as health_mod

    h = health_mod._Health()
    h._loaded = True  # pas de lecture disque
    h.record("actif", produced=True, today="2026-01-01")
    h.record("mort", produced=False, today="2026-01-01")
    h.record("recent", produced=False, today="2026-02-14")

    ids = ["actif", "mort", "recent"]
    assert h.stale_ids(ids, today="2026-01-20") == []  # dans la fenêtre de grâce
    stale = h.stale_ids(ids, today="2026-02-15")
    assert set(stale) == {"actif", "mort"}  # > 30 j sans production
    assert "recent" not in stale  # source fraîche épargnée


def test_http_get_retries_on_transient_network_error(monkeypatch):
    """Un aléa réseau ponctuel (IncompleteRead) est rejoué au lieu de faire
    perdre la source ; un statut HTTP explicite (404) n'est pas rejoué."""
    from http.client import IncompleteRead

    from quefaire.fetchers import base

    monkeypatch.setattr(base.time, "sleep", lambda s: None)  # pas d'attente réelle

    class FakeResp:
        def raise_for_status(self):
            pass

    attempts = {"n": 0}

    def flaky_get(url, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise IncompleteRead(b"")  # 1er coup : échec transitoire
        return FakeResp()

    monkeypatch.setattr(base.requests, "get", flaky_get)
    assert isinstance(base.http_get("https://exemple.fr/agenda"), FakeResp)
    assert attempts["n"] == 2  # une reprise, puis succès

    # Une erreur réseau persistante finit par remonter (source sautée en amont).
    attempts["n"] = 0
    monkeypatch.setattr(base.requests, "get", lambda url, **k: (_ for _ in ()).throw(IncompleteRead(b"")))
    with pytest.raises(IncompleteRead):
        base.http_get("https://exemple.fr/agenda")


def test_demo_and_export_roundtrip(tmp_path):
    sector = load_sector("villemoirieu")
    events = [enrich(geocode(e, "villemoirieu")) for e in demo_events()]
    meta = export(sector, dedupe(events), tmp_path)
    data = json.loads(
        (tmp_path / "cities" / "villemoirieu" / "events.json").read_text(encoding="utf-8")
    )
    assert meta["event_count"] == len(data) > 20
    assert all(e["start"] >= date.today().isoformat()[:4] for e in data)
    # La démo est autoportante : chaque événement porte ses coordonnées
    # (demo._COORDS), indépendamment du CSV du secteur. Garde-fou : si une
    # commune de la démo n'a pas de coordonnées, ce test échoue.
    assert all(e["lat"] is not None for e in data)


def test_export_writes_cities_manifest(tmp_path):
    sector = load_sector("villemoirieu")
    events = [enrich(geocode(e, "villemoirieu")) for e in demo_events()]
    meta = export(sector, dedupe(events), tmp_path)

    data = json.loads((tmp_path / "cities.json").read_text(encoding="utf-8"))
    assert data["generated_at"]
    vm = next(c for c in data["cities"] if c["id"] == "villemoirieu")
    assert vm["name"] == "Villemoirieu"
    assert vm["radius_minutes"] == 60
    assert abs(vm["center"]["lat"] - 45.7192) < 0.01
    assert vm["event_count"] == meta["event_count"]
    assert vm["generated_at"] == meta["generated_at"]
    assert vm["url"] == "villemoirieu/"  # ville crawlée → sous-chemin
    # Pont-de-Salars est référencée (registre) mais pas crawlée → « en préparation ».
    pds = next(c for c in data["cities"] if c["id"] == "pont-de-salars")
    assert pds["url"] is None and pds["event_count"] is None


def test_cities_manifest_merge_preserves_url(tmp_path):
    # Un `url` renseigné à la main (déploiement dédié) survit à un ré-export
    # (chaque crawl ne connaît que son secteur, on fusionne l'existant).
    (tmp_path / "cities.json").write_text(
        json.dumps({"generated_at": "x", "cities": [
            {"id": "villemoirieu", "url": "https://villemoirieu.quefaire.fr"}
        ]}), encoding="utf-8",
    )
    sector = load_sector("villemoirieu")
    export(sector, dedupe([enrich(geocode(e, "villemoirieu")) for e in demo_events()]), tmp_path)
    data = json.loads((tmp_path / "cities.json").read_text(encoding="utf-8"))
    vm = next(c for c in data["cities"] if c["id"] == "villemoirieu")
    assert vm["url"] == "https://villemoirieu.quefaire.fr"


# --- Épicentre + rayon (geo.py) ------------------------------------------------

def test_travel_minutes_matches_front_calibration():
    from quefaire.geo import haversine_km, travel_minutes

    # 1 h de voiture ≈ 48 km à vol d'oiseau (même formule que nlsearch.js).
    assert travel_minutes(48) == pytest.approx(60, abs=1)
    # Distance nulle → 0 min ; monotone croissante.
    assert travel_minutes(0) == 0
    assert travel_minutes(10) < travel_minutes(30) < travel_minutes(60)
    # Haversine symétrique et cohérente (Villemoirieu → Lyon ≈ 31 km).
    d = haversine_km(45.7192, 5.2431, 45.7578, 4.8320)
    assert 28 < d < 34
    assert haversine_km(45.7192, 5.2431, 45.7578, 4.8320) == pytest.approx(
        haversine_km(45.7578, 4.8320, 45.7192, 5.2431)
    )


def test_within_radius_epicentre_villemoirieu():
    from quefaire.geo import within_radius

    center = (45.7192, 5.2431)  # Villemoirieu

    lyon = make(commune="Lyon")
    lyon.lat, lyon.lon = 45.7578, 4.8320           # ~42 min → dans le rayon
    assert within_radius(lyon, *center, 60)

    amberieu = make(commune="Ambérieu-en-Bugey")
    amberieu.lat, amberieu.lon = 45.9583, 5.3547   # Ain, ~36 min → dans le rayon
    assert within_radius(amberieu, *center, 60)

    grenoble = make(commune="Grenoble")
    grenoble.lat, grenoble.lon = 45.1885, 5.7245   # sud-Isère, > 1 h → écarté
    assert not within_radius(grenoble, *center, 60)


def test_within_radius_keeps_events_without_coordinates():
    from quefaire.geo import within_radius

    ev = make(commune="Inconnue")  # pas de lat/lon → conservé par défaut
    assert ev.lat is None
    assert within_radius(ev, 45.7192, 5.2431, 60)


def test_sector_carries_radius_minutes():
    sector = load_sector("villemoirieu")
    assert sector.radius_minutes == 60
    assert abs(sector.center_lat - 45.7192) < 0.01


# --- Cloisonnement des caches par ville (crawl multi-villes) --------------------

def test_cache_partitioned_per_city(tmp_path, monkeypatch):
    import quefaire.cache as c

    monkeypatch.setattr(c, "CACHE_DIR", tmp_path)
    cache = c._ContentCache()
    cache.bind("ville-a")
    cache.put("extract:a", ["A"])
    cache.save()
    cache.bind("ville-b")   # simule le crawl de la ville suivante
    cache.put("extract:b", ["B"])
    cache.save()

    a = json.loads((tmp_path / "ville-a" / "content.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "ville-b" / "content.json").read_text(encoding="utf-8"))
    assert a == {"extract:a": ["A"]}   # non évincé par le crawl de ville-b
    assert b == {"extract:b": ["B"]}


def test_health_partitioned_per_city(tmp_path, monkeypatch):
    import quefaire.health as h

    monkeypatch.setattr(h, "HEALTH_DIR", tmp_path)
    health = h._Health()
    health.bind("ville-a")
    health.record("src-a", produced=True, today="2026-01-01")
    health.save(keep_ids={"src-a"})
    health.bind("ville-b")
    health.record("src-b", produced=True, today="2026-01-01")
    health.save(keep_ids={"src-b"})

    a = json.loads((tmp_path / "ville-a" / "source_health.json").read_text(encoding="utf-8"))
    assert "src-a" in a  # l'état de ville-a survit au crawl de ville-b


# --- Activités permanentes ---------------------------------------------------

# Réponse Overpass réaliste : un musée (node), un château (way avec `center`),
# un parc anonyme (à écarter), une piscine privée (à écarter), une œuvre d'art
# insolite, et un objet hors rayon.
OVERPASS_SAMPLE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 44.28, "lon": 2.73,
         "tags": {"tourism": "museum", "name": "Musée du Rouergue",
                  "website": "musee-rouergue.fr", "opening_hours": "Tu-Su 10:00-18:00",
                  "addr:city": "Pont-de-Salars", "wikidata": "Q1"}},
        {"type": "way", "id": 2, "center": {"lat": 44.30, "lon": 2.75},
         "tags": {"historic": "castle", "name": "Château de Bouloc", "fee": "yes"}},
        {"type": "node", "id": 3, "lat": 44.28, "lon": 2.74,
         "tags": {"leisure": "park"}},  # sans nom → écarté
        {"type": "node", "id": 4, "lat": 44.28, "lon": 2.74,
         "tags": {"leisure": "park", "name": "Square des Tilleuls"}},  # anonyme → écarté
        {"type": "node", "id": 5, "lat": 44.29, "lon": 2.72,
         "tags": {"leisure": "swimming_pool", "name": "Piscine privée", "access": "private"}},
        {"type": "node", "id": 6, "lat": 44.27, "lon": 2.71,
         "tags": {"tourism": "artwork", "name": "La Girafe de ferraille"}},
        {"type": "node", "id": 7, "lat": 48.85, "lon": 2.35,
         "tags": {"tourism": "museum", "name": "Louvre"}},  # Paris → hors rayon
    ]
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_places_osm_mapping_and_radius(monkeypatch):
    from quefaire import places

    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp(OVERPASS_SAMPLE)
    )
    found = places.fetch_osm(load_sector("pont-de-salars"))
    by_name = {p.name: p for p in found}

    assert set(by_name) == {"Musée du Rouergue", "Château de Bouloc", "La Girafe de ferraille"}
    assert by_name["Musée du Rouergue"].category == "musee"
    assert by_name["Château de Bouloc"].category == "patrimoine"  # historic prime sur tourism
    assert by_name["Château de Bouloc"].lat == 44.30  # `center` des ways exploité
    assert by_name["Château de Bouloc"].fee is True
    # Le site sans schéma est complété, pas recopié tel quel.
    assert by_name["Musée du Rouergue"].url == "https://musee-rouergue.fr"
    # Insolite : l'œuvre de bord de route oui, le musée référencé sur wikidata non.
    assert by_name["La Girafe de ferraille"].unusual is True
    assert by_name["Musée du Rouergue"].unusual is False


def test_places_merge_preserves_enrichment():
    from quefaire.models import Place
    from quefaire.places import merge

    old = Place(name="Musée", category="musee", source_id="osm", sector="s",
                external_id="node/1", tldr="Une phrase déjà payée", rating=4.5,
                rating_count=120, rating_source="google", unusual=True,
                first_seen="2026-01-01", last_seen="2026-06-01")
    fresh = Place(name="Musée du Rouergue", category="musee", source_id="osm", sector="s",
                  external_id="node/1", opening_hours="Mo-Fr 09:00-17:00")

    [merged] = merge([old], [fresh], today="2026-08-02")
    assert merged.name == "Musée du Rouergue"          # OSM fait autorité sur les faits
    assert merged.opening_hours == "Mo-Fr 09:00-17:00"
    assert merged.tldr == "Une phrase déjà payée"      # enrichissement conservé
    assert merged.rating == 4.5
    assert merged.unusual is True
    assert merged.first_seen == "2026-01-01"           # découverte d'origine gardée
    assert merged.last_seen == "2026-08-02"


def test_places_merge_keeps_then_drops_missing():
    from quefaire.models import Place
    from quefaire.places import merge

    gone = Place(name="Cinéma fermé", category="cinema", source_id="osm", sector="s",
                 external_id="node/9", last_seen="2026-07-28")
    # Absente depuis peu : conservée (Overpass peut avoir hoqueté).
    assert [p.name for p in merge([gone], [], today="2026-08-02")] == ["Cinéma fermé"]
    # Absente depuis plus de deux sweeps : retirée.
    assert merge([gone], [], today="2026-09-15") == []


def test_places_roundtrip_and_place_count(tmp_path):
    from quefaire.export import _count_places
    from quefaire.models import Place
    from quefaire.places import load, save

    items = [Place(name="Ludothèque", category="ludotheque", source_id="osm",
                   sector="pont-de-salars", external_id="node/3", lat=44.2, lon=2.7)]
    save(items, "pont-de-salars", tmp_path)
    [back] = load("pont-de-salars", tmp_path)
    assert back.name == "Ludothèque" and back.category == "ludotheque"
    assert back.id == items[0].id  # identifiant stable entre deux passages
    assert _count_places("pont-de-salars", tmp_path) == 1
    assert _count_places("ville-sans-activites", tmp_path) == 0


def test_place_category_falls_back():
    from quefaire.models import Place

    p = Place(name="X", category="n-importe-quoi", source_id="osm", sector="s")
    assert p.category == "autre"


def test_ratings_skipped_without_key(monkeypatch, caplog):
    from quefaire import ratings
    from quefaire.models import Place

    monkeypatch.delenv("GOOGLE_PLACES_KEY", raising=False)
    monkeypatch.delenv("TRIPADVISOR_API_KEY", raising=False)
    assert ratings.provider() is None
    p = Place(name="X", category="musee", source_id="osm", sector="s", lat=44.0, lon=2.0)
    assert ratings.enrich([p])[0].rating is None  # dégradation gracieuse


def test_radius_km_matches_front_approximation():
    from quefaire.geo import radius_km, travel_minutes

    km = radius_km(60)
    assert 47 < km < 49  # 1 h de voiture ≈ 48 km, comme annoncé dans la doc
    assert travel_minutes(km) == pytest.approx(60, abs=0.1)


# --- DATAtourisme et signaux de qualité --------------------------------------

# Flux JSON-LD réaliste : formes de valeurs volontairement hétérogènes (chaîne
# nue, {"@value"}, dictionnaire de langues), car les producteurs DATAtourisme
# remplissent l'ontologie inégalement.
DT_SAMPLE = {
    "@graph": [
        {
            "@id": "https://data.datatourisme.fr/1",
            "@type": ["PointOfInterest", "CulturalSite", "Museum"],
            "rdfs:label": {"fr": ["Musée du Rouergue"]},
            "hasDescription": {"shortDescription": {"fr": ["Outils et costumes du Rouergue."]}},
            "hasLabel": [{"rdfs:label": {"fr": ["Musée de France"]}}, "Qualité Tourisme"],
            "isLocatedAt": {
                "schema:geo": {"schema:latitude": "44.28", "schema:longitude": "2.73"},
                "schema:address": {"schema:addressLocality": "Pont-de-Salars",
                                   "schema:streetAddress": "3 rue du Moulin"},
            },
            "hasContact": {"foaf:homepage": ["https://musee-rouergue.fr"],
                           "schema:telephone": "0565000000"},
        },
        {
            "@id": "https://data.datatourisme.fr/2",
            "@type": ["PointOfInterest", "SportsAndLeisurePlace"],
            "rdfs:label": "Accrobranche du Lévézou",
            "isLocatedAt": {"schema:geo": {"schema:latitude": 44.30, "schema:longitude": 2.75}},
        },
        {  # hors rayon : doit être écarté
            "@id": "https://data.datatourisme.fr/3",
            "@type": ["Museum"],
            "rdfs:label": "Louvre",
            "isLocatedAt": {"schema:geo": {"schema:latitude": 48.86, "schema:longitude": 2.34}},
        },
        {  # sans coordonnées : inexploitable
            "@id": "https://data.datatourisme.fr/4",
            "@type": ["Museum"],
            "rdfs:label": "Musée fantôme",
        },
    ]
}


def test_datatourisme_parses_heterogeneous_jsonld(monkeypatch):
    from quefaire import datatourisme

    monkeypatch.setenv(datatourisme.FLUX_ENV, "https://flux.test/x")
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp(DT_SAMPLE)
    )
    found = datatourisme.fetch(load_sector("pont-de-salars"))
    by_name = {p.name: p for p in found}

    assert set(by_name) == {"Musée du Rouergue", "Accrobranche du Lévézou"}
    musee = by_name["Musée du Rouergue"]
    assert musee.category == "musee"           # Museum gagne sur PointOfInterest
    assert musee.commune == "Pont-de-Salars"
    assert musee.url == "https://musee-rouergue.fr"
    assert "Outils et costumes" in musee.description
    # Labels reconnus quelle que soit leur forme (objet imbriqué ou chaîne nue).
    assert set(musee.quality) == {"musee-de-france", "qualite-tourisme"}
    assert musee.providers == ["datatourisme"]
    assert by_name["Accrobranche du Lévézou"].category == "sport-loisir"


def test_datatourisme_skipped_without_flux(monkeypatch):
    from quefaire import datatourisme

    monkeypatch.delenv(datatourisme.FLUX_ENV, raising=False)
    assert datatourisme.available() is False
    assert datatourisme.fetch(load_sector("pont-de-salars")) == []  # complément absent ≠ échec


def test_osm_quality_signals():
    from quefaire.places import _quality_of

    assert "monument-historique" in _quality_of({"heritage:operator": "mhs", "ref:mhs": "PA00"})
    assert "notoriete" in _quality_of({"wikidata": "Q42"})
    assert _quality_of({}) == []


def test_dedupe_providers_merges_same_place():
    from quefaire.models import Place
    from quefaire.places import dedupe_providers

    osm = Place(name="Musée du Rouergue", category="musee", source_id="osm", sector="s",
                external_id="node/1", lat=44.2800, lon=2.7300,
                opening_hours="Tu-Su 10:00-18:00", quality=["notoriete"], providers=["osm"])
    dt = Place(name="Le Musée du Rouergue", category="musee", source_id="datatourisme",
               sector="s", external_id="https://data.datatourisme.fr/1",
               lat=44.2802, lon=2.7305, description="Outils et costumes.",
               url="https://musee-rouergue.fr", quality=["musee-de-france"],
               providers=["datatourisme"])

    [merged] = dedupe_providers([osm, dt])
    # « Le » ignoré, 20 m d'écart, même catégorie → un seul lieu.
    assert merged.description == "Outils et costumes."   # apporté par DATAtourisme
    assert merged.url == "https://musee-rouergue.fr"
    assert merged.opening_hours == "Tu-Su 10:00-18:00"   # apporté par OSM
    assert set(merged.quality) == {"notoriete", "musee-de-france"}
    assert set(merged.providers) == {"osm", "datatourisme"}
    assert merged.external_id == "node/1"                # l'id OSM prime (stable)
    assert merged.source_id == "osm"


def test_dedupe_providers_keeps_distinct_places():
    from quefaire.models import Place
    from quefaire.places import dedupe_providers

    a = Place(name="Cinéma Le Royal", category="cinema", source_id="osm", sector="s",
              external_id="node/1", lat=44.28, lon=2.73)
    b = Place(name="Cinéma Le Rex", category="cinema", source_id="osm", sector="s",
              external_id="node/2", lat=44.28, lon=2.73)          # même point, autre nom
    c = Place(name="Cinéma Le Royal", category="cinema", source_id="osm", sector="s",
              external_id="node/3", lat=44.40, lon=2.90)          # même nom, 15 km
    assert len(dedupe_providers([a, b, c])) == 3


def test_merge_keeps_labels_when_flux_unavailable():
    from quefaire.models import Place
    from quefaire.places import merge

    old = Place(name="Musée", category="musee", source_id="osm", sector="s",
                external_id="node/1", quality=["musee-de-france"],
                providers=["osm", "datatourisme"], last_seen="2026-07-30")
    # Sweep OSM seule : le label DATAtourisme ne doit pas être perdu.
    fresh = Place(name="Musée", category="musee", source_id="osm", sector="s",
                  external_id="node/1", quality=["notoriete"], providers=["osm"])
    [out] = merge([old], [fresh], today="2026-08-02")
    assert set(out.quality) == {"notoriete", "musee-de-france"}
    assert set(out.providers) == {"osm", "datatourisme"}


def test_notable_excludes_wikipedia_only():
    from quefaire.models import NOTABLE_LABELS

    assert "notoriete" not in NOTABLE_LABELS       # notoriété ≠ distinction
    assert "monument-historique" in NOTABLE_LABELS
