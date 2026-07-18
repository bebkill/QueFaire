from ..models import Event, Source
from .html_llm import HtmlLlmFetcher
from .ical import IcalFetcher
from .openagenda import OpenAgendaFetcher
from .rss import RssFetcher
from .social import SocialFetcher

FETCHERS = {
    "rss": RssFetcher(),
    "ical": IcalFetcher(),
    "openagenda": OpenAgendaFetcher(),
    "html": HtmlLlmFetcher(),
    "facebook": SocialFetcher("facebook"),
    "instagram": SocialFetcher("instagram"),
}


def fetch_source(source: Source, sector_id: str) -> list[Event]:
    fetcher = FETCHERS.get(source.type)
    if fetcher is None:
        raise ValueError(f"Type de source inconnu : {source.type} ({source.id})")
    return fetcher.fetch(source, sector_id)
