from typing import Any, Optional
from requests import Response


class YandexMetrikaApiError(Exception):
    def __init__(self, response: Response, message: Optional[str] = None, *args: Any, **kwargs: Any) -> None:
        self.response = response
        self.message = message

    def __str__(self) -> str:
        return "{} {} {}\nHEADERS = {}\nURL = {}".format(
            self.response.status_code,
            self.response.reason,
            self.message or self.response.text,
            self.response.headers,
            self.response.url,
        )


class YandexMetrikaClientError(YandexMetrikaApiError):
    def __init__(self, response: Response, message: Optional[str] = None, code: Optional[str] = None, errors: Optional[Any] = None) -> None:
        super().__init__(response, message)
        self.code = code
        self.message = message
        self.errors = errors

    def __str__(self) -> str:
        return "code={}, message={}, errors={}".format(
            self.code, self.message, self.errors
        )


class YandexMetrikaTokenError(YandexMetrikaClientError):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class YandexMetrikaLimitError(YandexMetrikaClientError):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class YandexMetrikaDownloadReportError(YandexMetrikaClientError):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def __str__(self) -> str:
        return self.message or ""


class BackwardCompatibilityError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return (
            "This {} is deprecated and not supported. "
            "Install a later version "
            "'pip install --upgrade tapi-yandex-metrika==2020.10.20'. "
            "Info https://github.com/pavelmaksimov/tapi-yandex-metrika"
        ).format(self.name)
