from app.scrapers.base import DealerScraper
from app.scrapers.tavex import TavexScraper

ALL_SCRAPERS: list[DealerScraper] = [
    TavexScraper(),
]
