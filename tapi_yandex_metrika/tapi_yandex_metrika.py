import io
import logging
import random
import re
import time
from typing import Any, Dict, Generator, List, Optional, Union

import orjson
from requests import Response
from tapi2 import TapiAdapter, generate_wrapper_from_adapter, JSONAdapterMixin
from tapi2.exceptions import ResponseProcessException

from tapi_yandex_metrika import exceptions
from .resource_mapping import (
    STATS_RESOURCE_MAPPING,
    LOGSAPI_RESOURCE_MAPPING,
    MANAGEMENT_RESOURCE_MAPPING,
)

logger = logging.getLogger(__name__)

LIMIT: int = 10000


class YandexMetrikaClientAdapterAbstract(JSONAdapterMixin, TapiAdapter):
    def get_api_root(self, api_params: Dict[str, Any], resource_name: str) -> str:
        return "https://api-metrika.yandex.net/"

    def get_request_kwargs(self, api_params: Dict[str, Any], *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if "receive_all_data" in api_params:
            raise exceptions.BackwardCompatibilityError("parameter 'receive_all_data'")

        params = super().get_request_kwargs(api_params, *args, **kwargs)
        params["headers"]["Authorization"] = "OAuth {}".format(
            api_params["access_token"]
        )
        return params

    def get_error_message(self, data: Optional[Any], response: Optional[Response] = None) -> Dict[str, Any]:
        if data is None:
            if response is not None:
                return {"error_text": response.content.decode()}
            return {"error_text": "Unknown error"}
        else:
            return data

    def format_data_to_request(self, data: Optional[Any]) -> Optional[bytes]:
        if data:
            return orjson.dumps(data)
        return None

    def process_response(self, response: Response, request_kwargs: Dict[str, Any], **kwargs: Any) -> Any:
        data = super().process_response(response, request_kwargs, **kwargs)
        if isinstance(data, dict) and "errors" in data:
            raise ResponseProcessException(response, data)
        return data

    def response_to_native(self, response: Response) -> Optional[Union[Dict[str, Any], str]]:
        if response.content.strip():
            try:
                return orjson.loads(response.content.decode())
            except ValueError:
                return response.text
        return None

    def retry_request(
        self,
        tapi_exception: Exception,
        error_message: Dict[str, Any],
        repeat_number: int,
        response: Response,
        request_kwargs: Dict[str, Any],
        api_params: Dict[str, Any],
        **kwargs: Any
    ) -> bool:
        code = int(error_message.get("code", 0))
        message = error_message.get("message", "")
        errors_types = [i.get("error_type") for i in error_message.get("errors", [])]

        limit_errors = {
            "quota_requests_by_uid":
                "The limit on the number of API requests per day for the user has been exceeded.",
            "quota_delegate_requests":
                "Exceeded the limit on the number of API requests to add representatives per hour for a user.",
            "quota_grants_requests":
                "Exceeded the limit on the number of API requests to add access to the counter per hour",
            "quota_requests_by_ip":
                "The limit on the number of API requests per second for an IP address has been exceeded.",
            "quota_parallel_requests":
                "The limit on the number of parallel API requests per day for the user has been exceeded.",
            "quota_requests_by_counter_id":
                "The limit on the number of API requests per day for the counter has been exceeded.",
        }
        big_report_request = (
            "Query is too complicated. Please reduce the date interval or sampling."
        )

        if code == 400:
            if message == big_report_request:
                if repeat_number < 10:
                    retry_seconds = random.randint(5, 30)
                    big_report_request += " Re-request after {} seconds".format(
                        retry_seconds
                    )
                    logger.warning(big_report_request)
                    time.sleep(retry_seconds)
                    return True

        if code == 429:
            if "quota_requests_by_ip" in errors_types:
                retry_seconds = random.randint(1, 30)
                error_text = "{} Re-request after {} seconds.".format(
                    limit_errors["quota_requests_by_ip"], retry_seconds
                )
                logger.warning(error_text)
                time.sleep(retry_seconds)
                return True
            else:
                for err in errors_types:
                    logger.error(limit_errors[err])

        elif code == 503:
            if repeat_number < api_params.get("retries_if_server_error", 3):
                logger.warning("Server error. Re-request after 3 seconds")
                time.sleep(5)
                return True

        return False

    def error_handling(
        self,
        tapi_exception: Exception,
        error_message: Dict[str, Any],
        repeat_number: int,
        response: Response,
        request_kwargs: Dict[str, Any],
        api_params: Dict[str, Any],
        **kwargs: Any
    ) -> None:
        if "error_text" in error_message:
            raise exceptions.YandexMetrikaApiError(
                response, error_message["error_text"]
            )
        else:
            error_code = int(error_message.get("code", 0))

            if error_code == 429:
                raise exceptions.YandexMetrikaLimitError(response, **error_message)
            elif error_code == 403:
                raise exceptions.YandexMetrikaTokenError(response, **error_message)
            else:
                raise exceptions.YandexMetrikaClientError(response, **error_message)

    def transform(self, **kwargs: Any) -> None:
        raise exceptions.BackwardCompatibilityError("method 'transform'")


class YandexMetrikaManagementClientAdapter(YandexMetrikaClientAdapterAbstract):
    resource_mapping = MANAGEMENT_RESOURCE_MAPPING


class YandexMetrikaLogsapiClientAdapter(YandexMetrikaClientAdapterAbstract):
    resource_mapping = LOGSAPI_RESOURCE_MAPPING

    def process_response(self, response: Response, request_kwargs: Dict[str, Any], **kwargs: Any) -> Any:
        data = super().process_response(response, request_kwargs, **kwargs)
        if "download" in request_kwargs["url"]:
            kwargs["store"]["columns"] = data[: data.find("\n")].split("\t")
        else:
            kwargs["store"].pop("columns", None)
        return data

    def error_handling(
        self,
        tapi_exception: Exception,
        error_message: Dict[str, Any],
        repeat_number: int,
        response: Response,
        request_kwargs: Dict[str, Any],
        api_params: Dict[str, Any],
        **kwargs: Any
    ) -> None:
        message = error_message.get("message")
        if message == "Incorrect part number":
            # Fires when trying to download a non-existent part of a report.
            return

        if message == "Only log of requests in status 'processed' can be downloaded":
            raise exceptions.YandexMetrikaDownloadReportError(response)

        return super().error_handling(
            tapi_exception,
            error_message,
            repeat_number,
            response,
            request_kwargs,
            api_params,
            **kwargs
        )

    def _check_status_report(self, response: Response, api_params: Dict[str, Any], **kwargs: Any) -> None:
        request_id = api_params["default_url_params"]["requestId"]
        if kwargs["store"].get(request_id) is None:
            client = kwargs["client"]
            info = client.info(requestId=request_id).get()
            status = info.data["log_request"]["status"]
            if status not in ("processed", "created"):
                raise exceptions.YandexMetrikaDownloadReportError(
                    response,
                    message=f"Such status '{status}' of the report does not allow downloading it",
                )
            kwargs["store"][request_id] = status

    def retry_request(
        self,
        tapi_exception: Exception,
        error_message: Dict[str, Any],
        repeat_number: int,
        response: Response,
        request_kwargs: Dict[str, Any],
        api_params: Dict[str, Any],
        **kwargs: Any,
    ) -> bool:
        """
        Conditions for repeating a request. If it returns True, the request will be repeated.
        """
        message = error_message.get("message")

        if (
            message == "Only log of requests in status 'processed' can be downloaded"
            and "download" in request_kwargs["url"]
            and api_params.get("wait_report", False)
        ):
            self._check_status_report(response, api_params, **kwargs)

            # The error appears when trying to download an unprepared report.
            max_sleep = 60 * 5
            sleep_time = repeat_number * 60
            sleep_time = sleep_time if sleep_time <= max_sleep else max_sleep
            logger.info("Wait report %s sec.", sleep_time)
            time.sleep(sleep_time)

            return True

        return super().retry_request(
            tapi_exception,
            error_message,
            repeat_number,
            response,
            request_kwargs,
            api_params,
            **kwargs,
        )

    def fill_resource_template_url(self, template: str, params: Dict[str, Any], resource: str) -> str:
        if resource == "download" and not params.get("partNumber"):
            params.update(partNumber=0)
        return super().fill_resource_template_url(template, params, resource)

    def get_iterator_next_request_kwargs(
        self, response_data: Any, response: Response, request_kwargs: Dict[str, Any], api_params: Dict[str, Any], **kwargs: Any
    ) -> Dict[str, Any]:
        url = request_kwargs["url"]

        if "download" not in url:
            raise NotImplementedError("Iteration not supported for this resource")

        part = int(re.findall(r"part/([0-9]*)/", url)[0])
        next_part = part + 1
        new_url = re.sub(r"part/[0-9]*/", "part/{}/".format(next_part), url)
        return {**request_kwargs, "url": new_url}

    def _iter_line(self, text: str, **kwargs: Any) -> Generator[str, None, None]:
        if "download" not in kwargs["request_kwargs"]["url"]:
            raise NotImplementedError("Only available for download resource responses")

        f = io.StringIO(text)
        next(f)  # skipping columns
        return (line.replace("\n", "") for line in f)

    def get_iterator_iteritems(self, response_data: Any, **kwargs: Any) -> Union[Generator[str, None, None], List[Any]]:
        if response_data:
            return self._iter_line(response_data, **kwargs)
        else:
            return []

    def get_iterator_pages(self, response_data: Any, **kwargs: Any) -> List[Any]:
        if response_data:
            return [response_data]
        else:
            return []

    def get_iterator_items(self, data: str, **kwargs: Any) -> Generator[str, None, None]:
        return self._iter_line(data, **kwargs)

    def parts(self, max_parts: Optional[int] = None, **kwargs: Any) -> Generator[Any, None, None]:
        client = kwargs["client"]
        yield from client.pages(max_pages=max_parts)

    def iter_lines(self, max_parts: Optional[int] = None, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[str, None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        yield from client.iter_items(max_pages=max_parts, max_items=max_rows)

    def iter_values(self, max_parts: Optional[int] = None, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[List[str], None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        for line in client.iter_items(max_pages=max_parts, max_items=max_rows):
            yield line.split("\t")

    def iter_dicts(self, max_parts: Optional[int] = None, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[Dict[str, str], None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        for values in client.iter_values(max_pages=max_parts, max_items=max_rows):
            yield dict(zip(kwargs["store"]["columns"], values))

    def lines(self, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[str, None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        yield from client.items(max_items=max_rows)

    def values(self, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[List[str], None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        for line in client.items(max_items=max_rows):
            yield line.split("\t")

    def dicts(self, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[Dict[str, str], None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        for line in client.items(max_items=max_rows):
            yield dict(zip(kwargs["store"]["columns"], line.split("\t")))

    def to_dicts(self, data: str, **kwargs: Any) -> List[Dict[str, str]]:
        return [
            dict(zip(kwargs["store"]["columns"], line.split("\t")))
            for line in data.split("\n")[1:]
            if line
        ]

    def to_values(self, data: str, **kwargs: Any) -> List[List[str]]:
        return [line.split("\t") for line in data.split("\n")[1:] if line]

    def to_lines(self, data: str, **kwargs: Any) -> List[str]:
        return [line for line in data.split("\n")[1:] if line]

    def to_columns(self, data: str, **kwargs: Any) -> List[List[str]]:
        columns: List[List[str]] = [[] for _ in range(len(kwargs["store"]["columns"]))]
        for line in self._iter_line(data, **kwargs):
            values = line.split("\t")
            for i, col in enumerate(columns):
                col.append(values[i])

        return columns


class YandexMetrikaStatsClientAdapter(YandexMetrikaClientAdapterAbstract):
    resource_mapping = STATS_RESOURCE_MAPPING

    def process_response(self, response: Response, request_kwargs: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        data = super().process_response(response, request_kwargs, **kwargs)
        attribution = data["query"]["attribution"]
        sampled = data["sampled"]
        sample_share = data["sample_share"]
        total_rows = int(data["total_rows"])
        offset = data["query"]["offset"]
        limit = request_kwargs["params"].get("limit", LIMIT)
        offset2 = offset + limit - 1
        if offset2 > total_rows:
            offset2 = total_rows

        if sampled:
            logger.info("Sample: {}".format(sample_share))
        logger.info("Attribution: {}".format(attribution))
        logger.info(
            "Exported lines {}-{}. Total rows {}".format(offset, offset2, total_rows)
        )

        kwargs["store"]["columns"] = (
            data["query"]["dimensions"] + data["query"]["metrics"]
        )

        return data

    def _iter_transform_data(self, data: Dict[str, Any]) -> Generator[List[Any], None, None]:
        for row in data["data"]:
            dimensions_data = [i["name"] for i in row["dimensions"]]
            metrics_data = row["metrics"]
            yield dimensions_data + metrics_data

    def to_values(self, data: Dict[str, Any], **kwargs: Any) -> List[List[Any]]:
        return list(self._iter_transform_data(data))

    def to_columns(self, data: Dict[str, Any], **kwargs: Any) -> Optional[List[List[Any]]]:
        columns: Optional[List[List[Any]]] = None
        for row in self._iter_transform_data(data):
            if columns is None:
                columns = [[] for _ in range(len(row))]

            for i, col in enumerate(columns):
                col.append(row[i])

        return columns

    def to_dicts(self, data: Dict[str, Any], **kwargs: Any) -> List[Dict[str, Any]]:
        return [
            dict(zip(kwargs["store"]["columns"], row))
            for row in self._iter_transform_data(data)
        ]

    def get_iterator_next_request_kwargs(
        self, response_data: Dict[str, Any], response: Response, request_kwargs: Dict[str, Any], api_params: Dict[str, Any], **kwargs: Any
    ) -> Optional[Dict[str, Any]]:
        total_rows = int(response_data["total_rows"])
        limit = request_kwargs["params"].get("limit", LIMIT)
        offset = response_data["query"]["offset"] + limit

        if offset <= total_rows:
            request_kwargs["params"]["offset"] = offset
            return request_kwargs
        return None

    def get_iterator_iteritems(self, response_data: Dict[str, Any], **kwargs: Any) -> Generator[List[Any], None, None]:
        return self._iter_transform_data(response_data)

    def get_iterator_pages(self, response_data: Dict[str, Any], **kwargs: Any) -> List[Dict[str, Any]]:
        return [response_data]

    def get_iterator_items(self, data: Dict[str, Any], **kwargs: Any) -> Generator[List[Any], None, None]:
        return self._iter_transform_data(data)

    def iter_rows(self, max_pages: Optional[int] = None, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[List[Any], None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        yield from client.iter_items(max_pages=max_pages, max_items=max_rows)

    def iter_values(self, max_pages: Optional[int] = None, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[List[Any], None, None]:
        return self.iter_rows(max_pages=max_pages, max_rows=max_rows, **kwargs)

    def iter_dicts(self, max_pages: Optional[int] = None, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[Dict[str, Any], None, None]:
        for values in self.iter_values(
            max_pages=max_pages, max_rows=max_rows, **kwargs
        ):
            yield dict(zip(kwargs["store"]["columns"], values))

    def rows(self, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[List[Any], None, None]:
        max_rows = max_rows or kwargs.get("max_items")
        client = kwargs["client"]
        yield from client.items(max_items=max_rows)

    def values(self, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[List[Any], None, None]:
        return self.rows(max_rows=max_rows, **kwargs)

    def dicts(self, max_rows: Optional[int] = None, **kwargs: Any) -> Generator[Dict[str, Any], None, None]:
        for values in self.values(max_rows=max_rows, **kwargs):
            yield dict(zip(kwargs["store"]["columns"], values))


YandexMetrikaStats = generate_wrapper_from_adapter(YandexMetrikaStatsClientAdapter)
YandexMetrikaLogsapi = generate_wrapper_from_adapter(YandexMetrikaLogsapiClientAdapter)
YandexMetrikaManagement = generate_wrapper_from_adapter(
    YandexMetrikaManagementClientAdapter
)
