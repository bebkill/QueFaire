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
    def __init__(self, payload, content=None):
        self._payload = payload
        # `content` sert au mode FLUX, qui lit les octets bruts pour reconnaître
        # l'emballage (ZIP, gzip, JSON nu) au lieu de supposer du JSON.
        self._content = content

    def json(self):
        return self._payload

    @property
    def content(self):
        if self._content is not None:
            return self._content
        import json as _json

        return _json.dumps(self._payload).encode("utf-8")


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
    # Présomption d'insolite : l'œuvre de bord de route oui, le musée référencé
    # sur wikidata non. `unusual` reste faux tant que le LLM n'a pas tranché.
    assert by_name["La Girafe de ferraille"].unusual_hint is True
    assert by_name["Musée du Rouergue"].unusual_hint is False
    assert all(p.unusual is False for p in found)


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


def test_places_merge_drops_deliberately_excluded_type():
    """Un resserrement des règles doit s'appliquer TOUT DE SUITE.

    Le sursis de deux sweeps encaisse une panne de fournisseur ; il ne doit pas
    maintenir en vie ce qu'une règle vient d'écarter, sinon un resserrement met
    quinze jours à produire son effet.
    """
    from quefaire.models import Place
    from quefaire.places import merge

    # `historic=memorial` n'est plus classé : la fiche part au premier passage.
    memorial = Place(name="Monument aux morts", category="patrimoine", source_id="osm",
                     sector="s", external_id="node/7", tags=["osm:historic=memorial"],
                     last_seen="2026-08-01")
    assert merge([memorial], [], today="2026-08-02") == []

    # Toujours classée, juste absente de la sweep du jour : conservée.
    cinema = Place(name="Cinéma", category="cinema", source_id="osm", sector="s",
                   external_id="node/8", tags=["osm:amenity=cinema"], last_seen="2026-08-01")
    assert [p.name for p in merge([cinema], [], today="2026-08-02")] == ["Cinéma"]

    # Type DATAtourisme encore retenu : conservé de même.
    dt = Place(name="Musée", category="musee", source_id="datatourisme", sector="s",
               external_id="dt/1", tags=["dt:Museum"], last_seen="2026-08-01")
    assert [p.name for p in merge([dt], [], today="2026-08-02")] == ["Musée"]


def test_filter_relevant_drops_mute_places_in_every_category():
    """Une fiche muette n'est pas exploitable, quelle que soit sa catégorie.

    Un signal = quelqu'un a jugé le lieu digne d'être documenté. Aucun signal,
    c'est six sources qui disent non en même temps — et la tuile n'aurait rien
    à montrer ni de destination où envoyer le visiteur.
    """
    from quefaire.models import Place
    from quefaire.places import filter_relevant

    def pat(name, **kw):
        return Place(name=name, category="patrimoine", source_id="osm", sector="s", **kw)

    muet = pat("Ancien four à chaux")
    # Le tldr est DÉRIVÉ : il ne doit pas suffire à sauver une fiche muette,
    # sinon le filtre se mord la queue.
    présenté = pat("Ancien moulin", tldr="Une jolie phrase déjà payée")
    gardés = [
        pat("Abbatiale de Conques", description="Chef-d'œuvre roman."),
        pat("Château de Calmont", url="https://example.org"),
        pat("Beffroi de Millau", opening_hours="Mo-Su 10:00-18:00"),
        pat("Ancien prieuré", quality=["monument-historique"]),
        pat("Abbaye de Bonneval", providers=["osm", "datatourisme"]),
        pat("Chapelle Saint-Roch", image_url="https://commons.test/x.jpg"),
    ]
    # AUCUNE catégorie n'est exemptée : une salle des fêtes sans un mot tombe
    # comme un four à chaux sans un mot.
    salle = Place(name="Salle des Tilleuls", category="spectacle", source_id="osm", sector="s")
    piscine = Place(name="Piscine", category="parc-aquatique", source_id="osm", sector="s")

    kept = filter_relevant([muet, présenté, *gardés, salle, piscine])
    assert [p.name for p in kept] == [p.name for p in gardés]


def test_place_id_distinguishes_homonyms_without_external_id():
    """Quatre « Point lecture » dans quatre communes = quatre pages distinctes.

    Sans identifiant de source, l'id retombait sur le nom seul : les homonymes
    partageaient une page, et le visiteur y lisait les coordonnées d'une autre
    commune — exactement ce que la page de détail promet de ne pas faire.
    """
    from quefaire.models import Place

    def lecture(lat, lon):
        return Place(name="Point lecture", category="ludotheque", source_id="datatourisme",
                     sector="s", lat=lat, lon=lon)

    a, b = lecture(44.4816, 2.2594), lecture(44.4114, 2.1891)
    assert a.id != b.id
    # Stable d'un run à l'autre : même position → même URL.
    assert a.id == lecture(44.4816, 2.2594).id
    # Un identifiant de source reste prioritaire : un lieu déplacé de quelques
    # mètres ne doit pas changer d'URL.
    avec_id = Place(name="Musée", category="musee", source_id="osm", sector="s",
                    external_id="node/1", lat=44.0, lon=2.0)
    bouge = Place(name="Musée", category="musee", source_id="osm", sector="s",
                  external_id="node/1", lat=44.001, lon=2.001)
    assert avec_id.id == bouge.id


def test_datatourisme_prefers_french():
    """Le flux rend fr ET en ; l'ordre ne doit pas décider de la langue affichée."""
    from quefaire.datatourisme import _first

    assert _first([
        {"@language": "en", "@value": "The mission of the network…"},
        {"@language": "fr", "@value": "Lisez ou empruntez des livres."},
    ]) == "Lisez ou empruntez des livres."
    # Dictionnaire de langues : comportement inchangé.
    assert _first({"en": ["Museum"], "fr": ["Musée"]}) == "Musée"
    # Aucune étiquette de langue : on prend ce qui vient, plutôt que rien.
    assert _first([{"@value": "Sans étiquette"}]) == "Sans étiquette"


def test_datatourisme_external_id_from_api_shape():
    """En mode API la fiche s'identifie par `uri`/`uuid`, pas par `@id`."""
    from quefaire.datatourisme import _to_place

    node = {
        "uri": "https://data.datatourisme.fr/42",
        "uuid": "42",
        "@type": ["Museum"],
        "rdfs:label": {"fr": ["Musée"]},
        "isLocatedAt": {"schema:geo": {"schema:latitude": "44.2", "schema:longitude": "2.7"}},
    }
    place = _to_place(node, "s", "2026-08-02")
    assert place.external_id == "https://data.datatourisme.fr/42"


def test_datatourisme_reads_image_and_credit():
    """Chaîne image de l'ontologie §8.9 : locator = l'URL, crédits à côté."""
    from quefaire.datatourisme import _image_of

    node = {
        "hasMainRepresentation": {
            "ebucore:hasRelatedResource": {
                "ebucore:locator": "https://photos.test/musee.jpg",
            },
            "hasCredits": {"rdfs:label": {"fr": ["Office de tourisme du Lévézou"]}},
        }
    }
    assert _image_of(node) == ("https://photos.test/musee.jpg", "Office de tourisme du Lévézou")
    # Pas de média, ou un média sans URL exploitable : aucune invention.
    assert _image_of({}) == (None, None)
    assert _image_of({"hasMainRepresentation": {"ebucore:locator": "http://pas-sur.test/x.jpg"}}) == (None, None)


def test_osm_image_only_from_commons():
    """Commons seulement : licence libre et page qui nomme l'auteur."""
    from quefaire.places import _image_of

    url, credit, page = _image_of({"wikimedia_commons": "File:Château de Tholet.jpg"})
    assert url.startswith("https://commons.wikimedia.org/wiki/Special:FilePath/Ch")
    assert credit == "Wikimedia Commons" and page.endswith("Ch%C3%A2teau_de_Tholet.jpg")
    # Le tag `image` est ignoré : URL d'un tiers, licence inconnue, et le
    # créditer à OpenStreetMap serait une attribution inventée.
    assert _image_of({"image": "https://exemple.test/p.jpg"}) == (None, None, None)
    assert _image_of({"wikimedia_commons": "Category:Rodez"}) == (None, None, None)
    assert _image_of({}) == (None, None, None)


def _zip_flux(fichiers: dict) -> bytes:
    """Archive ZIP en mémoire, comme celle que livre le diffuseur DATAtourisme."""
    import io
    import json as _json
    import zipfile

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as zf:
        for nom, contenu in fichiers.items():
            zf.writestr(nom, contenu if isinstance(contenu, str) else _json.dumps(contenu))
    return tampon.getvalue()


def test_flux_lit_une_archive_zip(monkeypatch):
    """Un flux DATAtourisme est livré en ZIP, pas en JSON nu.

    Le premier vrai passage en mode flux a échoué sur
    « Expecting value: line 1 column 1 » : le téléchargement réussissait et
    `.json()` recevait du binaire. La disposition interne de l'archive n'est pas
    devinée — on lit tout membre JSON et on garde ce qui se lit.
    """
    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    musee = {
        "@id": "https://data.datatourisme.fr/9",
        "@type": ["Museum"],
        "rdfs:label": {"fr": ["Musée du flux"]},
        "isLocatedAt": {"schema:geo": {"schema:latitude": 44.28, "schema:longitude": 2.74}},
    }
    archive = _zip_flux({
        "index.json": ["objects/9.json"],       # index de chemins : aucune fiche
        "objects/9.json": musee,                # un fichier PAR fiche, sans enveloppe
        "LISEZ-MOI.txt": "pas du json",         # membre non JSON : ignoré
    })

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/export.zip")
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp(None, content=archive)
    )
    found = dt.fetch(load_sector("pont-de-salars"))
    assert [p.name for p in found] == ["Musée du flux"]


def test_flux_manifeste_ni_converti_ni_muet(monkeypatch, caplog):
    """Le manifeste de l'archive sert de témoin de complétude, pas de fiche.

    L'archive du diffuseur contient un fichier par fiche PLUS un index de
    23 541 entrées `{label, lastUpdateDatatourisme, file}`. Ces entrées n'ont ni
    `@type` ni `@id` : les convertir échouait d'avance, et le nombre qu'elles
    annoncent est la seule chose qui permette de dire qu'une archive est amputée —
    sans quoi une perte de fiches ne se voit que des semaines plus tard, quand la
    rétention lâche.
    """
    import logging

    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    def fiche(n):
        return {
            "@id": f"https://data.datatourisme.fr/{n}",
            "@type": ["Museum"],
            "rdfs:label": {"fr": [f"Musée {n}"]},
            "isLocatedAt": {"schema:geo": {"schema:latitude": 44.28, "schema:longitude": 2.74}},
        }

    # Le manifeste annonce TROIS fiches, l'archive n'en contient que deux.
    archive = _zip_flux({
        "index.json": [
            {"label": "Musée 1", "lastUpdateDatatourisme": "2026-08-05", "file": "objects/1.json"},
            {"label": "Musée 2", "lastUpdateDatatourisme": "2026-08-05", "file": "objects/2.json"},
            {"label": "Musée 3", "lastUpdateDatatourisme": "2026-08-05", "file": "objects/3.json"},
        ],
        "objects/1.json": fiche(1),
        "objects/2.json": fiche(2),
    })

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/export.zip")
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp(None, content=archive)
    )
    with caplog.at_level(logging.WARNING):
        found = dt.fetch(load_sector("pont-de-salars"))

    # Les entrées d'index ne deviennent pas des activités.
    assert sorted(p.name for p in found) == ["Musée 1", "Musée 2"]
    manque = [r.getMessage() for r in caplog.records if "manifeste annonce" in r.getMessage()]
    assert manque, "un écart entre fiches annoncées et fiches lues doit être DIT"
    assert "3 fiches, 2 lues" in manque[0]


def test_flux_ne_tronque_pas_une_archive_volumineuse(monkeypatch, caplog):
    """Le garde-fou d'archive borne le PIC, pas le cumul décompressé.

    Le garde-fou d'origine coupait au-delà de 500 Mo **cumulés** : il a tronqué un
    flux parfaitement légitime (19 685 fiches), dont des centaines n'ont survécu
    au run que par la rétention. Le cumul ne mesurait rien de réel — les membres
    sont lus et libérés un par un. Ici, trois fiches dont le cumul dépasse
    largement la limite par membre doivent TOUTES passer, et seule celle qui
    excède `MAX_MEMBER_BYTES` à elle seule doit être écartée, avec un log.
    """
    import logging

    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    def fiche(n, bourre=0):
        return {
            "@id": f"https://data.datatourisme.fr/{n}",
            "@type": ["Museum"],
            "rdfs:label": {"fr": [f"Musée {n}"]},
            "isLocatedAt": {"schema:geo": {"schema:latitude": 44.28, "schema:longitude": 2.74}},
            "rdfs:comment": {"fr": ["x" * bourre]} if bourre else {},
        }

    monkeypatch.setattr(dt, "MAX_MEMBER_BYTES", 20_000)
    archive = _zip_flux({
        "objects/1.json": fiche(1, 15_000),   # gros mais admissible
        "objects/2.json": fiche(2, 15_000),   # le CUMUL dépasse : ne doit rien couper
        "objects/3.json": fiche(3, 30_000),   # trop gros à lui seul : écarté
    })

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/export.zip")
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp(None, content=archive)
    )
    with caplog.at_level(logging.WARNING):
        found = dt.fetch(load_sector("pont-de-salars"))

    assert sorted(p.name for p in found) == ["Musée 1", "Musée 2"]
    assert any("objects/3.json" in r.getMessage() for r in caplog.records), (
        "un membre écarté doit se DIRE : sinon la fiche passe pour absente du flux"
    )


def test_flux_accepte_graph_et_json_nu(monkeypatch):
    """Les autres emballages restent acceptés : le mode flux ne doit pas casser
    si le diffuseur change de format."""
    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    fiche = {
        "@id": "https://data.datatourisme.fr/10",
        "@type": ["Museum"],
        "rdfs:label": {"fr": ["Musée enveloppé"]},
        "isLocatedAt": {"schema:geo": {"schema:latitude": 44.28, "schema:longitude": 2.74}},
    }
    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/x")

    # a) ZIP contenant un seul gros JSON-LD sous @graph
    archive = _zip_flux({"flux.jsonld": {"@graph": [fiche]}})
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp(None, content=archive)
    )
    assert [p.name for p in dt.fetch(load_sector("pont-de-salars"))] == ["Musée enveloppé"]

    # b) JSON nu, sans archive (comportement d'origine)
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get", lambda *a, **k: _FakeResp({"@graph": [fiche]})
    )
    assert [p.name for p in dt.fetch(load_sector("pont-de-salars"))] == ["Musée enveloppé"]


def test_flux_paresseux_declenche_quand_meme_le_repli(monkeypatch):
    """Le parcours du flux est paresseux, le TÉLÉCHARGEMENT doit rester immédiat.

    Piège évité de justesse : dans un générateur pur, l'erreur HTTP ne survient
    qu'à la première itération — donc après le `try` de `fetch()`, et le repli sur
    l'API ne jouerait plus. Ce test laisse tourner le VRAI `_nodes_from_flux`,
    contrairement au test suivant qui le remplace.
    """
    import requests

    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/x")
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.delenv(dt.API_PARAMS_ENV, raising=False)
    monkeypatch.delenv(dt.API_FILTERS_ENV, raising=False)

    def http(url, **k):
        if "flux.test" in url:
            raise requests.HTTPError("504 Gateway Timeout")
        return _FakeResp({"objects": [{
            "uri": "https://data.datatourisme.fr/11",
            "@type": ["Museum"],
            "rdfs:label": {"fr": ["Musée de repli"]},
            "isLocatedAt": {"schema:geo": {"schema:latitude": "44.28", "schema:longitude": "2.74"}},
        }], "meta": {"next": None}})

    monkeypatch.setattr("quefaire.fetchers.base.http_get", http)
    assert [p.name for p in dt.fetch(load_sector("pont-de-salars"))] == ["Musée de repli"]


def test_flux_vide_declenche_le_repli(monkeypatch):
    """Une archive qui ne rend aucune fiche doit basculer sur l'API, pas publier
    un jeu vide. Le flot paresseux doit donc être testé AVANT d'être consommé."""
    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/vide.zip")
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.delenv(dt.API_PARAMS_ENV, raising=False)
    monkeypatch.delenv(dt.API_FILTERS_ENV, raising=False)

    vide = _zip_flux({"LISEZ-MOI.txt": "aucun json ici"})

    def http(url, **k):
        if "flux.test" in url:
            return _FakeResp(None, content=vide)
        return _FakeResp({"objects": [{
            "uri": "https://data.datatourisme.fr/12",
            "@type": ["Museum"],
            "rdfs:label": {"fr": ["Musée de repli"]},
            "isLocatedAt": {"schema:geo": {"schema:latitude": "44.28", "schema:longitude": "2.74"}},
        }], "meta": {"next": None}})

    monkeypatch.setattr("quefaire.fetchers.base.http_get", http)
    assert [p.name for p in dt.fetch(load_sector("pont-de-salars"))] == ["Musée de repli"]


def test_datatourisme_falls_back_to_api_when_flux_refused(monkeypatch):
    """Un flux dépublié rend 403 : la clé d'API doit prendre le relais.

    Sans ce repli, le run publiait un jeu OSM seul — amputé du tiers de ses
    fiches — sans autre signe qu'un warning noyé dans le log.
    """
    from quefaire import datatourisme as dt
    from quefaire.cli import load_sector

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/refuse")
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.delenv(dt.API_PARAMS_ENV, raising=False)
    monkeypatch.delenv(dt.API_FILTERS_ENV, raising=False)

    def flux_refuse(_url):
        raise RuntimeError("403 Client Error: Forbidden")

    appels = []

    def api(key, sector, filters=""):
        appels.append(key)
        return [{
            "uri": "https://data.datatourisme.fr/7",
            "@type": ["Museum"],
            "rdfs:label": {"fr": ["Musée de secours"]},
            "isLocatedAt": {"schema:geo": {"schema:latitude": "44.19", "schema:longitude": "2.68"}},
        }]

    monkeypatch.setattr(dt, "_nodes_from_flux", flux_refuse)
    monkeypatch.setattr(dt, "_nodes_from_api", api)

    found = dt.fetch(load_sector("pont-de-salars"))
    assert appels == ["K"]                       # le repli a bien été emprunté
    assert [p.name for p in found] == ["Musée de secours"]


def test_dedupe_after_merge_absorbs_retained_duplicates():
    """Une panne de fournisseur ne doit pas publier deux fois le même lieu.

    Les fiches d'un fournisseur muet survivent par la rétention de merge() ;
    elles échappent donc au dédoublonnage d'avant fusion, et se retrouvaient
    côte à côte avec la fiche OSM fraîche du même lieu.
    """
    from quefaire.models import Place
    from quefaire.places import dedupe_providers, merge

    retenue = Place(name="Cathédrale Notre-Dame de Rodez", category="patrimoine",
                    source_id="datatourisme", sector="s", external_id="dt/1",
                    lat=44.3496, lon=2.5751, description="Gothique méridional.",
                    tldr="Une flèche de 87 m.", providers=["datatourisme"],
                    tags=["dt:Church"], last_seen="2026-08-02")
    fraiche = Place(name="Cathédrale Notre-Dame de Rodez", category="patrimoine",
                    source_id="osm", sector="s", external_id="way/9",
                    lat=44.3497, lon=2.5752, opening_hours="Mo-Su 09:00-19:00",
                    providers=["osm"], tags=["osm:historic=church"])

    fusion = merge([retenue], [fraiche], today="2026-08-03")
    assert len(fusion) == 2  # la rétention les laisse côte à côte…
    [unique] = dedupe_providers(fusion)  # …le dédoublonnage les réunit
    assert unique.opening_hours == "Mo-Su 09:00-19:00"   # fait frais conservé
    assert unique.tldr == "Une flèche de 87 m."          # enrichissement conservé
    assert set(unique.providers) == {"osm", "datatourisme"}


def test_sports_centre_is_not_an_activity():
    """Un gymnase municipal n'est pas une sortie de week-end.

    `leisure=sports_centre` est le fourre-tout d'OSM pour les équipements
    sportifs : 112 fiches dont « Gymnase » cinq fois. Les vraies activités de
    loisir ont leurs propres tags, qui restent classés.
    """
    from quefaire.places import _category_of

    assert _category_of({"leisure": "sports_centre"}) is None
    for value in ("escape_game", "climbing", "horse_riding", "golf_course",
                  "bowling_alley", "adventure_park", "ice_rink"):
        assert _category_of({"leisure": value}) == "sport-loisir", value


def test_name_key_matches_hyphen_and_group_variants():
    """Trois doublons publiés du même château, tous dus à la clé de nom."""
    from quefaire.places import _name_key

    attendu = _name_key("Château de Brousse")
    # Trait d'union non coupé : le « le » de Brousse-le-Château échappait aux
    # mots vides, et « chateau » répété empêchait l'égalité.
    assert _name_key("Château de Brousse-le-Château") == attendu
    # Variante « groupes » de DATAtourisme : même lieu, même offre.
    assert _name_key("Château de Brousse (groupes)") == attendu
    # Autres écarts relevés dans le corpus.
    assert _name_key("Musée des Beaux Arts Denys-Puech") == _name_key("Musée des Beaux-Arts Denys-Puech")
    assert _name_key("Saint-Rome Plage") == _name_key("Saint-Rome-Plage")
    # Et ce qui doit RESTER distinct : deux lieux différents du même village.
    assert _name_key("Pont de Brousse-le-Château") != attendu
    assert _name_key("Village médiéval de Brousse-le-Château") != attendu


def test_casino_reste_au_catalogue_mais_pas_en_famille():
    """Un casino est une sortie, pas une sortie en famille.

    Le filtre « En famille » existait côté site mais était inopérant sur les
    activités permanentes : leur `data-audience` valait « tous » en dur, donc
    aucune n'était jamais écartée.
    """
    from quefaire.models import Place

    casino = Place(name="Casino de Cransac", category="ludotheque", source_id="datatourisme",
                   sector="s", tags=["dt:Casino"])
    ludo = Place(name="Ludothèque", category="ludotheque", source_id="osm",
                 sector="s", tags=["osm:amenity=toy_library"])

    # Il garde sa catégorie et sa place au catalogue…
    assert casino.category == "ludotheque"
    # …mais son public le sort des résultats « En famille ».
    assert casino.audience == ["adultes"]
    assert ludo.audience == ["tous"]

    # La règle porte sur le TAG : la catégorie « Ludothèque & jeux » ne suffirait
    # pas à distinguer les deux.
    assert Place(name="X", category="autre", source_id="osm", sector="s",
                 tags=["osm:amenity=casino"]).audience == ["adultes"]

    # Recalculé à la relecture : la règle s'applique aux fiches déjà publiées,
    # sans attendre une nouvelle découverte.
    relu = Place(**{**casino.to_dict(), "audience": ["tous"]})
    assert relu.audience == ["adultes"]


def test_place_stats_feed_the_city_portal(tmp_path):
    """Le portail annonçait les seuls événements temporaires — le plus petit
    et le plus volatil des deux chiffres. Il lui faut les activités."""
    import json

    from quefaire.export import _place_stats

    city = tmp_path / "cities" / "pont-de-salars"
    city.mkdir(parents=True)
    (city / "places.json").write_text(json.dumps([
        {"name": "A", "unusual": True, "quality": ["monument-historique"], "image_url": "https://x/1"},
        {"name": "B", "unusual": False, "quality": ["notoriete"]},
        {"name": "C", "quality": [], "image_url": "https://x/2"},
    ]), encoding="utf-8")

    stats = _place_stats("pont-de-salars", tmp_path)
    assert stats == {
        "place_count": 3,
        "unusual_count": 1,
        # « notoriete » (Wikipédia) n'est pas une valeur sûre : c'est une
        # notoriété, pas une distinction décernée par un tiers.
        "notable_count": 1,
        "photo_count": 2,
    }
    # Ville jamais découverte : des zéros, jamais une exception — le crawl ne
    # doit pas échouer parce que le cycle hebdomadaire n'a pas encore tourné.
    assert _place_stats("inconnue", tmp_path)["place_count"] == 0


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
            # §8.2 : labels et classements passent par hasReview, pas hasLabel.
            "hasReview": [{"rdfs:label": {"fr": ["Musée de France"]}}, "Qualité Tourisme"],
            "isLocatedAt": {
                "schema:geo": {"schema:latitude": "44.28", "schema:longitude": "2.73"},
                "schema:address": {"schema:addressLocality": "Pont-de-Salars",
                                   "schema:streetAddress": "3 rue du Moulin"},
                # §8.5 : les horaires vivent SOUS isLocatedAt.
                "schema:openingHoursSpecification": [{
                    "schema:dayOfWeek": ["Tuesday", "Wednesday"],
                    "schema:opens": "10:00:00", "schema:closes": "18:00:00",
                }],
            },
            "hasContact": {"foaf:homepage": ["https://musee-rouergue.fr"],
                           "schema:telephone": "0565000000"},
        },
        {
            "@id": "https://data.datatourisme.fr/2",
            "@type": ["PointOfInterest", "SportsAndLeisurePlace"],
            "rdfs:label": "Accrobranche du Lévézou",
            # Une description, comme 99 % des fiches réelles de DATAtourisme :
            # sans elle, `filter_relevant` l'écarterait à juste titre et ce test
            # ne mesurerait plus ce qu'il prétend (la panne d'OSM, pas le filtre).
            "hasDescription": [{"shortDescription": {"fr": ["Parcours dans les arbres."]}}],
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

    # « Accrobranche du Lévézou » ne porte que SportsAndLeisurePlace + le type
    # racine : elle reste reconnue. Une fiche sans type précis serait écartée.
    assert set(by_name) == {"Musée du Rouergue", "Accrobranche du Lévézou"}
    musee = by_name["Musée du Rouergue"]
    assert musee.category == "musee"           # Museum gagne sur PointOfInterest
    assert musee.commune == "Pont-de-Salars"
    assert musee.url == "https://musee-rouergue.fr"
    assert "Outils et costumes" in musee.description
    # Labels reconnus quelle que soit leur forme (objet imbriqué ou chaîne nue).
    assert set(musee.quality) == {"musee-de-france", "qualite-tourisme"}
    # Horaires lus sous isLocatedAt et rendus lisibles en français.
    assert musee.opening_hours == "mar, mer 10:00-18:00"
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


def test_dedupe_providers_ne_depend_pas_de_l_ordre_d_arrivee():
    """Le rapprochement ne doit pas dépendre de l'ordre dans lequel arrivent les
    fiches — sinon un diff de données générées cesse de vouloir dire quelque chose.

    Le cas qui l'a révélé : trois homonymes en CHAÎNE, A-B et B-C sous le seuil de
    rapprochement, A-C au-dessus. Comme chaque fiche n'est comparée qu'à la TÊTE de
    groupe, l'arrivée de B en premier absorbait A et C, alors que l'arrivée de A en
    premier laissait C dehors — 1 fiche ou 2 selon Overpass, qui ne garantit pas
    l'ordre de ses éléments. Mesuré sur les six permutations : 1 pour `bac`/`bca`,
    2 pour les quatre autres. En production, deux runs à données identiques ont
    publié 2747 puis 2745 activités.

    Le rapprochement reste volontairement comparé à la tête et non à tous les
    membres : la fermeture transitive collerait des lieux distincts de proche en
    proche, ce que ce projet a déjà refusé pour l'URL partagée. Ce qui est corrigé
    ici est l'ARBITRAIRE, pas le périmètre du rapprochement.
    """
    import itertools

    from quefaire.models import Place
    from quefaire.places import SAME_PLACE_KM, dedupe_providers

    pas = SAME_PLACE_KM / 111.0 * 0.9   # 90 % du seuil, exprimé en degrés de latitude

    def fiche(ident, rang):
        return Place(name="Chapelle Saint-Roch", category="patrimoine", source_id="osm",
                     sector="s", external_id=ident, lat=44.0 + rang * pas, lon=2.0,
                     tags=["osm:historic=monument"], providers=["osm"])

    comptes = set()
    for ordre in itertools.permutations([("a", 0), ("b", 1), ("c", 2)]):
        comptes.add(len(dedupe_providers([fiche(i, r) for i, r in ordre])))
    assert len(comptes) == 1, f"résultat dépendant de l'ordre : {sorted(comptes)}"


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


def test_datatourisme_respects_quota_guard(monkeypatch):
    """Un 429 est rejoué en respectant Retry-After, pas abandonné ni martelé."""
    import requests

    from quefaire import datatourisme as dt

    monkeypatch.setattr(dt, "MIN_INTERVAL_S", 0)  # pas d'attente réelle en test
    monkeypatch.setattr(dt, "_requests_made", 0)
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            resp = requests.Response()
            resp.status_code = 429
            resp.headers["Retry-After"] = "7"
            raise requests.HTTPError("429", response=resp)
        return _FakeResp(DT_SAMPLE)

    monkeypatch.setattr("quefaire.fetchers.base.http_get", flaky)
    assert dt._request("https://flux.test/x").json() == DT_SAMPLE
    assert calls["n"] == 2       # rejoué une fois
    assert 7 in slept            # Retry-After honoré plutôt qu'un délai arbitraire


def test_datatourisme_stops_before_quota(monkeypatch):
    """Le plafond de sécurité coupe une boucle anormale avant le quota réel."""
    from quefaire import datatourisme as dt

    monkeypatch.setattr(dt, "_requests_made", dt.MAX_REQUESTS_PER_HOUR)
    with pytest.raises(RuntimeError, match="plafond de sécurité"):
        dt._request("https://flux.test/x")


def test_datatourisme_api_follows_next_url(monkeypatch):
    """Mode API : on suit meta.next (méthode recommandée), pas un compteur de pages."""
    from quefaire import datatourisme as dt

    monkeypatch.delenv(dt.FLUX_ENV, raising=False)
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.setattr(dt, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(dt, "_requests_made", 0)

    page1 = {"objects": DT_SAMPLE["@graph"][:2],
             "meta": {"total": 4, "total_pages": 2,
                      "next": "https://api.datatourisme.fr/v1/placeOfInterest?page=2"}}
    page2 = {"objects": DT_SAMPLE["@graph"][2:], "meta": {"next": None}}
    seen: list[tuple[str, dict]] = []

    def fake(url, **k):
        seen.append((url, k.get("headers") or {}))
        return _FakeResp(page1 if len(seen) == 1 else page2)

    monkeypatch.setattr("quefaire.fetchers.base.http_get", fake)
    found = dt.fetch(load_sector("pont-de-salars"))

    assert len(seen) == 2                          # deux pages, puis arrêt sur next=None
    # Clé en en-tête (méthode recommandée) et non dans l'URL : elle ne fuite pas
    # dans les journaux de requêtes.
    assert seen[0][1].get("X-API-Key") == "K"
    assert "api_key" not in seen[0][0]
    assert seen[1][0].endswith("page=2")           # l'URL next est suivie telle quelle
    assert {p.name for p in found} == {"Musée du Rouergue", "Accrobranche du Lévézou"}


def test_datatourisme_api_extra_filters(monkeypatch):
    """Les filtres serveur sont configurables sans toucher au code."""
    from quefaire import datatourisme as dt

    monkeypatch.delenv(dt.FLUX_ENV, raising=False)
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.delenv(dt.API_PARAMS_ENV, raising=False)
    monkeypatch.delenv(dt.API_FILTERS_ENV, raising=False)
    monkeypatch.setattr(dt, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(dt, "_requests_made", 0)
    seen: list[str] = []
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get",
        lambda url, **k: (seen.append(url), _FakeResp({"objects": [], "meta": {}}))[1],
    )
    # Le périmètre est DÉRIVÉ de l'épicentre : ni liste de communes, ni code
    # départemental à maintenir. 60 min de voiture ≈ 48 km.
    sector = load_sector("pont-de-salars")
    dt.fetch(sector)
    assert "geo_distance=44.2789%2C2.73%2C48km" in seen[0]
    # Filtre de type côté serveur : on ne rapatrie pas ce qu'on jetterait.
    assert "filters=type%5Bin%5D=" in seen[0] or "filters=type%5Bin%5D%3D" in seen[0]
    assert "Museum" in seen[0]
    assert f"page_size={dt.MAX_PAGE_SIZE}" in seen[0]       # 250 = maximum autorisé
    assert "fields=" in seen[0]                             # réponse allégée
    assert dt.MAX_PAGE_SIZE == 250                          # plafond imposé par l'API

    # Rayon plus court → cercle plus petit, sans rien changer d'autre.
    seen.clear()
    sector.radius_minutes = 30
    dt.fetch(sector)
    assert "%2C18km" in seen[0]


def test_datatourisme_type_mapping_uses_real_ontology_names():
    """Noms de types relevés dans l'énumération `type` de la doc de l'API."""
    from quefaire.datatourisme import _category_of

    assert _category_of(["PointOfInterest", "Museum"]) == "musee"
    assert _category_of(["PointOfInterest", "ParkAndGarden"]) == "nature"
    assert _category_of(["PointOfInterest", "PointOfView"]) == "nature"
    assert _category_of(["PointOfInterest", "IceSkatingRink"]) == "sport-loisir"
    assert _category_of(["PointOfInterest", "MegalithDolmenMenhir"]) == "patrimoine"
    assert _category_of(["PointOfInterest", "Producer"]) == "ferme"
    # PAS de repli générique : PlaceOfInterest/PointOfInterest sont les types
    # RACINE que porte CHAQUE fiche, hôtels et restaurants compris. Un repli sur
    # eux classait tout le territoire en « visite » (6431/6431 au premier run).
    assert _category_of(["PlaceOfInterest"]) is None
    assert _category_of(["PointOfInterest", "Hotel"]) is None
    assert _category_of(["PointOfInterest", "Restaurant"]) is None
    # …mais un type précis reste reconnu même accompagné du type racine.
    assert _category_of(["PointOfInterest", "PlaceOfInterest", "Museum"]) == "musee"


def test_merge_un_refus_n_est_pas_une_absence():
    """Une fiche refusée par la sweep ne bénéficie PAS du sursis d'absence.

    L'exclusion des bibliothèques et des bars à vin n'avait rien changé au
    catalogue de Villemoirieu : 3775 activités avant, 3775 après,
    `dt:SportsAndLeisurePlace` toujours à 1440. Les fiches quittaient bien la
    sweep, et la rétention les reprenait aussitôt pour quatorze jours.

    `_tag_still_mapped()` ne pouvait pas les rattraper : il rejoue la règle sur le
    seul tag de provenance — `dt:SportsAndLeisurePlace`, qui reste parfaitement
    valide — alors que la décision d'origine voyait TOUS les types de la fiche. Un
    rejeu moins informé que la décision ne peut pas la reproduire. D'où ce chemin
    explicite, et ce test : quatrième variante du même défaut, il ne doit pas y en
    avoir une cinquième.
    """
    from quefaire.models import Place
    from quefaire.places import merge

    def fiche(ident, nom):
        return Place(name=nom, category="sport-loisir", source_id="datatourisme",
                     sector="s", external_id=ident, lat=45.7, lon=5.2,
                     tags=["dt:SportsAndLeisurePlace"], providers=["datatourisme"],
                     first_seen="2026-07-01", last_seen="2026-08-04")

    biblio = fiche("https://data.datatourisme.fr/biblio", "Bibliothèque de Crémieu")
    accro = fiche("https://data.datatourisme.fr/accro", "Accrobranche de l'Isle")

    # Les deux disparaissent de la sweep. L'une est REFUSÉE, l'autre simplement
    # absente : la seconde garde son sursis, la première non.
    garde = merge([biblio, accro], [], today="2026-08-05",
                  refuses={"https://data.datatourisme.fr/biblio"})
    assert [p.name for p in garde] == ["Accrobranche de l'Isle"]

    # Sans l'ensemble des refus, la rétention garde tout — c'est le comportement
    # d'origine, et c'est bien lui qui rendait l'exclusion inopérante.
    assert len(merge([biblio, accro], [], today="2026-08-05")) == 2


def test_datatourisme_type_disqualifiant_bat_toutes_les_regles():
    """« Non classé » et « exclu » ne sont PAS la même chose.

    Une fiche porte plusieurs types et passe dès qu'un seul est classé. Retirer
    `Library` des règles n'a donc rien empêché : à Villemoirieu, 261 bibliothèques
    entrent comme `SportsAndLeisurePlace`, et 211 bars à vin de même — alors que
    `FoodEstablishment` est le type le plus massivement rejeté du flux. C'est le
    motif des 56 monuments aux morts, transposé aux types : une exclusion qu'une
    seconde voie contourne.

    Un type disqualifiant doit donc battre TOUTES les règles, quel que soit le
    reste de la fiche.
    """
    from quefaire.datatourisme import _category_of

    # Le cas réel : la bibliothèque est aussi rangée en équipement de loisir.
    assert _category_of(["PlaceOfInterest", "SportsAndLeisurePlace", "Library"]) is None
    assert _category_of(["PointOfInterest", "SportsAndLeisurePlace", "BistroOrWineBar"]) is None
    # L'ordre des types dans la fiche ne doit rien changer.
    assert _category_of(["Library", "SportsAndLeisurePlace"]) is None
    # Et l'exclusion reste ÉTROITE : ce qui n'est pas disqualifiant passe toujours.
    assert _category_of(["PlaceOfInterest", "SportsAndLeisurePlace"]) == "sport-loisir"
    # `Winery` n'est délibérément PAS disqualifiant : une cave qui fait déguster
    # est une visite légitime, et le type arrive sur les mêmes fiches que le bar.
    assert _category_of(["PlaceOfInterest", "WineCellar", "Winery"]) == "ferme"
    # `LocalBusiness` non plus : 98 musées sur 98 le portent, il ne sépare rien.
    assert _category_of(["PlaceOfInterest", "Museum", "schema:LocalBusiness"]) == "musee"


def test_datatourisme_api_caps_pagination(monkeypatch, caplog):
    """Un catalogue non filtré est tronqué — mais bruyamment, jamais en silence."""
    from quefaire import datatourisme as dt

    monkeypatch.delenv(dt.FLUX_ENV, raising=False)
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.delenv(dt.API_PARAMS_ENV, raising=False)
    monkeypatch.setattr(dt, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(dt, "MAX_PAGES", 3)
    monkeypatch.setattr(dt, "_requests_made", 0)
    calls = {"n": 0}

    def endless(url, **k):
        calls["n"] += 1
        return _FakeResp({"objects": [], "meta": {"next": "https://api.datatourisme.fr/v1/catalog?p=x"}})

    monkeypatch.setattr("quefaire.fetchers.base.http_get", endless)
    with caplog.at_level("WARNING"):
        dt.fetch(load_sector("pont-de-salars"))
    assert calls["n"] == 3                                  # plafonné
    assert "TRONQUÉ" in caplog.text                          # et signalé


def test_datatourisme_flux_preferred_over_api(monkeypatch):
    """Le flux coûte une requête là où l'API en coûte une par page : il prime."""
    from quefaire import datatourisme as dt

    monkeypatch.setenv(dt.FLUX_ENV, "https://flux.test/x")
    monkeypatch.setenv(dt.API_KEY_ENV, "K")
    monkeypatch.setattr(dt, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(dt, "_requests_made", 0)
    seen: list[str] = []
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get",
        lambda url, **k: (seen.append(url), _FakeResp(DT_SAMPLE))[1],
    )
    dt.fetch(load_sector("pont-de-salars"))
    assert seen == ["https://flux.test/x"]   # l'API n'est pas sollicitée


def test_cache_partitioned_per_cycle(tmp_path, monkeypatch):
    """Crawl et découverte d'activités ne doivent pas s'évincer mutuellement.

    L'élagage ne garde que les clés vues pendant le run : deux cycles partageant
    le même fichier s'effaceraient l'un l'autre à chaque passage (vécu au
    premier run réel — 40 entrées de crawl remplacées par 7157 d'activités).
    """
    import quefaire.cache as c

    monkeypatch.setattr(c, "CACHE_DIR", tmp_path)
    cache = c._ContentCache()

    cache.bind("ville", "content")          # cycle crawl
    cache.put("extract:page", ["A"])
    cache.save()
    cache.bind("ville", "places")           # cycle découverte d'activités
    cache.put("place:musee", "phrase")
    cache.save()

    crawl = json.loads((tmp_path / "ville" / "content.json").read_text(encoding="utf-8"))
    places = json.loads((tmp_path / "ville" / "places.json").read_text(encoding="utf-8"))
    assert crawl == {"extract:page": ["A"]}   # survit au cycle activités
    assert places == {"place:musee": "phrase"}


def test_overpass_falls_back_to_mirror(monkeypatch):
    """Un 504 de l'instance publique bascule sur un miroir, sans perdre le run."""
    import requests

    from quefaire import places

    tried: list[str] = []

    def flaky(url, **k):
        tried.append(url)
        if len(tried) == 1:
            resp = requests.Response()
            resp.status_code = 504
            raise requests.HTTPError("504", response=resp)
        return _FakeResp(OVERPASS_SAMPLE)

    monkeypatch.setattr("quefaire.fetchers.base.http_get", flaky)
    found = places.fetch_osm(load_sector("pont-de-salars"))
    assert len(tried) == 2                       # bascule sur le miroir suivant
    assert tried[0] != tried[1]
    assert any(p.name == "Musée du Rouergue" for p in found)


def test_overpass_raises_when_all_mirrors_fail(monkeypatch):
    import requests

    from quefaire import places

    def dead(url, **k):
        resp = requests.Response()
        resp.status_code = 504
        raise requests.HTTPError("504", response=resp)

    monkeypatch.setattr("quefaire.fetchers.base.http_get", dead)
    with pytest.raises(RuntimeError, match="Overpass"):
        places.fetch_osm(load_sector("pont-de-salars"))


def test_discover_places_survives_osm_outage(monkeypatch, tmp_path):
    """Overpass en panne ne doit pas priver le secteur de DATAtourisme."""
    from quefaire import cli, datatourisme, places

    monkeypatch.setenv(datatourisme.API_KEY_ENV, "K")
    monkeypatch.delenv(datatourisme.FLUX_ENV, raising=False)
    monkeypatch.setattr(datatourisme, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(datatourisme, "_requests_made", 0)
    monkeypatch.setattr(places, "fetch_osm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("504")))
    monkeypatch.setattr(
        "quefaire.fetchers.base.http_get",
        lambda url, **k: _FakeResp({"objects": DT_SAMPLE["@graph"], "meta": {"next": None}}),
    )
    _reset_cache()

    assert cli.discover_places("pont-de-salars", tmp_path, use_llm=False, use_ratings=False) == 0
    saved = places.load("pont-de-salars", tmp_path)
    assert {p.name for p in saved} == {"Musée du Rouergue", "Accrobranche du Lévézou"}


def test_discover_places_keeps_file_when_all_providers_down(monkeypatch, tmp_path):
    """Deux fournisseurs muets = on ne touche à rien, jamais de secteur vidé."""
    from quefaire import cli, datatourisme, places
    from quefaire.models import Place

    existing = [Place(name="Musée", category="musee", source_id="osm",
                      sector="pont-de-salars", external_id="node/1", lat=44.2, lon=2.7)]
    places.save(existing, "pont-de-salars", tmp_path)

    monkeypatch.delenv(datatourisme.API_KEY_ENV, raising=False)
    monkeypatch.delenv(datatourisme.FLUX_ENV, raising=False)
    monkeypatch.setattr(places, "fetch_osm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("504")))

    assert cli.discover_places("pont-de-salars", tmp_path, use_llm=False, use_ratings=False) == 1
    assert [p.name for p in places.load("pont-de-salars", tmp_path)] == ["Musée"]


def test_unusual_requires_llm_confirmation():
    """L'heuristique PROPOSE, seul le LLM DISPOSE.

    Au run réel, l'heuristique taguait 615 activités « insolites » (23 % du
    corpus) dont 585 que le LLM n'avait jamais examinées — une affirmation
    affichée au visiteur sans examen.
    """
    from quefaire import places

    tags = {"name": "La Girafe de ferraille", "tourism": "artwork"}
    assert places._looks_unusual(tags, "visite") is True   # présomption
    place = places._element_to_place(
        {"type": "node", "id": 1, "lat": 44.28, "lon": 2.73, "tags": tags}, "s", "2026-08-02"
    )
    assert place.unusual_hint is True     # candidate à l'examen
    assert place.unusual is False         # …mais rien n'est affirmé avant le LLM


def test_merge_keeps_llm_verdict_not_heuristic():
    from quefaire.models import Place
    from quefaire.places import merge

    old = Place(name="X", category="visite", source_id="osm", sector="s",
                external_id="node/1", tldr="phrase", unusual=True, unusual_hint=True)
    fresh = Place(name="X", category="visite", source_id="osm", sector="s",
                  external_id="node/1", unusual_hint=True)
    [out] = merge([old], [fresh], today="2026-08-02")
    assert out.unusual is True        # verdict LLM conservé
    assert out.unusual_hint is True


def test_presentation_queue_follows_display_order(monkeypatch):
    """La file LLM suit l'ordre d'AFFICHAGE, pas les présomptions d'insolite.

    Vécu au run réel : la file privilégiait 594 présomptions dont 533 sans
    description — le LLM n'avait rien à lire et rendait du vide. 26 phrases
    utiles pour 400 tentatives.
    """
    from quefaire import places
    from quefaire.models import Place

    riche = Place(name="Château classé", category="patrimoine", source_id="osm",
                  sector="s", external_id="node/1", description="Un donjon du XIIe.",
                  url="https://x.fr", opening_hours="Tu-Su 10:00-18:00",
                  quality=["monument-historique"])
    pauvre = Place(name="Aire de pique-nique", category="nature", source_id="osm",
                   sector="s", external_id="node/2", unusual_hint=True)
    assert places.display_score(riche) > places.display_score(pauvre)

    monkeypatch.setattr(places, "PRESENT_MAX_PER_RUN", 1)
    _reset_cache()
    soumis: list[str] = []

    class _Chain:
        def available(self): return True
        def healthy(self): return True
        def run(self, prompt):
            soumis.append(prompt)
            raise RuntimeError("stop")  # on n'observe que la sélection

    monkeypatch.setattr("quefaire.llm.clarify_chain", lambda: _Chain())
    places.present([pauvre, riche])
    assert "Château classé" in soumis[0]        # la fiche documentée passe d'abord
    assert "Aire de pique-nique" not in soumis[0]


def test_le_site_ne_plafonne_plus_l_affichage():
    """Le site doit afficher TOUT le catalogue, pas une présélection.

    Ce test remplace un miroir devenu faux : il vérifiait que le budget de
    présentation du pipeline valait `MAX_RENDERED` côté site. Ce plafond de 300
    tuiles n'était pas un filtre de pertinence mais une limite de poids de page,
    et il écartait 1900 activités de la RECHERCHE. Il a été supprimé — ce qui doit
    trier, ce sont les préférences du visiteur.

    On pin donc l'invariant qui reste : `rankPlaces` ordonne sans tronquer.
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "site/src/lib/places.js").read_text(encoding="utf-8")
    assert "MAX_RENDERED" not in js, "le plafond d'affichage est revenu"
    assert ".slice(" not in js, "rankPlaces tronque à nouveau le catalogue"


def test_display_score_is_intrinsic_not_circular():
    """Le score ne doit dépendre d'AUCUN enrichissement LLM.

    Sinon le classement est circulaire : présenter une fiche la fait monter et
    en déloge une autre, restée sans phrase. Mesuré au run #6 — 385 phrases
    payées jamais affichées, 74 activités affichées sans phrase.
    """
    from quefaire.models import Place
    from quefaire.places import display_score

    nu = Place(name="X", category="musee", source_id="osm", sector="s",
               external_id="node/1", url="https://x.fr", description="d")
    enrichi = Place(name="X", category="musee", source_id="osm", sector="s",
                    external_id="node/1", url="https://x.fr", description="d",
                    tldr="Une phrase.", unusual=True)
    assert display_score(nu) == display_score(enrichi)


def test_presentation_covers_the_displayed_set():
    """La file de présentation couvre exactement l'ensemble affiché."""
    from quefaire.models import Place
    from quefaire.places import display_order_key

    # 5 fiches documentées (affichées), 5 nues (hors écran).
    riches = [Place(name=f"Musée {i}", category="musee", source_id="osm", sector="s",
                    external_id=f"node/{i}", url="https://x.fr", description="d",
                    quality=["monument-historique"]) for i in range(5)]
    nues = [Place(name=f"Aire {i}", category="nature", source_id="osm", sector="s",
                  external_id=f"node/1{i}") for i in range(5)]
    ordre = sorted(riches + nues, key=display_order_key)
    affichees = {p.id for p in ordre[:5]}
    file_ = [p for p in ordre if not p.tldr][:5]
    assert affichees == {p.id for p in file_}   # on présente ce qui est affiché


def test_classification_provenance_is_recorded():
    """Chaque fiche garde le tag/type qui a déclenché son classement.

    Sans cette traçabilité, une catégorie anormalement grosse ne dit pas QUEL
    tag la gonfle — on ne peut qu'élaguer au jugé.
    """
    from quefaire import datatourisme, places

    osm = places._element_to_place(
        {"type": "node", "id": 1, "lat": 44.28, "lon": 2.73,
         "tags": {"name": "Piscine", "leisure": "swimming_pool"}}, "s", "2026-08-02")
    assert osm.tags == ["osm:leisure=swimming_pool"]

    dt = datatourisme._to_place(DT_SAMPLE["@graph"][1], "s", "2026-08-02")
    assert dt.tags == ["dt:SportsAndLeisurePlace"]


def test_report_breaks_down_categories_by_raw_type():
    from quefaire.datatourisme import report
    from quefaire.models import Place

    places_ = [
        Place(name="A", category="sport-loisir", source_id="datatourisme", sector="s",
              tags=["dt:LeisureSportActivityProvider"]),
        Place(name="B", category="sport-loisir", source_id="datatourisme", sector="s",
              tags=["dt:LeisureSportActivityProvider"]),
        Place(name="C", category="sport-loisir", source_id="datatourisme", sector="s",
              tags=["dt:GolfCourse"]),
        Place(name="D", category="musee", source_id="datatourisme", sector="s",
              tags=["dt:Museum"]),
    ]
    r = report(places_)
    # La catégorie la plus grosse d'abord, et le type qui la gonfle identifié.
    assert list(r["types_bruts"]) == ["sport-loisir", "musee"]
    assert r["types_bruts"]["sport-loisir"]["LeisureSportActivityProvider"] == 2


def test_provider_is_not_a_place():
    """Un PRESTATAIRE d'activités relève de sa propre catégorie, pas d'un lieu.

    Mesuré : `LeisureSportActivityProvider` pesait 597 fiches, soit 74 % de
    « sport & loisirs » — « Grimpe d'arbres », « Balade numérique », « séances
    de bien-être ». Du vrai contenu, mais d'une autre nature qu'une adresse.
    """
    from quefaire.datatourisme import _category_of
    from quefaire.models import PLACE_CATEGORIES

    assert "prestation" in PLACE_CATEGORIES
    assert _category_of(["PointOfInterest", "LeisureSportActivityProvider"]) == "prestation"
    # Un vrai lieu de sport reste un lieu.
    assert _category_of(["PointOfInterest", "SportsAndLeisurePlace"]) == "sport-loisir"
    assert _category_of(["PointOfInterest", "GolfCourse"]) == "sport-loisir"


def test_noise_types_are_dropped():
    """Bibliothèques de village et monuments aux morts : mesurés sans valeur."""
    from quefaire.datatourisme import _category_of
    from quefaire.places import _category_of as osm_category

    assert _category_of(["PointOfInterest", "Library"]) is None
    assert osm_category({"historic": "memorial", "name": "10 août 1944"}) is None
    assert osm_category({"historic": "castle", "name": "Château"}) == "patrimoine"
