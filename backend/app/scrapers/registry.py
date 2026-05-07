from app.scrapers.base import DealerScraper
from app.scrapers.nordiskguld import NordiskGuldScraper
from app.scrapers.nyfortuna import NyfortunaScraper
from app.scrapers.plaza import PlazaScraper
from app.scrapers.seroguld import SeroGuldScraper
from app.scrapers.tavex import TavexScraper
from app.scrapers.vitusguld import VitusGuldScraper

ALL_SCRAPERS: list[DealerScraper] = [
    TavexScraper(),
    VitusGuldScraper(),
    PlazaScraper(),
    NordiskGuldScraper(),
    SeroGuldScraper(),
    NyfortunaScraper(),
]
