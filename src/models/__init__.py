from .screener import ScreenerResponse
from .trendlyne import TrendlyneResponse

# Mapping of data source names to their corresponding response models
SOURCE_MODELS = {
    "screener": ScreenerResponse,
    "trendlyne": TrendlyneResponse,
}
