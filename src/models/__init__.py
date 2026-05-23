from .screener import ScreenerResponse
from .trendlyne import TrendlyneResponse
from .marketsmithindia import MarketSmithIndiaResponse

# Mapping of data source names to their corresponding response models
SOURCE_MODELS = {
    "screener": ScreenerResponse,
    "trendlyne": TrendlyneResponse,
    "marketsmithindia": MarketSmithIndiaResponse,
}
