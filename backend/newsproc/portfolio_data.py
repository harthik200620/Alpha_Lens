"""
Static lookup tables for the portfolio assistant's free-text ticker detection.
Pure data extracted verbatim from app.py:
  * COMMON_EXTERNAL_STOCK_ALIASES — colloquial name -> ticker aliases
  * GENERIC_STOCK_NAME_WORDS       — generic words that are NOT a company
  * OUT_OF_SCOPE_TOPIC_WORDS       — topics the assistant should decline

app.py imports these back; behaviour is identical.
"""


COMMON_EXTERNAL_STOCK_ALIASES = {
    "tesla": "TSLA",
    "tsla": "TSLA",
    "apple": "AAPL",
    "aapl": "AAPL",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "googl": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "amazon": "AMZN",
    "amzn": "AMZN",
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
}

GENERIC_STOCK_NAME_WORDS = {
    "limited", "ltd", "industries", "industry", "india", "indian", "company",
    "corporation", "corp", "bank", "finance", "financial", "services",
    "service", "group", "holdings", "holding", "enterprise", "enterprises",
}

OUT_OF_SCOPE_TOPIC_WORDS = {
    "weather", "rain", "temperature", "cricket", "football", "movie",
    "movies", "song", "songs", "recipe", "travel", "hotel", "politics",
    "election", "celebrity", "astrology", "horoscope",
}
