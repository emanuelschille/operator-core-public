from pathlib import Path

from operator_core.bootstrap import BootstrapContext
from operator_core.config import (
    AirtableSettings,
    AppSettings,
    OpenAISettings,
    Settings,
    TelegramSettings,
)
from operator_core.core.backbone.event_log_service import EventLogService
from operator_core.core.backbone.execution_service import ExecutionService
from operator_core.core.backbone.job_service import JobService
from operator_core.core.backbone.repositories import (
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryRunRepository,
)
from operator_core.core.content_ops.models import ContentOpResult
from operator_core.core.content_ops.proposal_store import ContentProposal, ContentProposalStore
from operator_core.core.request_flow.daily_plan_message_store import DailyPlanMessageStore
from operator_core.core.backbone.run_service import RunService
from operator_core.core.request_flow.service import RequestFlowService
from operator_core.integrations.daily_plan_service import TodayPlanSnapshot
from operator_core.interfaces.telegram.entry_flow import build_telegram_entry_handoff
from operator_core.proactive.plan_reminder_store import PlanReminderStore


def _bootstrap() -> BootstrapContext:
    settings = Settings(
        app=AppSettings(
            env="test",
            log_level="INFO",
            runtime_mode="service",
            active_project="everydayengel",
        ),
        telegram=TelegramSettings(enabled=False, bot_token="", allowed_user_ids=(), allowed_chat_ids=()),
        airtable=AirtableSettings(enabled=False, api_key="", project_base_ids={"everydayengel": ""}),
        openai=OpenAISettings(
            enabled=False,
            api_key="",
            model="gpt-5",
            base_url="https://api.openai.com/v1",
            timeout_seconds=30,
        ),
    )
    return BootstrapContext(
        settings=settings,
        runtime_path=Path("projects/everydayengel/runtime.yaml"),
        project_runtime={
            "project_key": "everydayengel",
            "display_name": "Everyday Engel",
            "status": "active",
            "primary_interface": "telegram",
            "human_in_the_loop": "true",
        },
    )


class _DailyPlanStub:
    def __init__(self) -> None:
        self.rows = {
            "rec-tiktok": TodayPlanSnapshot(
                record_id="rec-tiktok",
                decision="pending",
                platform="tiktok",
                candidate_record_id="rec-draft-tiktok",
            ),
            "rec-instagram": TodayPlanSnapshot(
                record_id="rec-instagram",
                decision="pending",
                platform="instagram_reel",
            ),
            "rec-facebook": TodayPlanSnapshot(
                record_id="rec-facebook",
                decision="pending",
                platform="facebook_reel",
            ),
            "rec-youtube": TodayPlanSnapshot(
                record_id="rec-youtube",
                decision="pending",
                platform="youtube_short",
            ),
        }
        self.upsert_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.autofill_calls: list[dict] = []
        self.clear_calls: list[dict] = []
        self.patch_calls: list[dict] = []

    def list_today_plans(self, *, project_key: str, date: str) -> tuple[TodayPlanSnapshot, ...]:
        return tuple(self.rows.values())

    def get_plan_record(self, *, project_key: str, record_id: str) -> TodayPlanSnapshot:
        return self.rows[record_id]

    def upsert_plan(self, **kwargs: object) -> str:
        self.upsert_calls.append(dict(kwargs))
        platform = str(kwargs.get("platform"))
        return {
            "tiktok": "rec-tiktok",
            "instagram_reel": "rec-instagram",
            "facebook_reel": "rec-facebook",
            "youtube_short": "rec-youtube",
        }[platform]

    def update_decision(self, **kwargs: object) -> None:
        self.update_calls.append(dict(kwargs))
        record_id = str(kwargs["record_id"])
        current = self.rows[record_id]
        self.rows[record_id] = TodayPlanSnapshot(
            **{**current.__dict__, "decision": str(kwargs["decision"])}
        )

    def autofill_selection(
        self,
        *,
        project_key: str,
        record_id: str,
        siblings: tuple[TodayPlanSnapshot, ...] = (),
        excluded_values: dict[str, str] | None = None,
    ) -> TodayPlanSnapshot:
        self.autofill_calls.append(
            {
                "project_key": project_key,
                "record_id": record_id,
                "siblings": siblings,
                "excluded_values": excluded_values or {},
            }
        )
        current = self.rows[record_id]
        updated = TodayPlanSnapshot(
            **{
                **current.__dict__,
                "serie_thema": current.serie_thema or "Wintergebäck",
                "title_raw": current.title_raw or "Kekse zur Weihnachtszeit",
                "hook": current.hook or "Warum Kekse an Weihnachten mehr sind als nur Süßes",
                "cta": current.cta or "Rezept speichern",
                "caption": current.caption or "Speichere dir das Rezept für die Feiertage.",
                "format_typ": current.format_typ or "Reel",
                "bereit": current.bereit or "bereit",
            }
        )
        self.rows[record_id] = updated
        return updated

    def clear_selection(self, *, project_key: str, record_id: str) -> TodayPlanSnapshot:
        self.clear_calls.append({"project_key": project_key, "record_id": record_id})
        current = self.rows[record_id]
        updated = TodayPlanSnapshot(
            **{
                **current.__dict__,
                "decision": "pending",
                "serie_thema": "",
                "title_raw": "",
                "hook": "",
                "cta": "",
                "caption": "",
                "format_typ": "",
                "bereit": "",
            }
        )
        self.rows[record_id] = updated
        return updated

    def patch_fields(
        self,
        *,
        project_key: str,
        record_id: str,
        fields: dict[str, str],
        current: TodayPlanSnapshot,
    ) -> TodayPlanSnapshot:
        self.patch_calls.append(
            {
                "project_key": project_key,
                "record_id": record_id,
                "fields": dict(fields),
            }
        )
        updated = TodayPlanSnapshot(**{**current.__dict__, **fields})
        self.rows[record_id] = updated
        return updated


class _GenerationStub:
    def __init__(self, exclusions: dict[str, str] | None = None) -> None:
        self.exclusions = exclusions or {}
        self.exclusion_calls: list[dict[str, str]] = []
        self.fill_calls: list[dict[str, object]] = []

    def get_non_repetition_exclusions(self, *, record_id: str) -> dict[str, str]:
        self.exclusion_calls.append({"record_id": record_id})
        return dict(self.exclusions)

    def fill_missing_fields(
        self,
        *,
        project_key: str,
        snapshot: TodayPlanSnapshot,
        siblings: tuple[TodayPlanSnapshot, ...] = (),
    ) -> TodayPlanSnapshot:
        self.fill_calls.append(
            {"project_key": project_key, "record_id": snapshot.record_id, "siblings": siblings}
        )
        return snapshot


def _build_service(
    daily_plan_service: object,
    *,
    plan_reminder_store: PlanReminderStore | None = None,
    daily_plan_generation_service: object | None = None,
    content_ops_service: object | None = None,
    content_proposal_store: ContentProposalStore | None = None,
    daily_plan_message_store: DailyPlanMessageStore | None = None,
) -> RequestFlowService:
    execution_service = ExecutionService(
        job_service=JobService(InMemoryJobRepository()),
        run_service=RunService(InMemoryRunRepository()),
        event_log_service=EventLogService(InMemoryEventRepository()),
    )
    if content_ops_service is not None:
        execution_service.content_ops_service = content_ops_service  # type: ignore[attr-defined]
    return RequestFlowService(
        execution_service,
        daily_plan_service=daily_plan_service,  # type: ignore[arg-type]
        plan_reminder_store=plan_reminder_store,
        daily_plan_generation_service=daily_plan_generation_service,  # type: ignore[arg-type]
        content_proposal_store=content_proposal_store,
        daily_plan_message_store=daily_plan_message_store,
    )


def _plan_handoff() -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 1000,
            "message": {
                "message_id": 2000,
                "text": "/plan_demo",
                "chat": {"id": 3000, "type": "private"},
                "from": {"id": 4000, "username": "julia"},
            },
        },
        _bootstrap(),
    )


def _callback_handoff(callback_data: str) -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 1001,
            "callback_query": {
                "id": "cbq-plan",
                "from": {"id": 4000, "username": "julia"},
                "data": callback_data,
                "message": {
                    "message_id": 2001,
                    "text": "📋 Tagesplan · TikTok",
                    "chat": {"id": 3000, "type": "private"},
                },
            },
        },
        _bootstrap(),
    )


def _reply_handoff(text: str, *, reply_text: str = "📋 Tagesplan · YouTube") -> object:
    return build_telegram_entry_handoff(
        {
            "update_id": 1002,
            "message": {
                "message_id": 2002,
                "text": text,
                "chat": {"id": 3000, "type": "private"},
                "from": {"id": 4000, "username": "julia"},
                "reply_to_message": {
                    "message_id": 1999,
                    "text": reply_text,
                    "chat": {"id": 3000, "type": "private"},
                    "from": {"id": 4000, "username": "julia"},
                },
            },
        },
        _bootstrap(),
    )


class _ContentOpsStub:
    def __init__(self, *, items: tuple[str, ...]) -> None:
        self.items = items
        self.calls: list[dict[str, object]] = []

    def follow_up(self, **kwargs: object) -> ContentOpResult:
        self.calls.append(dict(kwargs))
        return ContentOpResult(
            lane_name="content_ops",
            project_key="everydayengel",
            action_type="followup",
            command_body=str(kwargs.get("instruction") or ""),
            title="Follow-up",
            summary="Vorschlag aktualisiert.",
            items=self.items,
            openai_used=True,
            platform="youtube_short",
        )


def test_plan_demo_returns_four_platform_messages() -> None:
    service = _build_service(_DailyPlanStub())
    result = service.handle_telegram_entry_handoff(_plan_handoff())

    assert result.decision == "plan_demo"
    assert "📋 Tagesplan · TikTok" in result.formatter_payload.message_text
    assert result.formatter_payload.response_reply_markup is not None
    assert result.formatter_payload.response_reply_markup["inline_keyboard"][0][0]["callback_data"] == (
        "plan_demo:skip_today:rec-tiktok"
    )
    assert len(result.formatter_payload.additional_messages) == 3
    assert result.formatter_payload.additional_messages[0].text.startswith("📋 Tagesplan · Instagram")
    assert result.formatter_payload.additional_messages[1].text.startswith("📋 Tagesplan · Facebook")
    assert result.formatter_payload.additional_messages[2].text.startswith("📋 Tagesplan · YouTube")


def test_skip_updates_only_current_platform_record() -> None:
    stub = _DailyPlanStub()
    service = _build_service(stub)

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:skip_today:rec-instagram")
    )

    assert result.decision == "plan_demo_callback"
    assert stub.update_calls == [
        {"project_key": "everydayengel", "record_id": "rec-instagram", "decision": "skip"}
    ]
    assert "Status: heute auslassen" in (result.formatter_payload.edit_message_text or "")
    assert "Instagram" in (result.formatter_payload.edit_message_text or "")


def test_autofill_populates_only_missing_fields() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-tiktok"] = TodayPlanSnapshot(
        record_id="rec-tiktok",
        decision="pending",
        platform="tiktok",
        serie_thema="Schon gesetzt",
        candidate_record_id="rec-draft-tiktok",
    )
    service = _build_service(stub)

    result = service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:auto_fill:rec-tiktok"))

    assert result.decision == "plan_demo_callback"
    assert len(stub.autofill_calls) == 1
    text = result.formatter_payload.edit_message_text or ""
    assert "Serie/Thema: Schon gesetzt" in text
    assert "Title: Kekse zur Weihnachtszeit" in text
    assert "Hook: Warum Kekse an Weihnachten mehr sind als nur Süßes" in text
    assert "CTA: Rezept speichern" in text
    assert "Caption: Speichere dir das Rezept für die Feiertage." in text
    assert "Format: Reel" in text
    assert "Bereit: bereit" in text


def test_autofill_passes_non_repetition_exclusions_before_source_fill() -> None:
    stub = _DailyPlanStub()
    gen = _GenerationStub({"serie_thema": "Schon mal generiert", "caption": "Alte Caption"})
    service = _build_service(stub, daily_plan_generation_service=gen)

    service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:auto_fill:rec-youtube"))

    assert gen.exclusion_calls == [{"record_id": "rec-youtube"}]
    assert stub.autofill_calls[0]["excluded_values"] == {
        "serie_thema": "Schon mal generiert",
        "caption": "Alte Caption",
    }


def test_platform_plan_text_normalizes_internal_bereit_value() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-tiktok"] = TodayPlanSnapshot(
        record_id="rec-tiktok",
        decision="pending",
        platform="tiktok",
        bereit="not_required",
    )
    service = _build_service(stub)

    result = service.handle_telegram_entry_handoff(_callback_handoff("plan_demo:auto_fill:rec-tiktok"))

    text = result.formatter_payload.edit_message_text or ""
    assert "Bereit: Kein Review nötig" in text
    assert "Bereit: not_required" not in text


def test_remind_15m_schedules_platform_scoped_reminder() -> None:
    stub = _DailyPlanStub()
    store = PlanReminderStore()
    service = _build_service(stub, plan_reminder_store=store)

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:remind_15m:rec-instagram")
    )

    assert result.decision == "plan_demo_callback"
    text = result.formatter_payload.edit_message_text or ""
    assert "Instagram" in text
    assert "⏰ Erinnerung in 15 Min. gesetzt." in text
    assert store.size() == 1
    reminder = next(iter(store._store.values()))
    assert reminder.key == "remind_15m:rec-instagram"
    assert reminder.platform == "instagram_reel"
    assert reminder.record_id == "rec-instagram"
    assert reminder.reminder_type == "remind_15m"


def test_clear_selection_resets_only_current_platform_fields() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-facebook"] = TodayPlanSnapshot(
        record_id="rec-facebook",
        decision="skip",
        platform="facebook_reel",
        serie_thema="X",
        title_raw="Y",
        hook="Y",
        cta="Z",
        caption="C",
        format_typ="Carousel",
        bereit="bereit",
    )
    service = _build_service(stub)

    result = service.handle_telegram_entry_handoff(
        _callback_handoff("plan_demo:clear_selection:rec-facebook")
    )

    assert result.decision == "plan_demo_callback"
    assert stub.clear_calls == [{"project_key": "everydayengel", "record_id": "rec-facebook"}]
    text = result.formatter_payload.edit_message_text or ""
    assert "Facebook" in text
    assert "Status: offen" in text
    assert "Serie/Thema: —" in text
    assert "Title: —" in text
    assert "Hook: —" in text
    assert "CTA: —" in text
    assert "Caption: —" in text


def test_reply_on_daily_plan_literal_edit_updates_only_target_field_and_keeps_plan_buttons() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-youtube"] = TodayPlanSnapshot(
        record_id="rec-youtube",
        decision="pending",
        platform="youtube_short",
        serie_thema="Ruhiger Morgen",
        title_raw="Alter Title",
        hook="Alter Hook",
        cta="Alter CTA",
        caption="Alte Caption",
        format_typ="Short",
        bereit="bereit",
    )
    proposal_store = ContentProposalStore()
    proposal_store.save(
        ContentProposal(
            proposal_id="prop-active",
            project_key="everydayengel",
            action_type="caption",
            platform="youtube_short",
            fields={"caption": "Aktiver Vorschlag"},
            chat_id="3000",
            user_id="4000",
        )
    )
    service = _build_service(stub, content_proposal_store=proposal_store)

    result = service.handle_telegram_entry_handoff(_reply_handoff("Änder cta zu: clown"))

    assert result.decision == "plan_demo_callback"
    assert stub.patch_calls == [
        {
            "project_key": "everydayengel",
            "record_id": "rec-youtube",
            "fields": {"cta": "clown"},
        }
    ]
    assert proposal_store.active_for(chat_id="3000", user_id="4000") is not None
    assert result.formatter_payload.message_text.startswith("📋 Tagesplan · YouTube")
    assert "CTA: clown" in result.formatter_payload.message_text
    assert "Title: Alter Title" in result.formatter_payload.message_text
    reply_markup = result.formatter_payload.response_reply_markup or {}
    inline_keyboard = reply_markup.get("inline_keyboard") or []
    button_texts = [button["text"] for row in inline_keyboard for button in row]
    assert "⏭ Heute auslassen" in button_texts
    assert "🪄 Automatisch ergänzen" in button_texts
    assert "🔄 Ersetzen" in button_texts
    assert "⬆️ Upload in Airtable" in button_texts


def test_reply_on_daily_plan_uses_message_id_store_even_without_matching_reply_text() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-youtube"] = TodayPlanSnapshot(
        record_id="rec-youtube",
        decision="pending",
        platform="youtube_short",
        cta="Alter CTA",
    )
    store = DailyPlanMessageStore()
    store.put(chat_id=3000, message_id=1999, record_id="rec-youtube")
    service = _build_service(stub, daily_plan_message_store=store)

    result = service.handle_telegram_entry_handoff(
        _reply_handoff("Änder cta zu: opa", reply_text="irgendein anderer reply-text")
    )

    assert result.decision == "plan_demo_callback"
    assert stub.patch_calls == [
        {
            "project_key": "everydayengel",
            "record_id": "rec-youtube",
            "fields": {"cta": "opa"},
        }
    ]
    assert "CTA: opa" in result.formatter_payload.message_text


def test_reply_on_daily_plan_uses_reply_markup_record_id_when_store_is_empty() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-youtube"] = TodayPlanSnapshot(
        record_id="rec-youtube",
        decision="pending",
        platform="youtube_short",
        cta="Alter CTA",
    )
    service = _build_service(stub)
    handoff = build_telegram_entry_handoff(
        {
            "update_id": 1003,
            "message": {
                "message_id": 2003,
                "text": "Ersetz cta in opa",
                "chat": {"id": 3000, "type": "private"},
                "from": {"id": 4000, "username": "julia"},
                "reply_to_message": {
                    "message_id": 1999,
                    "text": "",
                    "chat": {"id": 3000, "type": "private"},
                    "from": {"id": 4000, "username": "julia"},
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {"text": "⏭ Heute auslassen", "callback_data": "plan_demo:skip_today:rec-youtube"},
                            ]
                        ]
                    },
                },
            },
        },
        _bootstrap(),
    )

    result = service.handle_telegram_entry_handoff(handoff)

    assert result.decision == "plan_demo_callback"
    assert stub.patch_calls == [
        {
            "project_key": "everydayengel",
            "record_id": "rec-youtube",
            "fields": {"cta": "opa"},
        }
    ]
    assert "CTA: opa" in result.formatter_payload.message_text


def test_reply_on_daily_plan_soft_edit_revises_only_requested_field() -> None:
    stub = _DailyPlanStub()
    stub.rows["rec-youtube"] = TodayPlanSnapshot(
        record_id="rec-youtube",
        decision="pending",
        platform="youtube_short",
        serie_thema="Ruhiger Morgen",
        title_raw="Alter Title",
        hook="Alter Hook",
        cta="Mach mit",
        caption="Alte Caption",
        format_typ="Short",
        bereit="bereit",
    )
    content_ops = _ContentOpsStub(items=("CTA: Starte entspannt in den Morgen",))
    service = _build_service(stub, content_ops_service=content_ops)

    result = service.handle_telegram_entry_handoff(_reply_handoff("Mach cta einfacher"))

    assert result.decision == "plan_demo_callback"
    assert stub.patch_calls == [
        {
            "project_key": "everydayengel",
            "record_id": "rec-youtube",
            "fields": {"cta": "Starte entspannt in den Morgen"},
        }
    ]
    assert len(content_ops.calls) == 1
    proposal = content_ops.calls[0]["proposal"]
    assert isinstance(proposal, ContentProposal)
    assert proposal.fields["hook"] == "Alter Hook"
    assert proposal.fields["caption"] == "Alte Caption"
    assert "Bearbeite nur das Feld CTA" in str(content_ops.calls[0]["instruction"])
    assert "CTA: Starte entspannt in den Morgen" in result.formatter_payload.message_text
    assert "Hook: Alter Hook" in result.formatter_payload.message_text


def test_reply_on_daily_plan_unrecognized_text_does_not_fall_back_to_free_text_flow() -> None:
    stub = _DailyPlanStub()
    service = _build_service(stub)

    result = service.handle_telegram_entry_handoff(_reply_handoff("Irgendwas ohne Feldanweisung"))

    assert result.decision == "not_a_command"
    assert "Bitte als Feldanweisung auf den Tagesplan antworten" in result.formatter_payload.message_text
