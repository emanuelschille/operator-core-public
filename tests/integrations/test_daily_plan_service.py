"""
Tests for DailyPlanService.

Covers:
  - upsert_plan creates new row when no row exists for (project, date)
  - upsert_plan returns existing record_id when decision is already decided
  - upsert_plan refreshes metadata when existing row is pending
  - update_decision calls update_record with correct fields only
  - update_decision does not send extra fields
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.integrations.airtable_service import AirtableRecordList, AirtableService
from operator_core.integrations.daily_plan_service import DailyPlanService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bootstrap() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(
            enabled=False,
            bot_token="",
            allowed_user_ids=(),
            allowed_chat_ids=(),
        ),
        airtable=AirtableSettings(
            enabled=True,
            api_key="pat-test",
            project_base_ids={"everydayengel": "appTestBase123"},
        ),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "everydayengel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


def _make_airtable_service(transport: Any) -> AirtableService:
    ctx = _make_bootstrap()
    svc = AirtableService(ctx)
    svc.transport = transport
    return svc


def _make_service(transport: Any) -> DailyPlanService:
    return DailyPlanService(_make_airtable_service(transport))


class _CaptureFindRecordsAirtableService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def find_records(self, table_name: str, **kwargs: object) -> AirtableRecordList:
        self.calls.append({"table_name": table_name, **kwargs})
        return AirtableRecordList(records=())


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

def _transport_empty_list(
    method: str, url: str, headers: dict, body: dict | None
) -> tuple[int, dict]:
    """GET → empty records list; POST/PATCH → echo back a created/updated record."""
    if method == "GET":
        return 200, {"records": []}
    if method in ("POST", "PATCH"):
        record_id = "recNewRecord1234"
        fields = body.get("fields", {}) if body else {}
        return 200, {"id": record_id, "fields": fields, "createdTime": "2026-04-08T07:00:00.000Z"}
    return 200, {}


def _transport_pending_row(
    method: str, url: str, headers: dict, body: dict | None
) -> tuple[int, dict]:
    """GET → one pending row; PATCH → echo updated record."""
    if method == "GET":
        return 200, {
            "records": [
                {
                    "id": "recExistingPending",
                    "fields": {
                        "date": "2026-04-08",
                        "project": "everydayengel",
                        "decision": "pending",
                    },
                    "createdTime": "2026-04-08T06:00:00.000Z",
                }
            ]
        }
    if method == "PATCH":
        fields = body.get("fields", {}) if body else {}
        return 200, {
            "id": "recExistingPending",
            "fields": fields,
            "createdTime": "2026-04-08T06:00:00.000Z",
        }
    return 200, {}


def _transport_decided_row(decision: str):
    def transport(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        """GET → one decided row; calls to POST/PATCH should not occur."""
        if method == "GET":
            return 200, {
                "records": [
                    {
                        "id": "recDecidedRow1234",
                        "fields": {
                            "date": "2026-04-08",
                            "project": "everydayengel",
                            "decision": decision,
                        },
                        "createdTime": "2026-04-08T06:00:00.000Z",
                    }
                ]
            }
        raise AssertionError(f"Unexpected {method} call when row is already decided")
    return transport


def _transport_update_capture() -> tuple[Any, list]:
    """Returns a (transport, calls) pair. calls accumulates (method, url, body)."""
    calls: list[tuple[str, str, dict | None]] = []

    def transport(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        calls.append((method, url, body))
        return 200, {
            "id": "recUpdated1234",
            "fields": (body or {}).get("fields", {}),
            "createdTime": "2026-04-08T07:00:00.000Z",
        }

    return transport, calls


# ---------------------------------------------------------------------------
# Tests: upsert_plan
# ---------------------------------------------------------------------------

def test_upsert_creates_record_when_no_row_exists() -> None:
    svc = _make_service(_transport_empty_list)
    record_id = svc.upsert_plan(
        project_key="everydayengel",
        date="2026-04-08",
        plan_type="post",
        candidate_record_id="recDraft001",
        platform="tiktok",
        candidate_count=2,
    )
    assert record_id == "recNewRecord1234"


def test_upsert_uses_date_normalizing_lookup_formula() -> None:
    airtable = _CaptureFindRecordsAirtableService()
    svc = DailyPlanService(airtable)  # type: ignore[arg-type]

    svc.get_today_plan(project_key="everydayengel", date="2026-04-09")

    assert len(airtable.calls) == 1
    assert airtable.calls[0]["table_name"] == "Daily Plans"
    assert airtable.calls[0]["filter_formula"] == (
        "AND(DATETIME_FORMAT({date}, 'YYYY-MM-DD')=\"2026-04-09\","
        "{project}=\"everydayengel\")"
    )


def test_upsert_includes_correct_fields_on_create() -> None:
    transport, calls = _transport_update_capture()

    # First call is GET (list), second is POST (create)
    get_called = [False]

    def transport_seq(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        if method == "GET" and not get_called[0]:
            get_called[0] = True
            return 200, {"records": []}
        calls.append((method, url, body))
        return 200, {
            "id": "recCreated",
            "fields": (body or {}).get("fields", {}),
            "createdTime": "2026-04-08T07:00:00.000Z",
        }

    svc = _make_service(transport_seq)
    svc.upsert_plan(
        project_key="everydayengel",
        date="2026-04-08",
        plan_type="post",
        candidate_record_id="recDraft001",
        platform="tiktok",
        candidate_count=3,
    )

    assert len(calls) == 1
    method, _url, body = calls[0]
    assert method == "POST"
    fields = (body or {}).get("fields", {})
    assert fields["date"] == "2026-04-08"
    assert fields["project"] == "everydayengel"
    assert fields["plan_type"] == "post"
    assert fields["decision"] == "pending"
    assert fields["candidate_record_id"] == "recDraft001"
    assert fields["platform"] == "tiktok"
    assert fields["candidate_count"] == 3


def test_upsert_returns_existing_record_id_when_decided() -> None:
    for decision in ("post", "skip", "draft"):
        svc = _make_service(_transport_decided_row(decision))
        record_id = svc.upsert_plan(
            project_key="everydayengel",
            date="2026-04-08",
            plan_type="post",
        )
        assert record_id == "recDecidedRow1234", f"Expected no-overwrite for decision={decision}"


def test_upsert_does_not_call_create_when_decided() -> None:
    write_calls: list = []

    def transport(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        if method != "GET":
            write_calls.append(method)
        return 200, {
            "records": [
                {
                    "id": "recDecidedRow1234",
                    "fields": {"date": "2026-04-08", "project": "everydayengel", "decision": "post"},
                    "createdTime": "2026-04-08T06:00:00.000Z",
                }
            ]
        }

    svc = _make_service(transport)
    svc.upsert_plan(project_key="everydayengel", date="2026-04-08", plan_type="post")
    assert write_calls == [], "No write calls expected when row is already decided"


def test_upsert_refreshes_pending_row_metadata() -> None:
    transport, calls = _transport_update_capture()

    get_called = [False]

    def transport_seq(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        if method == "GET" and not get_called[0]:
            get_called[0] = True
            return 200, {
                "records": [
                    {
                        "id": "recExistingPending",
                        "fields": {
                            "date": "2026-04-08",
                            "project": "everydayengel",
                            "decision": "pending",
                        },
                        "createdTime": "2026-04-08T06:00:00.000Z",
                    }
                ]
            }
        calls.append((method, url, body))
        return 200, {
            "id": "recExistingPending",
            "fields": (body or {}).get("fields", {}),
            "createdTime": "2026-04-08T06:00:00.000Z",
        }

    svc = _make_service(transport_seq)
    record_id = svc.upsert_plan(
        project_key="everydayengel",
        date="2026-04-08",
        plan_type="draft",
        candidate_count=5,
    )

    assert record_id == "recExistingPending"
    assert len(calls) == 1
    method, _url, body = calls[0]
    assert method == "PATCH"
    fields = (body or {}).get("fields", {})
    assert fields["plan_type"] == "draft"
    assert fields["candidate_count"] == 5
    # decision is NOT in the refresh payload — only metadata is updated
    assert "decision" not in fields


def test_upsert_reuses_latest_pending_duplicate_instead_of_creating_new_row() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET":
            return 200, {
                "records": [
                    {
                        "id": "recPendingOlder",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": "pending",
                        },
                        "createdTime": "2026-04-09T06:00:00.000Z",
                    },
                    {
                        "id": "recPendingNewest",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": "pending",
                        },
                        "createdTime": "2026-04-09T06:05:00.000Z",
                    },
                ]
            }
        if method == "PATCH":
            return 200, {
                "id": "recPendingNewest",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-09T06:05:00.000Z",
            }
        raise AssertionError(f"Unexpected method: {method}")

    svc = _make_service(transport)
    record_id = svc.upsert_plan(
        project_key="everydayengel",
        date="2026-04-09",
        plan_type="draft",
        candidate_count=4,
    )

    assert record_id == "recPendingNewest"
    methods = [method for method, _url, _body in calls]
    assert methods == ["GET", "PATCH"]
    _method, url, body = calls[1]
    assert "recPendingNewest" in url
    fields = (body or {}).get("fields", {})
    assert fields["plan_type"] == "draft"
    assert fields["candidate_count"] == 4


# ---------------------------------------------------------------------------
# Tests: update_decision
# ---------------------------------------------------------------------------

def test_update_decision_calls_update_record_with_correct_fields() -> None:
    transport, calls = _transport_update_capture()
    svc = _make_service(transport)
    svc.update_decision(
        project_key="everydayengel",
        record_id="recABC123",
        decision="post",
    )

    assert len(calls) == 1
    method, _url, body = calls[0]
    assert method == "PATCH"
    fields = (body or {}).get("fields", {})
    assert fields == {"decision": "post"}


def test_update_decision_only_sends_decision_field() -> None:
    transport, calls = _transport_update_capture()
    svc = _make_service(transport)
    svc.update_decision(
        project_key="everydayengel",
        record_id="recABC123",
        decision="skip",
    )

    fields = (calls[0][2] or {}).get("fields", {})
    assert list(fields.keys()) == ["decision"], "Only 'decision' field should be sent"


# ---------------------------------------------------------------------------
# Tests: get_today_plan
# ---------------------------------------------------------------------------

def _transport_full_row(decision: str, plan_type: str, platform: str, candidate_count: int):
    def transport(
        method: str, url: str, headers: dict, body: dict | None
    ) -> tuple[int, dict]:
        if method == "GET":
            return 200, {
                "records": [
                    {
                        "id": "recStoredPlan001",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": decision,
                            "plan_type": plan_type,
                            "platform": platform,
                            "candidate_count": candidate_count,
                        },
                        "createdTime": "2026-04-09T06:00:00.000Z",
                    }
                ]
            }
        raise AssertionError(f"Unexpected {method} call in get_today_plan transport")
    return transport


def test_get_today_plan_returns_snapshot_when_row_exists() -> None:
    svc = _make_service(_transport_full_row("post", "post", "tiktok", 2))
    snapshot = svc.get_today_plan(project_key="everydayengel", date="2026-04-09")

    assert snapshot is not None
    assert snapshot.record_id == "recStoredPlan001"
    assert snapshot.decision == "post"
    assert snapshot.plan_type == "post"
    assert snapshot.platform == "tiktok"
    assert snapshot.candidate_count == 2


def test_get_today_plan_returns_none_when_no_row_exists() -> None:
    svc = _make_service(_transport_empty_list)
    snapshot = svc.get_today_plan(project_key="everydayengel", date="2026-04-09")

    assert snapshot is None


def test_get_today_plan_defaults_empty_decision_to_pending() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "GET":
            return 200, {
                "records": [
                    {
                        "id": "recNullDecision",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            # decision field absent
                        },
                        "createdTime": "2026-04-09T06:00:00.000Z",
                    }
                ]
            }
        raise AssertionError("Unexpected write call")

    svc = _make_service(transport)
    snapshot = svc.get_today_plan(project_key="everydayengel", date="2026-04-09")

    assert snapshot is not None
    assert snapshot.decision == "pending"


def test_get_today_plan_maps_all_decided_states() -> None:
    for decision in ("post", "skip", "draft"):
        svc = _make_service(_transport_full_row(decision, "post", "tiktok", 1))
        snapshot = svc.get_today_plan(project_key="everydayengel", date="2026-04-09")
        assert snapshot is not None
        assert snapshot.decision == decision


def test_get_today_plan_prefers_decided_duplicate_over_pending() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "GET":
            return 200, {
                "records": [
                    {
                        "id": "recPending001",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": "pending",
                            "plan_type": "post",
                            "platform": "tiktok",
                        },
                        "createdTime": "2026-04-09T06:00:00.000Z",
                    },
                    {
                        "id": "recDecided001",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": "skip",
                            "plan_type": "skip",
                        },
                        "createdTime": "2026-04-09T05:59:00.000Z",
                    },
                ]
            }
        raise AssertionError("Unexpected write call")

    svc = _make_service(transport)
    snapshot = svc.get_today_plan(project_key="everydayengel", date="2026-04-09")

    assert snapshot is not None
    assert snapshot.record_id == "recDecided001"
    assert snapshot.decision == "skip"


def test_get_today_plan_prefers_most_recent_duplicate_when_none_decided() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "GET":
            return 200, {
                "records": [
                    {
                        "id": "recPendingOlder",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": "pending",
                        },
                        "createdTime": "2026-04-09T06:00:00.000Z",
                    },
                    {
                        "id": "recPendingNewest",
                        "fields": {
                            "date": "2026-04-09",
                            "project": "everydayengel",
                            "decision": "pending",
                            "plan_type": "draft",
                        },
                        "createdTime": "2026-04-09T06:05:00.000Z",
                    },
                ]
            }
        raise AssertionError("Unexpected write call")

    svc = _make_service(transport)
    snapshot = svc.get_today_plan(project_key="everydayengel", date="2026-04-09")

    assert snapshot is not None
    assert snapshot.record_id == "recPendingNewest"
    assert snapshot.decision == "pending"
    assert snapshot.plan_type == "draft"
