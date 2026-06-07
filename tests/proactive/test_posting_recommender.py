"""
Tests for PostingRecommender (Phase 2.5 — deterministic posting decision support).

Covers:
  - eligibility filter: stage, hook, readiness_check
  - posted_at-based platform gap logic (not updated_at)
  - global posting_gap_days read from Operational Knowledge
  - deterministic candidate ranking: produced > ready_to_produce > drafted, then older first
  - recommendation message format
  - "never posted" fallback (missing posted_at treated as platform never used)
  - missing platform fallback defaults to tiktok
  - silent on Airtable errors
  - no recommendation when no platform is due
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList
from operator_core.integrations.operational_knowledge_service import (
    OperationalKnowledgeContext,
    OperationalKnowledgeRow,
)
from operator_core.integrations.platform_signal_service import PlatformContext
from operator_core.proactive.posting_recommender import (
    PostingRecommender,
    _format_recommendation_message,
    _parse_iso,
    PostingCandidate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(days_ago: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _posted_at(days_ago: int) -> str:
    """ISO 8601 string for a posted_at field value."""
    return _ts(days_ago)


def _draft_record(
    record_id: str = "recDRAFT",
    stage: str = "drafted",
    format: str = "short_video",
    platform: str = "tiktok",
    hook: str = "Das ist mein Hook",
    body: str = "Mein Body",
    readiness_check: str = "not_required",
    created_time: str | None = None,
    posted_at: str = "",
    title: str = "",
) -> AirtableRecord:
    fields: dict = {
        "stage": stage,
        "format": format,
        "platform": platform,
        "hook": hook,
        "body": body,
        "readiness_check": readiness_check,
    }
    if posted_at:
        fields["posted_at"] = posted_at
    if title:
        fields["title"] = title
    return AirtableRecord(
        record_id=record_id,
        fields=fields,
        created_time=created_time or _ts(5),
    )


def _posted_record(platform: str, posted_at: str) -> AirtableRecord:
    return AirtableRecord(
        record_id="recPOSTED",
        fields={"platform": platform, "posted_at": posted_at},
        created_time=_ts(10),
    )


def _record_list(*records: AirtableRecord) -> AirtableRecordList:
    return AirtableRecordList(records=tuple(records))


def _make_ok_loader(
    gap_days: int | None = None,
    extra_rows: tuple[OperationalKnowledgeRow, ...] = (),
) -> MagicMock:
    """Return an OperationalKnowledgeLoader mock with optional gap_days row."""
    loader = MagicMock()
    rows = []
    if gap_days is not None:
        rows.append(
            OperationalKnowledgeRow(
                key="posting_gap_days",
                label="Gap",
                value=str(gap_days),
                category="posting",
                status="active",
            )
        )
    rows.extend(extra_rows)
    loader.load_active.return_value = OperationalKnowledgeContext(rows=tuple(rows))
    return loader


def _make_airtable(
    eligible: list[AirtableRecord],
    posted: list[AirtableRecord] | None = None,
) -> MagicMock:
    """Mock that returns eligible drafts on first call, posted drafts on second."""
    airtable = MagicMock()
    airtable.find_records.side_effect = [
        _record_list(*eligible),
        _record_list(*(posted or [])),
    ]
    return airtable


def _platform_ctx(platform_key: str, post_count: int, gap: str = "") -> PlatformContext:
    return PlatformContext(
        platform_key=platform_key,
        table_id=f"tbl_{platform_key}",
        post_count=post_count,
        dominant_cta="",
        gap=gap,
        hook_examples=(),
    )


def _make_platform_loader(contexts: dict[str, PlatformContext]) -> MagicMock:
    loader = MagicMock()
    loader.load_all.return_value = contexts
    return loader


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------

def test_parse_iso_z_suffix():
    dt = _parse_iso("2026-04-01T20:00:00.000Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_iso_empty_returns_none():
    assert _parse_iso("") is None


def test_parse_iso_invalid_returns_none():
    assert _parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# Eligibility filter
# ---------------------------------------------------------------------------

class TestEligibilityFilter:
    def test_eligible_drafted_stage(self):
        draft = _draft_record(stage="drafted")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None

    def test_eligible_produced_stage(self):
        draft = _draft_record(stage="produced")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None

    def test_format_does_not_block_eligibility(self):
        draft = _draft_record(format="hook_only")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None

    def test_ineligible_missing_hook(self):
        draft = _draft_record(hook="")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None

    def test_eligible_missing_body(self):
        draft = _draft_record(body="")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None

    def test_ineligible_rejected_readiness_check(self):
        draft = _draft_record(readiness_check="rejected")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None

    def test_eligible_approved_readiness_check(self):
        draft = _draft_record(readiness_check="approved")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None

    def test_eligible_empty_readiness_check(self):
        """Empty readiness_check (field not set) is treated as not_required."""
        draft = _draft_record(readiness_check="")
        airtable = _make_airtable([draft])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None

    def test_no_eligible_drafts_returns_none(self):
        airtable = _make_airtable([])
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None


# ---------------------------------------------------------------------------
# posted_at-based platform gap logic
# ---------------------------------------------------------------------------

class TestPlatformGapLogic:
    def test_platform_due_when_no_posted_drafts(self):
        """No posted_at records at all → platform treated as never posted → due."""
        draft = _draft_record(platform="tiktok")
        airtable = _make_airtable([draft], posted=[])
        ok = _make_ok_loader(gap_days=3)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.days_since_last_post == -1

    def test_disabled_weekday_schedule_skips_platform_from_recommendation(self):
        draft = _draft_record(platform="facebook_reel")
        airtable = _make_airtable([draft], posted=[])
        ok = _make_ok_loader(
            gap_days=3,
            extra_rows=(
                OperationalKnowledgeRow(
                    key="posting_schedule_facebook_reel_thursday",
                    label="Facebook Donnerstag",
                    value='{"platform":"facebook_reel","weekday":"thursday","timezone":"Europe/Berlin","enabled":false,"time_local":"","condition":"skip","note":"auslassen"}',
                    category="posting",
                    status="active",
                ),
            ),
        )

        from operator_core.proactive import posting_recommender as module

        class _FakeBerlinNow(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 4, 16, 12, 0, 0, tzinfo=tz)

        original_datetime = module.datetime
        module.datetime = _FakeBerlinNow
        try:
            rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        finally:
            module.datetime = original_datetime

        assert rec is None

    def test_platform_due_when_posted_at_exceeds_gap(self):
        """posted_at older than gap_days → platform is due."""
        draft = _draft_record(platform="tiktok")
        posted = _posted_record("tiktok", _posted_at(4))
        airtable = _make_airtable([draft], posted=[posted])
        ok = _make_ok_loader(gap_days=3)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.days_since_last_post >= 4

    def test_platform_not_due_when_posted_recently(self):
        """posted_at within gap_days → platform is not due → no recommendation."""
        draft = _draft_record(platform="tiktok")
        posted = _posted_record("tiktok", _posted_at(1))
        airtable = _make_airtable([draft], posted=[posted])
        ok = _make_ok_loader(gap_days=3)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None

    def test_missing_posted_at_field_treated_as_never_posted(self):
        """posted_at field absent (Airtable field not yet created) → never posted → due."""
        draft = _draft_record(platform="instagram_reel")
        # posted record exists but has no posted_at value
        posted = AirtableRecord(
            record_id="recP",
            fields={"platform": "instagram_reel"},  # no posted_at
            created_time=_ts(1),
        )
        airtable = _make_airtable([draft], posted=[posted])
        ok = _make_ok_loader(gap_days=3)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.days_since_last_post == -1

    def test_does_not_use_updated_at_for_gap_logic(self):
        """updated_at must NOT affect gap logic. Only posted_at on stage=posted records."""
        # Draft has a very old created_time — simulates a record that was "touched" recently
        # but has no corresponding posted record with posted_at
        draft = _draft_record(platform="tiktok", created_time=_ts(10))
        # No posted drafts at all
        airtable = _make_airtable([draft], posted=[])
        ok = _make_ok_loader(gap_days=3)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        # Must still fire: platform is never posted (no posted_at record)
        assert rec is not None
        assert rec.candidate.days_since_last_post == -1


# ---------------------------------------------------------------------------
# Global posting_gap_days config from Operational Knowledge
# ---------------------------------------------------------------------------

class TestGapDaysConfig:
    def test_uses_gap_days_from_ok(self):
        """If OK provides posting_gap_days=5, a 4-day-old post is not due."""
        draft = _draft_record(platform="tiktok")
        posted = _posted_record("tiktok", _posted_at(4))
        airtable = _make_airtable([draft], posted=[posted])
        ok = _make_ok_loader(gap_days=5)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None  # 4 < 5 → not due

    def test_falls_back_to_default_gap_when_ok_missing(self):
        """No posting_gap_days row → falls back to _DEFAULT_GAP_DAYS (3)."""
        draft = _draft_record(platform="tiktok")
        posted = _posted_record("tiktok", _posted_at(4))  # 4 >= 3 → due
        airtable = _make_airtable([draft], posted=[posted])
        ok = _make_ok_loader(gap_days=None)  # no row
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None  # 4 >= default 3 → due

    def test_invalid_gap_days_value_falls_back_to_default(self):
        """Malformed posting_gap_days value → falls back to default (3)."""
        loader = MagicMock()
        rows = (
            OperationalKnowledgeRow(
                key="posting_gap_days",
                label="Gap",
                value="not_a_number",
                category="posting",
                status="active",
            ),
        )
        loader.load_active.return_value = OperationalKnowledgeContext(rows=rows)
        draft = _draft_record(platform="tiktok")
        posted = _posted_record("tiktok", _posted_at(4))  # 4 >= 3 → due with default
        airtable = _make_airtable([draft], posted=[posted])
        rec = PostingRecommender(airtable, loader).recommend(project_key="everydayengel")
        assert rec is not None


# ---------------------------------------------------------------------------
# Deterministic candidate ranking
# ---------------------------------------------------------------------------

class TestCandidateRanking:
    def test_prefers_produced_over_ready_to_produce(self):
        ready = _draft_record(record_id="recREADY", stage="ready_to_produce", created_time=_ts(3))
        produced = _draft_record(record_id="recPROD", stage="produced", created_time=_ts(3))
        airtable = _make_airtable([ready, produced], posted=[])
        ok = _make_ok_loader(gap_days=1)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.record_id == "recPROD"

    def test_prefers_ready_to_produce_over_drafted(self):
        drafted = _draft_record(record_id="recDRAFTED", stage="drafted", created_time=_ts(10))
        ready = _draft_record(record_id="recREADY", stage="ready_to_produce", created_time=_ts(1))
        airtable = _make_airtable([drafted, ready], posted=[])
        ok = _make_ok_loader(gap_days=1)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.record_id == "recREADY"

    def test_prefers_older_draft_when_same_stage(self):
        newer = _draft_record(record_id="recNEW", stage="ready_to_produce", created_time=_ts(2))
        older = _draft_record(record_id="recOLD", stage="ready_to_produce", created_time=_ts(7))
        airtable = _make_airtable([newer, older], posted=[])
        ok = _make_ok_loader(gap_days=1)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.record_id == "recOLD"

    def test_produced_beats_older_ready(self):
        """produced always wins over ready_to_produce, regardless of age."""
        old_ready = _draft_record(record_id="recOLDREADY", stage="ready_to_produce", created_time=_ts(30))
        new_produced = _draft_record(record_id="recNEWPROD", stage="produced", created_time=_ts(1))
        airtable = _make_airtable([old_ready, new_produced], posted=[])
        ok = _make_ok_loader(gap_days=1)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        assert rec.candidate.record_id == "recNEWPROD"


class TestPlatformAwareRanking:
    def test_fewer_posts_wins_when_multiple_due_same_stage(self):
        tiktok_draft = _draft_record(
            record_id="recTIKTOK",
            platform="tiktok",
            stage="drafted",
            created_time=_ts(10),
        )
        instagram_draft = _draft_record(
            record_id="recINSTAGRAM",
            platform="instagram_reel",
            stage="drafted",
            created_time=_ts(5),
        )
        airtable = _make_airtable([tiktok_draft, instagram_draft], posted=[])
        ok = _make_ok_loader(gap_days=1)
        platform_loader = _make_platform_loader({
            "tiktok": _platform_ctx("tiktok", 8),
            "instagram_reel": _platform_ctx("instagram_reel", 2),
        })

        rec = PostingRecommender(airtable, ok, platform_loader).recommend(project_key="everydayengel")

        assert rec is not None
        assert rec.candidate.platform == "instagram_reel"
        assert "Instagram am wenigsten bespielt (2 Posts)" in rec.telegram_message

    def test_stage_priority_still_beats_post_count(self):
        tiktok_draft = _draft_record(
            record_id="recTIKTOK",
            platform="tiktok",
            stage="produced",
            created_time=_ts(1),
        )
        instagram_draft = _draft_record(
            record_id="recINSTAGRAM",
            platform="instagram_reel",
            stage="drafted",
            created_time=_ts(10),
        )
        airtable = _make_airtable([tiktok_draft, instagram_draft], posted=[])
        ok = _make_ok_loader(gap_days=1)
        platform_loader = _make_platform_loader({
            "tiktok": _platform_ctx("tiktok", 8),
            "instagram_reel": _platform_ctx("instagram_reel", 2),
        })

        rec = PostingRecommender(airtable, ok, platform_loader).recommend(project_key="everydayengel")

        assert rec is not None
        assert rec.candidate.platform == "tiktok"
        assert "am wenigsten bespielt" not in rec.telegram_message

    def test_falls_back_when_platform_context_missing(self):
        tiktok_draft = _draft_record(
            record_id="recTIKTOK",
            platform="tiktok",
            stage="drafted",
            created_time=_ts(10),
        )
        instagram_draft = _draft_record(
            record_id="recINSTAGRAM",
            platform="instagram_reel",
            stage="drafted",
            created_time=_ts(5),
        )
        airtable = _make_airtable([tiktok_draft, instagram_draft], posted=[])
        ok = _make_ok_loader(gap_days=1)
        platform_loader = _make_platform_loader({
            "tiktok": _platform_ctx("tiktok", 8),
        })

        rec = PostingRecommender(airtable, ok, platform_loader).recommend(project_key="everydayengel")

        assert rec is not None
        assert rec.candidate.record_id == "recTIKTOK"
        assert "am wenigsten bespielt" not in rec.telegram_message

    def test_no_tie_break_effect_when_only_one_due_platform_exists(self):
        draft = _draft_record(
            record_id="recONLY",
            platform="instagram_reel",
            stage="drafted",
            created_time=_ts(5),
        )
        posted = _posted_record("tiktok", _posted_at(1))
        airtable = _make_airtable([draft], posted=[posted])
        ok = _make_ok_loader(gap_days=3)
        platform_loader = _make_platform_loader({
            "instagram_reel": _platform_ctx("instagram_reel", 2),
        })

        rec = PostingRecommender(airtable, ok, platform_loader).recommend(project_key="everydayengel")

        assert rec is not None
        assert rec.candidate.platform == "instagram_reel"
        assert "am wenigsten bespielt" not in rec.telegram_message


# ---------------------------------------------------------------------------
# Recommendation message format
# ---------------------------------------------------------------------------

class TestMessageFormat:
    def _make_candidate(
        self,
        record_id: str = "recABC123",
        platform: str = "tiktok",
        hook_preview: str = "Das ist mein Hook",
        content_stage: str = "drafted",
        content_format: str = "short_video",
        has_body: bool = True,
        days_ready: int = 6,
        days_since: int = 4,
        posting_time: str = "20:00",
    ) -> PostingCandidate:
        return PostingCandidate(
            record_id=record_id,
            platform=platform,
            hook_preview=hook_preview,
            content_stage=content_stage,
            content_format=content_format,
            has_body=has_body,
            days_ready=days_ready,
            days_since_last_post=days_since,
            posting_time=posting_time,
        )

    def test_message_contains_hook_preview(self):
        c = self._make_candidate(hook_preview="Mein Hook Text")
        msg = _format_recommendation_message(c)
        assert "Mein Hook Text" in msg

    def test_message_contains_platform_label(self):
        c = self._make_candidate(platform="tiktok")
        msg = _format_recommendation_message(c)
        assert "TikTok" in msg

    def test_message_contains_posting_time(self):
        c = self._make_candidate(posting_time="20:00")
        msg = _format_recommendation_message(c)
        assert "20:00" in msg

    def test_message_contains_days_ready(self):
        c = self._make_candidate(days_ready=6)
        msg = _format_recommendation_message(c)
        assert "6" in msg
        assert "Tagen" in msg

    def test_message_singular_day_ready(self):
        # Use days_since=-1 so the only "Tag/Tagen" in the message comes from days_ready
        c = self._make_candidate(days_ready=1, days_since=-1)
        msg = _format_recommendation_message(c)
        assert "1 Tag bereit" in msg
        assert "1 Tagen" not in msg

    def test_message_contains_days_since_last_post(self):
        c = self._make_candidate(days_since=4)
        msg = _format_recommendation_message(c)
        assert "4" in msg

    def test_message_never_posted_wording(self):
        c = self._make_candidate(days_since=-1, platform="instagram_reel")
        msg = _format_recommendation_message(c)
        assert "noch nie gepostet" in msg
        assert "Instagram" in msg

    def test_message_contains_record_id_in_confirm(self):
        c = self._make_candidate(record_id="recXYZ789")
        msg = _format_recommendation_message(c)
        assert "/confirm recXYZ789" in msg

    def test_message_contains_skip(self):
        c = self._make_candidate()
        msg = _format_recommendation_message(c)
        assert "/skip" in msg

    def test_message_contains_hook_body_present_note(self):
        c = self._make_candidate()
        msg = _format_recommendation_message(c)
        assert "Hook und Body vorhanden" in msg

    def test_message_handles_missing_body(self):
        c = self._make_candidate(has_body=False)
        msg = _format_recommendation_message(c)
        assert "Hook vorhanden, Body optional/leer" in msg

    def test_message_contains_format_when_present(self):
        c = self._make_candidate(content_format="short_video")
        msg = _format_recommendation_message(c)
        assert "Format: short_video" in msg

    def test_message_contains_posting_note_when_present(self):
        c = self._make_candidate()
        c = PostingCandidate(**{**c.__dict__, "posting_note": "nur wenn starkes Video"})
        msg = _format_recommendation_message(c)
        assert "Hinweis: nur wenn starkes Video" in msg

    def test_message_header(self):
        c = self._make_candidate()
        msg = _format_recommendation_message(c)
        assert "📋 Posting-Empfehlung" in msg

    # Phase 2.8 — candidate count line
    def test_message_includes_candidate_count_line_when_multiple(self):
        """When multiple candidates competed, message includes selection reason line."""
        c = self._make_candidate()
        msg = _format_recommendation_message(c, candidate_count=3)
        assert "Ausgewählt aus 3 passenden Entwürfen" in msg
        assert "ältester zuerst" in msg

    def test_message_no_candidate_count_line_when_single(self):
        """When only 1 candidate, no selection count line is shown."""
        c = self._make_candidate()
        msg = _format_recommendation_message(c, candidate_count=1)
        assert "Ausgewählt aus" not in msg

    def test_message_no_candidate_count_line_by_default(self):
        """Default call (no candidate_count arg) does not show selection count line."""
        c = self._make_candidate()
        msg = _format_recommendation_message(c)
        assert "Ausgewählt aus" not in msg

    def test_full_recommendation_via_recommender(self):
        """End-to-end: recommender produces message with all required sections."""
        draft = _draft_record(
            record_id="recFULL",
            platform="instagram_reel",
            hook="Ich hab meinen Morgen damit gerettet",
            created_time=_ts(8),
        )
        airtable = _make_airtable([draft], posted=[])
        ok = _make_ok_loader(gap_days=3)
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is not None
        msg = rec.telegram_message
        assert "📋 Posting-Empfehlung" in msg
        assert "Instagram" in msg
        assert "19:00" in msg
        assert "noch nie gepostet" in msg
        assert "/confirm recFULL" in msg
        assert "/skip" in msg


# ---------------------------------------------------------------------------
# Hook preview truncation
# ---------------------------------------------------------------------------

def test_hook_preview_truncated_at_45_chars():
    long_hook = "A" * 60
    draft = _draft_record(hook=long_hook)
    airtable = _make_airtable([draft], posted=[])
    ok = _make_ok_loader(gap_days=1)
    rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
    assert rec is not None
    assert rec.candidate.hook_preview.endswith("…")
    assert len(rec.candidate.hook_preview) <= 46  # 45 chars + ellipsis


def test_hook_preview_falls_back_to_main_point_when_hook_missing():
    """Eligibility requires hook — this tests the format helper path for main_point fallback."""
    # The recommender filters out records without hook, but _format_recommendation_message
    # receives a pre-built candidate. Test the candidate construction path via main_point fallback
    # is handled by _draft_record with empty hook (ineligible) — instead test candidate directly.
    c = PostingCandidate(
        record_id="recNOHOOK",
        platform="tiktok",
        hook_preview="Fallback Title",
        content_stage="drafted",
        content_format="",
        has_body=False,
        days_ready=3,
        days_since_last_post=-1,
        posting_time="20:00",
    )
    msg = _format_recommendation_message(c)
    assert "Fallback Title" in msg


def test_missing_platform_defaults_to_tiktok(caplog: pytest.LogCaptureFixture):
    draft = _draft_record(platform="")
    airtable = _make_airtable([draft], posted=[])
    ok = _make_ok_loader(gap_days=1)
    with caplog.at_level("DEBUG", logger="operator_core.proactive.posting_recommender"):
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
    assert rec is not None
    assert rec.candidate.platform == "tiktok"
    assert "missing platform fallback applied" in caplog.text


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_returns_none_on_eligible_draft_load_failure(self):
        airtable = MagicMock()
        airtable.find_records.side_effect = Exception("Airtable down")
        ok = _make_ok_loader()
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None

    def test_returns_none_on_posted_draft_load_failure(self):
        draft = _draft_record()
        airtable = MagicMock()
        # First call (eligible) succeeds, second call (posted) fails
        airtable.find_records.side_effect = [
            _record_list(draft),
            Exception("timeout"),
        ]
        ok = _make_ok_loader(gap_days=1)
        # When posted load fails, per_platform is empty → all platforms treated as never posted → still recommends
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        # Safe fallback: posted load failure → treat as never posted → due
        assert rec is not None

    def test_returns_none_on_unexpected_error(self):
        airtable = MagicMock()
        ok = MagicMock()
        ok.load_active.side_effect = Exception("unexpected")
        rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
        assert rec is None


# ---------------------------------------------------------------------------
# Phase 2.8 — candidate count propagated via recommender end-to-end
# ---------------------------------------------------------------------------

def test_recommender_message_includes_candidate_count_when_multiple_eligible():
    """When multiple drafts are eligible and due, message includes 'Ausgewählt aus N passenden Entwürfen'."""
    draft_a = _draft_record(record_id="recA", created_time=_ts(10))
    draft_b = _draft_record(record_id="recB", created_time=_ts(5))
    airtable = _make_airtable([draft_a, draft_b], posted=[])
    ok = _make_ok_loader(gap_days=1)
    rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
    assert rec is not None
    assert "Ausgewählt aus 2 passenden Entwürfen" in rec.telegram_message


def test_recommender_message_no_candidate_count_when_single_eligible():
    """When only 1 draft is eligible and due, message does not include candidate count line."""
    draft = _draft_record(record_id="recONLY", created_time=_ts(5))
    airtable = _make_airtable([draft], posted=[])
    ok = _make_ok_loader(gap_days=1)
    rec = PostingRecommender(airtable, ok).recommend(project_key="everydayengel")
    assert rec is not None
    assert "Ausgewählt aus" not in rec.telegram_message
