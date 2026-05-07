from app.scrapers.base import DealerScraper
from app.scrapers.plaza import PlazaScraper
from app.scrapers.tavex import TavexScraper
from app.scrapers.vitusguld import VitusGuldScraper

ALL_SCRAPERS: list[DealerScraper] = [
    TavexScraper(),
    VitusGuldScraper(),
    PlazaScraper(),
]
