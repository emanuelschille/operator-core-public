from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, parse, request

from operator_core.bootstrap import BootstrapContext
from operator_core.core.project_resolver import resolve_active_project_context


AirtableTransport = Callable[
    [str, str, dict[str, str], dict[str, Any] | None],
    tuple[int, dict[str, Any]],
]


class AirtableServiceError(RuntimeError):
    pass


class AirtableConfigError(AirtableServiceError):
    pass


class AirtableUsageError(AirtableServiceError):
    pass


class AirtableAPIError(AirtableServiceError):
    def __init__(
        self,
        status_code: int,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(f"Airtable API error ({status_code}): {message}")


@dataclass(frozen=True)
class AirtableProjectContext:
    project_key: str
    base_id: str


@dataclass(frozen=True)
class AirtableRecord:
    record_id: str
    fields: dict[str, Any]
    created_time: str | None = None


@dataclass(frozen=True)
class AirtableRecordList:
    records: tuple[AirtableRecord, ...]
    offset: str | None = None


def _coerce_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _extract_error_message(payload: dict[str, Any]) -> str:
    error_value = payload.get("error")

    if isinstance(error_value, Mapping):
        return str(
            error_value.get("message")
            or error_value.get("type")
            or "Airtable API error"
        )

    if error_value:
        return str(error_value)

    return "Airtable API error"


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(url=url, data=data, headers=headers, method=method)

    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return response.getcode(), _coerce_payload(payload)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, _coerce_payload(payload)
    except error.URLError as exc:
        raise AirtableAPIError(
            status_code=0,
            message=f"Connection error: {exc.reason}",
            payload={},
        ) from exc


class AirtableService:
    def __init__(
        self,
        bootstrap_context: BootstrapContext,
        *,
        transport: AirtableTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bootstrap_context = bootstrap_context
        self.transport = transport or _urllib_transport
        self.logger = logger or logging.getLogger("operator_core.integrations.airtable")

    def resolve_project_context(
        self,
        project_key: str | None = None,
    ) -> AirtableProjectContext:
        settings = self.bootstrap_context.settings

        if not settings.airtable.enabled:
            raise AirtableConfigError("Airtable integration is disabled")

        if not settings.airtable.api_key:
            raise AirtableConfigError("Airtable API key is missing")

        active_project_context = resolve_active_project_context(self.bootstrap_context)
        effective_project_key = (project_key or active_project_context.project_key).strip()

        if not effective_project_key:
            raise AirtableUsageError("project_key must not be empty")

        base_id = settings.airtable.get_base_id(effective_project_key)
        if not base_id:
            raise AirtableConfigError(
                f"Missing Airtable base ID for project '{effective_project_key}'"
            )

        return AirtableProjectContext(
            project_key=effective_project_key,
            base_id=base_id,
        )

    def get_record(
        self,
        table_name: str,
        record_id: str,
        *,
        project_key: str | None = None,
    ) -> AirtableRecord:
        self._require_non_empty(table_name, "table_name")
        self._require_non_empty(record_id, "record_id")

        context = self.resolve_project_context(project_key)
        url = self._build_url(context, table_name, record_id=record_id)

        self.logger.debug(
            "airtable get_record | project=%s table=%s record_id=%s",
            context.project_key,
            table_name,
            record_id,
        )

        payload = self._request_json("GET", url, None)
        return self._parse_record(payload)

    def delete_record(
        self,
        table_name: str,
        record_id: str,
        *,
        project_key: str | None = None,
    ) -> None:
        """Delete a single record by ID. Swallows NOT_FOUND."""
        self._require_non_empty(table_name, "table_name")
        self._require_non_empty(record_id, "record_id")

        context = self.resolve_project_context(project_key)
        url = self._build_url(context, table_name, record_id=record_id)

        self.logger.debug(
            "airtable delete_record | project=%s table=%s record_id=%s",
            context.project_key,
            table_name,
            record_id,
        )

        try:
            self._request_json("DELETE", url, None)
        except AirtableAPIError as exc:
            if exc.status_code == 404:
                return
            raise

    def list_records(
        self,
        table_name: str,
        *,
        project_key: str | None = None,
        view: str | None = None,
        filter_formula: str | None = None,
        max_records: int | None = None,
        fields: tuple[str, ...] = (),
    ) -> AirtableRecordList:
        self._require_non_empty(table_name, "table_name")

        context = self.resolve_project_context(project_key)
        query_params: dict[str, Any] = {}

        if view:
            query_params["view"] = view
        if filter_formula:
            query_params["filterByFormula"] = filter_formula
        if max_records is not None:
            query_params["maxRecords"] = max_records
        if fields:
            query_params["fields[]"] = list(fields)

        url = self._build_url(context, table_name, query_params=query_params)

        self.logger.debug(
            "airtable list_records | project=%s table=%s",
            context.project_key,
            table_name,
        )

        payload = self._request_json("GET", url, None)
        records_payload = payload.get("records")

        if not isinstance(records_payload, list):
            raise AirtableAPIError(
                status_code=0,
                message="Airtable list response did not include a records list",
                payload=payload,
            )

        return AirtableRecordList(
            records=tuple(
                self._parse_record(record_payload)
                for record_payload in records_payload
                if isinstance(record_payload, Mapping)
            ),
            offset=str(payload.get("offset") or "").strip() or None,
        )

    def find_records(
        self,
        table_name: str,
        *,
        filter_formula: str,
        project_key: str | None = None,
        view: str | None = None,
        max_records: int | None = None,
        fields: tuple[str, ...] = (),
    ) -> AirtableRecordList:
        self._require_non_empty(filter_formula, "filter_formula")

        return self.list_records(
            table_name,
            project_key=project_key,
            view=view,
            filter_formula=filter_formula,
            max_records=max_records,
            fields=fields,
        )

    def create_record(
        self,
        table_name: str,
        fields: dict[str, Any],
        *,
        project_key: str | None = None,
    ) -> AirtableRecord:
        self._require_non_empty(table_name, "table_name")
        self._require_fields(fields)

        context = self.resolve_project_context(project_key)
        url = self._build_url(context, table_name)

        self.logger.debug(
            "airtable create_record | project=%s table=%s field_count=%s",
            context.project_key,
            table_name,
            len(fields),
        )

        payload = self._request_json("POST", url, {"fields": dict(fields)})
        return self._parse_record(payload)

    def update_record(
        self,
        table_name: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        project_key: str | None = None,
    ) -> AirtableRecord:
        self._require_non_empty(table_name, "table_name")
        self._require_non_empty(record_id, "record_id")
        self._require_fields(fields)

        context = self.resolve_project_context(project_key)
        url = self._build_url(context, table_name, record_id=record_id)

        self.logger.debug(
            "airtable update_record | project=%s table=%s record_id=%s field_count=%s",
            context.project_key,
            table_name,
            record_id,
            len(fields),
        )

        payload = self._request_json("PATCH", url, {"fields": dict(fields)})
        return self._parse_record(payload)

    def _request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        status_code, payload = self.transport(
            method,
            url,
            self._build_headers(),
            body,
        )

        if status_code >= 400:
            raise AirtableAPIError(
                status_code=status_code,
                message=_extract_error_message(payload),
                payload=payload,
            )

        return payload

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bootstrap_context.settings.airtable.api_key}",
            "Content-Type": "application/json",
        }

    def _build_url(
        self,
        context: AirtableProjectContext,
        table_name: str,
        *,
        record_id: str | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> str:
        table_segment = parse.quote(table_name, safe="")
        url = f"https://api.airtable.com/v0/{context.base_id}/{table_segment}"

        if record_id:
            url = f"{url}/{parse.quote(record_id, safe='')}"

        if query_params:
            url = f"{url}?{parse.urlencode(query_params, doseq=True)}"

        return url

    def _parse_record(self, payload: Mapping[str, Any]) -> AirtableRecord:
        record_id = str(payload.get("id") or "").strip()
        if not record_id:
            raise AirtableAPIError(
                status_code=0,
                message="Airtable response did not include a record id",
                payload=dict(payload),
            )

        fields = payload.get("fields")
        parsed_fields = dict(fields) if isinstance(fields, Mapping) else {}

        return AirtableRecord(
            record_id=record_id,
            fields=parsed_fields,
            created_time=str(payload.get("createdTime") or "").strip() or None,
        )

    def _require_non_empty(self, value: str, field_name: str) -> None:
        if not value.strip():
            raise AirtableUsageError(f"{field_name} must not be empty")

    def _require_fields(self, fields: dict[str, Any]) -> None:
        if not fields:
            raise AirtableUsageError("fields must not be empty")
