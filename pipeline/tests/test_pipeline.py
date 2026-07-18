"""Tests du cœur du pipeline (sans réseau, sans LLM)."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

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


def test_demo_and_export_roundtrip(tmp_path):
    sector = load_sector("isere")
    events = [enrich(geocode(e, "isere")) for e in demo_events()]
    meta = export(sector, dedupe(events), tmp_path)
    data = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert meta["event_count"] == len(data) > 20
    assert all(e["start"] >= date.today().isoformat()[:4] for e in data)
    # Tous les événements démo doivent être géocodés (communes connues du CSV).
    assert all(e["lat"] is not None for e in data)
