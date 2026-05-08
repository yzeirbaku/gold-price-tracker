from app.scrapers.base import DealerScraper
from app.scrapers.janjorgensen import JanJorgensenScraper
from app.scrapers.janjorgensen_coins import JanJorgensenCoinsScraper
from app.scrapers.nordiskguld import NordiskGuldScraper
from app.scrapers.nordiskguld_coins import NordiskGuldCoinsScraper
from app.scrapers.nyfortuna import NyfortunaScraper
from app.scrapers.nyfortuna_coins import NyfortunaCoinsScraper
from app.scrapers.plaza import PlazaScraper
from app.scrapers.plaza_coins import PlazaCoinsScraper
from app.scrapers.seroguld import SeroGuldScraper
from app.scrapers.seroguld_coins import SeroGuldCoinsScraper
from app.scrapers.tavex import TavexScraper
from app.scrapers.tavex_coins import TavexCoinsScraper
from app.scrapers.vitusguld import VitusGuldScraper
from app.scrapers.vitusguld_coins import VitusGuldCoinsScraper

ALL_SCRAPERS: list[DealerScraper] = [
    TavexScraper(),
    VitusGuldScraper(),
    PlazaScraper(),
    NordiskGuldScraper(),
    SeroGuldScraper(),
    NyfortunaScraper(),
    JanJorgensenScraper(),
]

ALL_COIN_SCRAPERS = [
    TavexCoinsScraper(),
    VitusGuldCoinsScraper(),
    PlazaCoinsScraper(),
    NordiskGuldCoinsScraper(),
    SeroGuldCoinsScraper(),
    NyfortunaCoinsScraper(),
    JanJorgensenCoinsScraper(),
]
