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
    return Event(title=title, start=start, source_id="t", sector="isere", commune=commune, **kw)


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
    ev = geocode(make(commune="grenoble"), "isere")
    assert ev.commune == "Grenoble"
    assert abs(ev.lat - 45.1885) < 0.01


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

    out = yaml.safe_load(cli.discover_openagenda("isere", ["Grenoble", "Vienne"], strict=False))
    uids = [e["url"] for e in out]
    assert sorted(uids) == [1, 2, 3]          # dédupliqué par UID
    assert out[0]["url"] == 1                  # l'agenda officiel sort en premier
    assert uids == [1, 2, 3]                   # puis tri par événements à venir (3 > 0)
    assert "Grenoble, Vienne" in out[0]["comment"]
    assert "12 événements à venir" in out[0]["comment"]
    assert all(e["enabled"] is False for e in out)  # validation humaine requise

    strict = yaml.safe_load(cli.discover_openagenda("isere", ["Grenoble", "Vienne"], strict=True))
    assert [e["url"] for e in strict] == [1, 3]  # strict : titre doit citer la commune


def test_social_fetcher_skips_without_config(monkeypatch, caplog):
    from quefaire.fetchers.social import SocialFetcher
    from quefaire.models import Source

    monkeypatch.delenv("RSSBRIDGE_URL", raising=False)
    src = Source(id="fb-test", name="t", type="facebook", url="mairie")
    assert SocialFetcher("facebook").fetch(src, "isere") == []


def test_nord_isere_communes_geocoded():
    for commune in ("Crémieu", "Morestel", "Saint-Chef", "La Verpillière", "Tignieu-Jameyzieu"):
        ev = geocode(make(commune=commune), "isere")
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
    events = html_llm.extract_events_llm("texte", src, "isere", "https://ot-ville.fr/agenda/")
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

    e1 = html_llm.extract_events_llm("même texte", src, "isere", "https://ex.fr/a/")
    e2 = html_llm.extract_events_llm("même texte", src, "isere", "https://ex.fr/a/")
    assert calls["n"] == 1  # 2ᵉ extraction servie par le cache
    assert [e.id for e in e1] == [e.id for e in e2]

    html_llm.extract_events_llm("texte modifié", src, "isere", "https://ex.fr/a/")
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
    sector = load_sector("isere")
    events = [enrich(geocode(e, "isere")) for e in demo_events()]
    meta = export(sector, dedupe(events), tmp_path)
    data = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert meta["event_count"] == len(data) > 20
    assert all(e["start"] >= date.today().isoformat()[:4] for e in data)
    # Tous les événements démo doivent être géocodés (communes connues du CSV).
    assert all(e["lat"] is not None for e in data)
