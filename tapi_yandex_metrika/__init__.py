__author__: str = "Pavel Maksimov"
__email__: str = "vur21@ya.ru"
__version__: str = "2022.4.8"

from .resource_mapping import (
    STATS_RESOURCE_MAPPING,
    LOGSAPI_RESOURCE_MAPPING,
    MANAGEMENT_RESOURCE_MAPPING,
)
from .tapi_yandex_metrika import (
    YandexMetrikaStats,
    YandexMetrikaLogsapi,
    YandexMetrikaManagement,
)

__all__ = [
    "STATS_RESOURCE_MAPPING",
    "LOGSAPI_RESOURCE_MAPPING",
    "MANAGEMENT_RESOURCE_MAPPING",
    "YandexMetrikaStats",
    "YandexMetrikaLogsapi",
    "YandexMetrikaManagement",
]
