"""
Correction capture and commercial classification for everydayengel content outputs.

Grounded in:
  docs/everydayengel/correction_capture_taxonomy.md
  docs/everydayengel/content_commercial_classification.md
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Enums — locked taxonomy, must not accept free-form strings
# ---------------------------------------------------------------------------

class CorrectionStatus(str, Enum):
    accepted_as_is = "accepted_as_is"
    accepted_with_edits = "accepted_with_edits"
    rejected = "rejected"


class CorrectionReasonTag(str, Enum):
    """Reason tag for a correction.

    Use 'none' for accepted_as_is (no rejection reason needed).
    For rejected/accepted_with_edits, pick the most specific label.
    """
    none = "none"
    too_literal = "too_literal"
    too_free = "too_free"
    moment_missed = "moment_missed"
    tone_off = "tone_off"
    not_julia = "not_julia"
    too_broad = "too_broad"
    too_loud = "too_loud"
    too_producty = "too_producty"
    weak_hook = "weak_hook"
    weak_clarity = "weak_clarity"
    good_but_wrong_platform = "good_but_wrong_platform"


class CommercialClass(str, Enum):
    """Commercial class for a content output.

    Grounded in docs/everydayengel/content_commercial_classification.md.
    trust_building is the majority of volume for /idea.
    product_near is the bridge — should be consistently present.
    recommendation_ready and direct_offer are rare and must feel earned.
    off_thesis_or_monetization_waste should be zero.
    """
    trust_building = "trust_building"
    product_near = "product_near"
    recommendation_ready = "recommendation_ready"
    direct_offer = "direct_offer"
    off_thesis_or_monetization_waste = "off_thesis_or_monetization_waste"


# ---------------------------------------------------------------------------
# Correction record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CorrectionRecord:
    """Structured capture of one human correction on a bot output.

    Fields mirror docs/everydayengel/correction_capture_taxonomy.md.
    """
    record_id: str
    project_key: str
    action_type: str
    proposal_id: str
    prompt: str
    bot_output: str
    status: CorrectionStatus
    commercial_class: CommercialClass | None = None
    reason_tag: CorrectionReasonTag = CorrectionReasonTag.none
    corrected_output: str | None = None
    supersedes_record_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_snapshot(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "project_key": self.project_key,
            "action_type": self.action_type,
            "proposal_id": self.proposal_id,
            "prompt": self.prompt,
            "bot_output": self.bot_output,
            "status": self.status.value,
            "commercial_class": self.commercial_class.value if self.commercial_class else None,
            "reason_tag": self.reason_tag.value,
            "corrected_output": self.corrected_output,
            "supersedes_record_id": self.supersedes_record_id,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Store — in-memory, same pattern as ContentProposalStore
# ---------------------------------------------------------------------------

class CorrectionCaptureStore:
    """In-memory session-scoped index for correction records.

    Secondary cache only — CorrectionFileRepository is the source of truth
    when a file path is configured.
    Keys: record_id. Secondary index: project_key.
    """

    def __init__(self) -> None:
        self._records: dict[str, CorrectionRecord] = {}

    def record(self, correction: CorrectionRecord) -> None:
        self._records[correction.record_id] = correction

    def get(self, record_id: str) -> CorrectionRecord | None:
        return self._records.get(record_id)

    def list_by_project(self, project_key: str) -> list[CorrectionRecord]:
        return [r for r in self._records.values() if r.project_key == project_key]

    def list_by_action(self, project_key: str, action_type: str) -> list[CorrectionRecord]:
        return [
            r for r in self._records.values()
            if r.project_key == project_key and r.action_type == action_type
        ]

    def latest_for_proposal(self, project_key: str, proposal_id: str) -> CorrectionRecord | None:
        matches = [
            r for r in self._records.values()
            if r.project_key == project_key and r.proposal_id == proposal_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda r: r.created_at)

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# CorrectionFileRepository — durable file-backed append-only store
# ---------------------------------------------------------------------------

class CorrectionFileRepository:
    """Thread-safe file-backed repository for CorrectionRecord objects.

    Follows the same pattern as PlanReminderStore / DailyPlanScheduleStore.
    Loads existing records on init; appends and rewrites the JSON file on
    every write. Survives process restarts.

    file_path=None → in-memory only (test / no-file mode).
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._file_path = Path(file_path) if file_path is not None else None
        self._lock = threading.Lock()
        self._records: dict[str, CorrectionRecord] = {}
        self._load()

    def append(self, correction: CorrectionRecord) -> None:
        with self._lock:
            self._records[correction.record_id] = correction
            self._save_locked()

    def get(self, record_id: str) -> CorrectionRecord | None:
        with self._lock:
            return self._records.get(record_id)

    def list_by_project(self, project_key: str) -> list[CorrectionRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.project_key == project_key]

    def list_by_action(self, project_key: str, action_type: str) -> list[CorrectionRecord]:
        with self._lock:
            return [
                r for r in self._records.values()
                if r.project_key == project_key and r.action_type == action_type
            ]

    def latest_for_proposal(self, project_key: str, proposal_id: str) -> CorrectionRecord | None:
        with self._lock:
            matches = [
                r for r in self._records.values()
                if r.project_key == project_key and r.proposal_id == proposal_id
            ]
            if not matches:
                return None
            return max(matches, key=lambda r: r.created_at)

    def latest_effective_by_action(
        self,
        *,
        project_key: str,
        action_type: str,
        limit: int = 50,
    ) -> tuple[CorrectionRecord, ...]:
        """Return latest correction per proposal, newest first."""
        with self._lock:
            latest_by_proposal: dict[str, CorrectionRecord] = {}
            for record in self._records.values():
                if record.project_key != project_key or record.action_type != action_type:
                    continue
                key = record.proposal_id or record.record_id
                current = latest_by_proposal.get(key)
                if current is None or record.created_at > current.created_at:
                    latest_by_proposal[key] = record
            records = sorted(latest_by_proposal.values(), key=lambda r: r.created_at, reverse=True)
            return tuple(records[:limit])

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def _load(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return
        loaded: dict[str, CorrectionRecord] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                record = _correction_record_from_dict(item)
            except (KeyError, ValueError, TypeError):
                continue
            if record.record_id:
                loaded[record.record_id] = record
        self._records = loaded

    def _save_locked(self) -> None:
        if self._file_path is None:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            r.to_snapshot()
            for r in sorted(self._records.values(), key=lambda r: r.created_at.isoformat())
        ]
        self._file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _correction_record_from_dict(item: dict) -> CorrectionRecord:
    """Deserialise a snapshot dict back to a CorrectionRecord."""
    commercial_raw = item.get("commercial_class")
    comm_class: CommercialClass | None = None
    if commercial_raw:
        try:
            comm_class = CommercialClass(commercial_raw)
        except ValueError:
            pass

    reason_raw = item.get("reason_tag") or "none"
    try:
        reason_tag = CorrectionReasonTag(reason_raw)
    except ValueError:
        reason_tag = CorrectionReasonTag.none

    created_at_raw = item.get("created_at") or ""
    try:
        created_at = datetime.fromisoformat(created_at_raw).astimezone(timezone.utc)
    except (ValueError, TypeError):
        created_at = datetime.now(timezone.utc)

    return CorrectionRecord(
        record_id=str(item["record_id"]),
        project_key=str(item["project_key"]),
        action_type=str(item["action_type"]),
        proposal_id=str(item.get("proposal_id") or ""),
        prompt=str(item.get("prompt") or ""),
        bot_output=str(item.get("bot_output") or ""),
        status=CorrectionStatus(item["status"]),
        commercial_class=comm_class,
        reason_tag=reason_tag,
        corrected_output=item.get("corrected_output"),
        supersedes_record_id=item.get("supersedes_record_id"),
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Commercial classifier — deterministic, rule-based
# ---------------------------------------------------------------------------

# Direct-offer markers: explicit sponsorship / code / paid signals
_DIRECT_OFFER_SIGNALS: frozenset[str] = frozenset({
    "anzeige",
    "kooperation",
    "werbung",
    "rabatt",
    "% auf",
    "code ",
    "affiliate",
    "gesponsert",
    "bezahlte",
})

# Recommendation-ready markers: personal product name + approval phrasing
_RECOMMENDATION_SIGNALS: frozenset[str] = frozenset({
    "echt geholfen",
    "macht einen echten unterschied",
    "macht wirklich einen unterschied",
    "seit ssw",
    "wirklich geholfen",
    "total empfehlen",
    "kann ich nur empfehlen",
    "ich empfehle",
})

# Product-near markers: friction + implicit product solution (no explicit product name)
_PRODUCT_NEAR_SIGNALS: frozenset[str] = frozenset({
    "kissen",
    "sitzmöglichkeit",
    "stütze",
    "stützen",
    "unterstützung",
    "erleichtert mir",
    "macht es leichter",
    "hilft mir dabei",
    "bräuchte ich",
    "wäre hilfreich",
    "ich brauche etwas",
    "brauche jetzt etwas",
    "würde mir helfen",
    "ohne das",
})


# ---------------------------------------------------------------------------
# CommercialClassLog — lightweight append-only generation log
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommercialLogEntry:
    """One generation event tagged with its commercial class.

    Stored independently from corrections — captures every generation at
    creation time, not only when a user reacts via ✅/❌.
    """
    record_id: str
    project_key: str
    action_type: str
    platform: str
    commercial_class: CommercialClass
    prompt_excerpt: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_snapshot(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "project_key": self.project_key,
            "action_type": self.action_type,
            "platform": self.platform,
            "commercial_class": self.commercial_class.value,
            "prompt_excerpt": self.prompt_excerpt,
            "created_at": self.created_at.isoformat(),
        }


class CommercialClassLog:
    """Thread-safe, file-backed, append-only log for commercial class generation events.

    file_path=None → in-memory only (test / no-file mode).
    Stored at .runtime/commercial_class_log.json in production.
    """

    def __init__(self, file_path: "str | Path | None" = None) -> None:
        self._file_path = Path(file_path) if file_path is not None else None
        self._lock = threading.Lock()
        self._entries: dict[str, CommercialLogEntry] = {}
        self._load()

    def append(self, entry: CommercialLogEntry) -> None:
        with self._lock:
            self._entries[entry.record_id] = entry
            self._save_locked()

    def list_by_project(self, project_key: str, since_days: int = 7) -> list[CommercialLogEntry]:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        with self._lock:
            return [
                e for e in self._entries.values()
                if e.project_key == project_key and e.created_at >= cutoff
            ]

    def list_all_by_project(self, project_key: str) -> list[CommercialLogEntry]:
        with self._lock:
            return [e for e in self._entries.values() if e.project_key == project_key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _load(self) -> None:
        if self._file_path is None or not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return
        loaded: dict[str, CommercialLogEntry] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entry = _commercial_log_entry_from_dict(item)
            except (KeyError, ValueError, TypeError):
                continue
            if entry.record_id:
                loaded[entry.record_id] = entry
        self._entries = loaded

    def _save_locked(self) -> None:
        if self._file_path is None:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            e.to_snapshot()
            for e in sorted(self._entries.values(), key=lambda e: e.created_at.isoformat())
        ]
        self._file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _commercial_log_entry_from_dict(item: dict) -> CommercialLogEntry:
    comm_class = CommercialClass(item["commercial_class"])
    created_at_raw = item.get("created_at") or ""
    try:
        created_at = datetime.fromisoformat(created_at_raw).astimezone(timezone.utc)
    except (ValueError, TypeError):
        created_at = datetime.now(timezone.utc)
    return CommercialLogEntry(
        record_id=str(item["record_id"]),
        project_key=str(item["project_key"]),
        action_type=str(item["action_type"]),
        platform=str(item.get("platform") or ""),
        commercial_class=comm_class,
        prompt_excerpt=str(item.get("prompt_excerpt") or ""),
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# CommercialMixSummary — weekly mix readout
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommercialMixSummary:
    """Aggregated commercial-class counts for a recent time window.

    drift_warning is True when zero product_near-or-above items exist in the
    window — invisible drift into pure trust_building, per the weekly mix
    principle in docs/everydayengel/content_commercial_classification.md.
    """
    window_days: int
    total: int
    trust_building: int
    product_near: int
    recommendation_ready: int
    direct_offer: int
    off_thesis_or_monetization_waste: int
    drift_warning: bool
    drift_hint: str | None = None

    def format_text(self) -> str:
        lines = [
            f"📊 Commercial Mix – letzte {self.window_days} Tage ({self.total} Vorschläge)",
            f"• Vertrauensaufbau: {self.trust_building}",
            f"• Produktnah: {self.product_near}",
            f"• Empfehlungsbereit: {self.recommendation_ready}",
            f"• Direktes Angebot: {self.direct_offer}",
            f"• Nicht passend: {self.off_thesis_or_monetization_waste}",
        ]
        if self.drift_hint:
            lines.append(f"⚠️ Drift-Warnung: {self.drift_hint}")
        elif self.drift_warning:
            lines.append("⚠️ Drift-Warnung: Kein produktnaher oder höherer Content in diesem Zeitraum.")
        return "\n".join(lines)


def summarize_commercial_mix(
    log: CommercialClassLog,
    project_key: str,
    window_days: int = 7,
) -> CommercialMixSummary:
    """Return commercial class counts for entries within the last window_days."""
    entries = log.list_by_project(project_key, since_days=window_days)
    counts: dict[str, int] = {c.value: 0 for c in CommercialClass}
    for e in entries:
        counts[e.commercial_class.value] = counts.get(e.commercial_class.value, 0) + 1
    pn = counts.get(CommercialClass.product_near.value, 0)
    rr = counts.get(CommercialClass.recommendation_ready.value, 0)
    do = counts.get(CommercialClass.direct_offer.value, 0)
    total = len(entries)
    drift_hint = _detect_commercial_mix_drift_hint(
        total=total,
        trust_building=counts.get(CommercialClass.trust_building.value, 0),
        product_near=pn,
        recommendation_ready=rr,
        direct_offer=do,
    )
    return CommercialMixSummary(
        window_days=window_days,
        total=total,
        trust_building=counts.get(CommercialClass.trust_building.value, 0),
        product_near=pn,
        recommendation_ready=rr,
        direct_offer=do,
        off_thesis_or_monetization_waste=counts.get(
            CommercialClass.off_thesis_or_monetization_waste.value, 0
        ),
        drift_warning=(pn + rr + do) == 0,
        drift_hint=drift_hint,
    )


def _detect_commercial_mix_drift_hint(
    *,
    total: int,
    trust_building: int,
    product_near: int,
    recommendation_ready: int,
    direct_offer: int,
) -> str | None:
    if total <= 0:
        return None
    if total >= 4 and trust_building / total >= 0.75:
        return "Der Content ist gerade sehr vertrauenslastig. Das ist gut für den Aufbau, aber bald sollten wieder 1–2 produktnähere Ideen dazukommen."
    if total >= 4 and product_near == 0:
        return "Es fehlen aktuell produktnahe Inhalte. Streue bald wieder eine Idee ein, die ein Problem oder Bedürfnis subtil anspricht."
    if total >= 6 and recommendation_ready == 0:
        return "Seit einiger Zeit gab es keine konkrete Empfehlung. Prüfe, ob ein passendes Produkt authentisch in einen Moment passt."
    if total >= 6 and direct_offer >= 3 and direct_offer / total >= 0.4:
        return "Der Anteil an direkten Angeboten ist gerade recht hoch. Achte darauf, wieder mehr reinen Vertrauensaufbau einzustreuen."
    return None


# ---------------------------------------------------------------------------
# Commercial classifier — deterministic, rule-based
# ---------------------------------------------------------------------------

def classify_commercial(text: str, action_type: str = "idea") -> CommercialClass:
    """Classify a content output into a commercial class.

    Rule-based. Grounded in docs/everydayengel/content_commercial_classification.md.

    For /idea outputs, the expected distribution is:
      - trust_building: majority (pure lived moments, no commercial signal)
      - product_near: consistently present (friction that implies a product category)
      - recommendation_ready: rare (explicit product + personal endorsement)
      - direct_offer: very rare (code, label, paid trigger)
      - off_thesis_or_monetization_waste: should be zero

    Args:
        text: The generated content output text.
        action_type: The command type (currently used for future specialization).

    Returns:
        CommercialClass matching the most specific signal found, or trust_building.
    """
    low = text.lower()

    if any(s in low for s in _DIRECT_OFFER_SIGNALS):
        return CommercialClass.direct_offer

    if any(s in low for s in _RECOMMENDATION_SIGNALS):
        return CommercialClass.recommendation_ready

    if any(s in low for s in _PRODUCT_NEAR_SIGNALS):
        return CommercialClass.product_near

    return CommercialClass.trust_building


# ---------------------------------------------------------------------------
# Human-readable German labels for reason tags (Telegram button text)
# ---------------------------------------------------------------------------

REASON_TAG_LABELS: dict[str, str] = {
    CorrectionReasonTag.too_literal.value:         "Zu wörtlich",
    CorrectionReasonTag.too_free.value:            "Zu frei",
    CorrectionReasonTag.moment_missed.value:       "Moment verpasst",
    CorrectionReasonTag.tone_off.value:            "Ton falsch",
    CorrectionReasonTag.not_julia.value:           "Nicht Julia",
    CorrectionReasonTag.too_broad.value:           "Zu breit",
    CorrectionReasonTag.too_loud.value:            "Zu laut",
    CorrectionReasonTag.too_producty.value:        "Zu werblich",
    CorrectionReasonTag.weak_hook.value:           "Schwacher Hook",
    CorrectionReasonTag.weak_clarity.value:        "Unklar",
    CorrectionReasonTag.good_but_wrong_platform.value: "Falsches Format",
}


# ---------------------------------------------------------------------------
# IdeaCorrectionService — live capture with EventLogService persistence
# ---------------------------------------------------------------------------

class IdeaCorrectionService:
    """Records correction decisions for /idea outputs.

    Three-layer persistence:
    - CorrectionFileRepository: file-backed JSON, durable across restarts (source of truth)
    - CorrectionCaptureStore: in-memory session cache (secondary, optional)
    - EventLogService: append-only audit trail (in-process)

    entity_type = "idea", entity_id = proposal_id so corrections are
    queryable via event_log_service.list_for_entity(project_key, "idea", proposal_id).
    """

    def __init__(
        self,
        correction_store: CorrectionCaptureStore,
        event_log_service: "object",  # EventLogService — typed loosely to avoid circular import
        correction_repository: CorrectionFileRepository | None = None,
    ) -> None:
        self._store = correction_store
        self._event_log = event_log_service
        self._repository = correction_repository

    def record_correction(
        self,
        *,
        project_key: str,
        proposal_id: str,
        prompt: str,
        bot_output: str,
        commercial_class: str | None,
        status: CorrectionStatus,
        reason_tag: CorrectionReasonTag = CorrectionReasonTag.none,
        corrected_output: str | None = None,
    ) -> CorrectionRecord:
        """Record one correction decision and persist via file repository + EventLogService."""
        from uuid import uuid4
        record_id = f"corr_{uuid4().hex}"
        comm_class: CommercialClass | None = None
        if commercial_class:
            try:
                comm_class = CommercialClass(commercial_class)
            except ValueError:
                pass
        previous = None
        if self._repository is not None:
            previous = self._repository.latest_for_proposal(project_key, proposal_id)
        if previous is None:
            previous = self._store.latest_for_proposal(project_key, proposal_id)

        record = CorrectionRecord(
            record_id=record_id,
            project_key=project_key,
            action_type="idea",
            proposal_id=proposal_id,
            prompt=prompt,
            bot_output=bot_output,
            status=status,
            commercial_class=comm_class,
            reason_tag=reason_tag,
            corrected_output=corrected_output,
            supersedes_record_id=previous.record_id if previous is not None else None,
        )
        # Durable persistence first — file repo survives restarts
        if self._repository is not None:
            self._repository.append(record)
        # In-memory session cache (secondary)
        self._store.record(record)
        # Audit trail
        self._event_log.log_event(
            project_key=project_key,
            entity_type="idea",
            entity_id=proposal_id,
            event_type="idea.correction_recorded",
            message=f"Idea correction: {status.value}",
            payload_json=record.to_snapshot(),
        )
        return record
