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
from operator_core.integrations.airtable_service import AirtableService
from operator_core.integrations.daily_plan_service import DailyPlanService, TodayPlanSnapshot
from operator_core.integrations.daily_plan_upload_service import (
    DailyPlanUploadService,
    parse_posted_time_input,
)
from operator_core.integrations.operational_knowledge_service import OperationalKnowledgeLoader


def _bootstrap() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="WARNING",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()),
        airtable=AirtableSettings(
            enabled=True,
            api_key="pat-test",
            project_base_ids={"everydayengel": "appProject", "analytics": "appAnalytics"},
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


def _make_services(transport: Any) -> tuple[DailyPlanUploadService, list[tuple[str, str, dict | None]]]:
    calls: list[tuple[str, str, dict | None]] = []

    def wrapped_transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        return transport(method, url, headers, body)

    airtable = AirtableService(_bootstrap())
    airtable.transport = wrapped_transport
    daily_plan_service = DailyPlanService(airtable)
    ok_loader = OperationalKnowledgeLoader(airtable)
    service = DailyPlanUploadService(
        airtable_service=airtable,
        ok_loader=ok_loader,
        daily_plan_service=daily_plan_service,
    )
    return service, calls


def test_upload_writes_to_resolved_platform_table_with_real_field_mapping() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "GET" and "Operational%20Knowledge" in url:
            return 200, {
                "records": [
                    {"id": "ok1", "fields": {"Key": "analytics_table_tiktok", "Value": "tblTikTok", "Status": "active"}},
                    {"id": "ok2", "fields": {"Key": "posting_time_tiktok", "Value": "20:15", "Status": "active"}},
                ]
            }
        if method == "GET" and url.endswith("/recDraft123"):
            return 200, {
                "id": "recDraft123",
                "fields": {"content_id": "content-42"},
                "createdTime": "2026-04-11T10:00:00.000Z",
            }
        if method == "POST" and "tblTikTok" in url:
            return 200, {
                "id": "recAnalytics1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T10:01:00.000Z",
            }
        if method == "PATCH" and "Daily%20Plans" in url:
            return 200, {
                "id": "recPlan1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T10:02:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service, calls = _make_services(transport)
    result = service.upload_plan_snapshot(
        project_key="everydayengel",
        date="2026-04-11",
        snapshot=TodayPlanSnapshot(
            record_id="recPlan1",
            decision="pending",
            platform="tiktok",
            candidate_record_id="recDraft123",
            serie_thema="Wintergebäck",
            title_raw="Kekse zur Weihnachtszeit",
            hook="Hook",
            cta="CTA",
            caption="Caption",
            format_typ="Reel",
            bereit="bereit",
        ),
    )

    post_body = next(body for method, url, body in calls if method == "POST" and "tblTikTok" in url)
    assert post_body["fields"] == {
        "serie_thema": "Wintergebäck",
        "titel": "Kekse zur Weihnachtszeit",
        "hook_kurz": "Hook",
        "cta_typ": "CTA",
        "caption": "Caption",
        "format_typ": "Reel",
        "bereit": "bereit",
        "draft_record_id": "recDraft123",
        "content_id": "content-42",
    }
    assert result.analytics_record_id == "recAnalytics1"
    assert result.default_posted_at_local == "2026-04-11 20:15"


def test_upload_prefers_weekday_posting_schedule_row_for_default_posted_time() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "GET" and "Operational%20Knowledge" in url:
            return 200, {
                "records": [
                    {"id": "ok1", "fields": {"Key": "analytics_table_facebook_reel", "Value": "tblFacebook", "Status": "active"}},
                    {
                        "id": "ok2",
                        "fields": {
                            "Key": "posting_schedule_facebook_reel_saturday",
                            "Value": '{"platform":"facebook_reel","weekday":"saturday","timezone":"Europe/Berlin","enabled":true,"time_local":"18:05","condition":"only_if_strong_video","note":"nur wenn starkes Video"}',
                            "Status": "active",
                        },
                    },
                    {"id": "ok3", "fields": {"Key": "posting_time_facebook", "Value": "18:00", "Status": "active"}},
                ]
            }
        if method == "POST" and "tblFacebook" in url:
            return 200, {
                "id": "recAnalytics1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-12T10:01:00.000Z",
            }
        if method == "PATCH" and "Daily%20Plans" in url:
            return 200, {
                "id": "recPlan1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-12T10:02:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service, _calls = _make_services(transport)
    result = service.upload_plan_snapshot(
        project_key="everydayengel",
        date="2026-04-11",
        snapshot=TodayPlanSnapshot(
            record_id="recPlan1",
            decision="pending",
            platform="facebook_reel",
            cta="CTA",
        ),
    )

    assert result.default_posted_at_local == "2026-04-11 18:05"


def test_upload_normalizes_internal_bereit_value() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "GET" and "Operational%20Knowledge" in url:
            return 200, {
                "records": [
                    {"id": "ok1", "fields": {"Key": "analytics_table_tiktok", "Value": "tblTikTok", "Status": "active"}},
                ]
            }
        if method == "POST" and "tblTikTok" in url:
            return 200, {
                "id": "recAnalytics1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T10:01:00.000Z",
            }
        if method == "PATCH" and "Daily%20Plans" in url:
            return 200, {
                "id": "recPlan1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T10:02:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service, calls = _make_services(transport)
    service.upload_plan_snapshot(
        project_key="everydayengel",
        date="2026-04-11",
        snapshot=TodayPlanSnapshot(
            record_id="recPlan1",
            decision="pending",
            platform="tiktok",
            bereit="not_required",
        ),
    )

    post_body = next(body for method, url, body in calls if method == "POST" and "tblTikTok" in url)
    assert post_body["fields"]["bereit"] == "Kein Review nötig"


def test_set_posted_at_local_updates_analytics_record_and_daily_plan_row() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        if method == "PATCH" and "tblTikTok" in url:
            return 200, {
                "id": "recAnalytics1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T10:05:00.000Z",
            }
        if method == "PATCH" and "Daily%20Plans" in url:
            return 200, {
                "id": "recPlan1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T10:06:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service, calls = _make_services(transport)
    updated = service.set_posted_at_local(
        project_key="everydayengel",
        posted_at_local="2026-04-11 19:45",
        snapshot=TodayPlanSnapshot(
            record_id="recPlan1",
            decision="pending",
            platform="tiktok",
            platform_record_id="recAnalytics1",
            platform_table_id="tblTikTok",
        ),
    )

    assert calls[0][2]["fields"] == {"posted_at_local": "2026-04-11 19:45"}
    assert calls[1][2]["fields"] == {"posted_at_local": "2026-04-11 19:45"}
    assert updated.posted_at_local == "2026-04-11 19:45"


def test_parse_posted_time_input_accepts_hh_mm_only() -> None:
    assert parse_posted_time_input(date="2026-04-11", text="19:45") == "2026-04-11 19:45"
    assert parse_posted_time_input(date="2026-04-11", text="19.45") is None
