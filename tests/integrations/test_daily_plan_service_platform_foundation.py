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
            project_base_ids={"everydayengel": "appTestBase123", "analytics": "appAnalytics123"},
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


def _make_service(transport: Any) -> DailyPlanService:
    airtable = AirtableService(_bootstrap())
    airtable.transport = transport
    return DailyPlanService(airtable)


def test_upsert_uses_platform_in_lookup_formula() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET":
            return 200, {"records": []}
        return 200, {
            "id": "recPlatformPlan",
            "fields": (body or {}).get("fields", {}),
            "createdTime": "2026-04-11T08:00:00.000Z",
        }

    service = _make_service(transport)
    service.upsert_plan(
        project_key="everydayengel",
        date="2026-04-11",
        platform="tiktok",
        plan_type="post",
        candidate_record_id="recDraft001",
        candidate_count=2,
    )

    assert "LOWER%28%7Bplatform%7D%29%3D%22tiktok%22" in calls[0][1]


def test_list_today_plans_returns_one_snapshot_per_platform() -> None:
    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        assert method == "GET"
        return 200, {
            "records": [
                {
                    "id": "rec-tiktok-1",
                    "fields": {
                        "date": "2026-04-11",
                        "project": "everydayengel",
                        "platform": "tiktok",
                        "decision": "pending",
                    },
                    "createdTime": "2026-04-11T08:00:00.000Z",
                },
                {
                    "id": "rec-instagram-1",
                    "fields": {
                        "date": "2026-04-11",
                        "project": "everydayengel",
                        "platform": "instagram_reel",
                        "decision": "skip",
                        "serie_thema": "Schon gesetzt",
                    },
                    "createdTime": "2026-04-11T09:00:00.000Z",
                },
            ]
        }

    service = _make_service(transport)
    snapshots = service.list_today_plans(project_key="everydayengel", date="2026-04-11")

    assert tuple(snapshot.platform for snapshot in snapshots) == ("tiktok", "instagram_reel")
    assert snapshots[1].decision == "skip"
    assert snapshots[1].serie_thema == "Schon gesetzt"


def test_autofill_only_updates_missing_fields() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-1"):
            return 200, {
                "id": "rec-plan-1",
                "fields": {
                    "platform": "tiktok",
                    "decision": "pending",
                    "candidate_record_id": "rec-draft-1",
                    "serie_thema": "Schon gesetzt",
                },
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        if method == "GET" and url.endswith("/rec-draft-1"):
            return 200, {
                "id": "rec-draft-1",
                "fields": {
                    "main_point": "Kekse zur Weihnachtszeit",
                    "hook": "Hook aus Draft",
                    "cta_direction": "Rezept speichern",
                    "body": "Speichere dir das Rezept.",
                    "format": "Reel",
                    "readiness_check": "not_required",
                },
                "createdTime": "2026-04-10T08:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-1",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-1",
        siblings=(
            TodayPlanSnapshot(
                record_id="rec-other",
                decision="pending",
                platform="instagram_reel",
                serie_thema="Serie von Instagram",
                cta="CTA von heute",
            ),
        ),
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields == {
        "title_raw": "Kekse zur Weihnachtszeit",
        "hook": "Hook aus Draft",
        "cta": "Rezept speichern",
        "caption": "Speichere dir das Rezept.",
        "format_typ": "Reel",
        "bereit": "Kein Review nötig",
    }
    assert updated.serie_thema == "Schon gesetzt"
    assert updated.title_raw == "Kekse zur Weihnachtszeit"
    assert updated.hook == "Hook aus Draft"


def test_autofill_uses_sibling_values_when_candidate_field_missing() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-2"):
            return 200, {
                "id": "rec-plan-2",
                "fields": {
                    "platform": "tiktok",
                    "decision": "pending",
                    "candidate_record_id": "rec-draft-2",
                },
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        if method == "GET" and url.endswith("/rec-draft-2"):
            return 200, {
                "id": "rec-draft-2",
                "fields": {
                    "main_point": "Kekse zur Weihnachtszeit",
                    "hook": "Hook aus Draft",
                },
                "createdTime": "2026-04-10T08:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-2",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-2",
        siblings=(
            TodayPlanSnapshot(
                record_id="rec-other",
                decision="pending",
                platform="instagram_reel",
                candidate_record_id="rec-draft-2",
                serie_thema="Serie von Instagram",
                cta="CTA von heute",
                caption="Caption von heute",
                format_typ="Carousel",
                bereit="No Retry",
            ),
        ),
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields == {
        "serie_thema": "Serie von Instagram",
        "title_raw": "Kekse zur Weihnachtszeit",
        "hook": "Hook aus Draft",
        "cta": "CTA von heute",
        "caption": "Caption von heute",
        "format_typ": "Carousel",
        "bereit": "Kein Retry",
    }
    assert updated.serie_thema == "Serie von Instagram"
    assert updated.caption == "Caption von heute"
    assert updated.bereit == "Kein Retry"


def test_autofill_with_locked_row_context_skips_foreign_content_fill_but_keeps_support_fields() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-ctx"):
            return 200, {
                "id": "rec-plan-ctx",
                "fields": {
                    "platform": "youtube_short",
                    "decision": "pending",
                    "candidate_record_id": "rec-draft-ctx",
                    "caption": "julias mulias",
                },
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        if method == "GET" and url.endswith("/rec-draft-ctx"):
            return 200, {
                "id": "rec-draft-ctx",
                "fields": {
                    "main_point": "Fremder Title",
                    "hook": "Fremder Hook",
                    "cta_direction": "Fremde CTA",
                    "body": "Fremde Caption",
                    "format": "YouTube Short",
                    "readiness_check": "approved",
                },
                "createdTime": "2026-04-10T08:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-ctx",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-ctx",
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields == {
        "format_typ": "YouTube Short",
        "bereit": "Freigegeben",
    }
    assert updated.caption == "julias mulias"
    assert updated.title_raw == ""
    assert updated.hook == ""
    assert updated.cta == ""


def test_autofill_does_not_leak_sibling_values_without_shared_candidate() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-3"):
            return 200, {
                "id": "rec-plan-3",
                "fields": {
                    "platform": "youtube_short",
                    "decision": "pending",
                },
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-3",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-3",
        siblings=(
            TodayPlanSnapshot(
                record_id="rec-other",
                decision="pending",
                platform="tiktok",
                candidate_record_id="rec-draft-tiktok",
                hook="TikTok Hook",
                cta="TikTok CTA",
                caption="TikTok Caption",
                format_typ="Vertikales Video (TikTok, Reels)",
                bereit="not_required",
            ),
        ),
    )

    assert len(calls) == 1
    assert updated.hook == ""
    assert updated.cta == ""
    assert updated.caption == ""
    assert updated.format_typ == ""
    assert updated.bereit == ""


def test_autofill_without_candidate_does_not_reuse_plain_sibling_row_values() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-4"):
            return 200, {
                "id": "rec-plan-4",
                "fields": {
                    "platform": "youtube_short",
                    "decision": "pending",
                },
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-4",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-4",
        siblings=(
            TodayPlanSnapshot(
                record_id="rec-tiktok",
                decision="pending",
                platform="tiktok",
                title_raw="Titel von heute",
                hook="Hook von heute",
                cta="CTA von heute",
                caption="Caption von heute",
                bereit="No Retry",
                format_typ="Vertikales Video (TikTok, Reels)",
            ),
        ),
    )

    assert len(calls) == 1
    assert updated.title_raw == ""
    assert updated.caption == ""
    assert updated.format_typ == ""


def test_autofill_without_candidate_uses_generic_format_from_sibling_analytics() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-5"):
            return 200, {
                "id": "rec-plan-5",
                "fields": {
                    "platform": "youtube_short",
                    "decision": "pending",
                },
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        if method == "GET" and "tblAnalytics1/recAnalytics1" in url:
            return 200, {
                "id": "recAnalytics1",
                "fields": {
                    "titel": "Titel aus Analytics",
                    "caption": "Caption aus Analytics",
                    "format_typ": "Carousel",
                    "bereit": "not_required",
                },
                "createdTime": "2026-04-11T09:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-5",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-11T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-5",
        siblings=(
            TodayPlanSnapshot(
                record_id="rec-instagram",
                decision="pending",
                platform="instagram_reel",
                platform_record_id="recAnalytics1",
                platform_table_id="tblAnalytics1",
            ),
        ),
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields == {
        "title_raw": "Titel aus Analytics",
        "format_typ": "Carousel",
        "bereit": "Kein Review nötig",
    }
    assert updated.title_raw == "Titel aus Analytics"
    assert updated.caption == ""
    assert updated.format_typ == "Carousel"


def test_analytics_snapshot_caption_falls_back_to_title_raw() -> None:
    """Analytics table pre-rename: caption field is still named 'title_raw'."""
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-prerenam"):
            return 200, {
                "id": "rec-plan-prerenam",
                "fields": {
                    "platform": "tiktok",
                    "decision": "pending",
                    "platform_record_id": "recAnalyticsPreRename",
                    "platform_table_id": "tblAnalyticsPreRename",
                },
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        if method == "GET" and "tblAnalyticsPreRename/recAnalyticsPreRename" in url:
            return 200, {
                "id": "recAnalyticsPreRename",
                "fields": {
                    "serie_thema": "Gedanken/Beobachtungen",
                    "title_raw": "Caption aus Analytics (pre-rename)",
                    "bereit": "not_required",
                },
                "createdTime": "2026-04-12T09:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-prerenam",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-prerenam",
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields["serie_thema"] == "Gedanken/Beobachtungen"
    assert patch_fields["caption"] == "Caption aus Analytics (pre-rename)"
    assert patch_fields["bereit"] == "Kein Review nötig"
    assert updated.serie_thema == "Gedanken/Beobachtungen"
    assert updated.caption == "Caption aus Analytics (pre-rename)"


def test_autofill_uses_own_platform_record_analytics_values() -> None:
    """Primary record has platform_record_id/table set; analytics has serie_thema + caption."""
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-own"):
            return 200, {
                "id": "rec-plan-own",
                "fields": {
                    "platform": "tiktok",
                    "decision": "pending",
                    "platform_record_id": "recAnalyticsOwn",
                    "platform_table_id": "tblAnalyticsOwn",
                },
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        if method == "GET" and "tblAnalyticsOwn/recAnalyticsOwn" in url:
            return 200, {
                "id": "recAnalyticsOwn",
                "fields": {
                    "serie_thema": "Gedanken/Beobachtungen",
                    "titel": "Titel aus Analytics",
                    "hook_kurz": "Hook aus Analytics",
                    "cta_typ": "CTA aus Analytics",
                    "caption": "Caption aus Analytics",
                    "format_typ": "Reel",
                    "bereit": "not_required",
                },
                "createdTime": "2026-04-12T09:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-own",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-own",
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields["serie_thema"] == "Gedanken/Beobachtungen"
    assert patch_fields["caption"] == "Caption aus Analytics"
    assert patch_fields["title_raw"] == "Titel aus Analytics"
    assert patch_fields["hook"] == "Hook aus Analytics"
    assert patch_fields["cta"] == "CTA aus Analytics"
    assert patch_fields["bereit"] == "Kein Review nötig"
    assert updated.serie_thema == "Gedanken/Beobachtungen"
    assert updated.caption == "Caption aus Analytics"


def test_autofill_excludes_repeated_analytics_values_for_same_row() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-repeat"):
            return 200, {
                "id": "rec-plan-repeat",
                "fields": {
                    "platform": "youtube_short",
                    "decision": "pending",
                    "platform_record_id": "recAnalyticsRepeat",
                    "platform_table_id": "tblAnalyticsRepeat",
                },
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        if method == "GET" and "tblAnalyticsRepeat/recAnalyticsRepeat" in url:
            return 200, {
                "id": "recAnalyticsRepeat",
                "fields": {
                    "serie_thema": "Gedanken/Beobachtungen",
                    "caption": "Immer dieselbe Caption",
                    "format_typ": "YouTube Short",
                },
                "createdTime": "2026-04-12T09:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-repeat",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-repeat",
        excluded_values={
            "serie_thema": "Gedanken/Beobachtungen",
            "caption": "Immer dieselbe Caption",
        },
    )

    patch_fields = calls[-1][2]["fields"]
    assert patch_fields == {"format_typ": "YouTube Short"}
    assert updated.serie_thema == ""
    assert updated.caption == ""
    assert updated.format_typ == "YouTube Short"


def test_clear_selection_resets_only_selection_fields() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        assert method == "PATCH"
        return 200, {
            "id": "rec-plan-2",
            "fields": (body or {}).get("fields", {}),
            "createdTime": "2026-04-11T08:00:00.000Z",
        }

    service = _make_service(transport)
    updated = service.clear_selection(project_key="everydayengel", record_id="rec-plan-2")

    assert calls[0][2]["fields"] == {
        "decision": "pending",
        "serie_thema": "",
        "title_raw": "",
        "hook": "",
        "cta": "",
        "caption": "",
        "format_typ": "",
        "bereit": "",
    }
    assert updated.decision == "pending"
    assert updated.hook == ""


def test_autofill_does_not_reuse_raw_sibling_row_values_without_shared_source() -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def transport(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/rec-plan-target"):
            return 200, {
                "id": "rec-plan-target",
                "fields": {
                    "platform": "youtube_short",
                    "decision": "pending",
                },
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        if method == "PATCH":
            return 200, {
                "id": "rec-plan-target",
                "fields": (body or {}).get("fields", {}),
                "createdTime": "2026-04-12T08:00:00.000Z",
            }
        raise AssertionError(f"unexpected call: {method} {url}")

    service = _make_service(transport)
    updated = service.autofill_selection(
        project_key="everydayengel",
        record_id="rec-plan-target",
        siblings=(
            TodayPlanSnapshot(
                record_id="rec-sibling",
                decision="pending",
                platform="tiktok",
                serie_thema="Alte Generierung",
                caption="Alte Caption",
                format_typ="Vertikales Video (TikTok, Reels)",
            ),
        ),
    )

    assert len(calls) == 1
    assert updated.serie_thema == ""
    assert updated.caption == ""
    assert updated.format_typ == ""
