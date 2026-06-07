import json
from unittest.mock import MagicMock, call

from operator_core.core.analysis_foundation.models import AnalysisSnapshot, EvidencePack, ModelExecutionMeta
from operator_core.integrations.airtable_service import AirtableRecord, AirtableRecordList
from operator_core.integrations.analysis_foundation_persistence import AnalysisFoundationPersistenceService


def _build_snapshot() -> AnalysisSnapshot:
    return AnalysisSnapshot(
        snapshot_id="as_1",
        project_key="everydayengel",
        scope="platform",
        created_at="2026-04-13T10:00:00+00:00",
        title="TikTok analysis snapshot",
        summary="TikTok snapshot",
        platform_key="tiktok",
        analytics_summary_lines=("Posts: 4",),
        rule_summary_lines=("Audience: Frauen 23-38",),
        source_refs=("docs:project-state",),
        posting_context={"enabled": True, "time_local": "20:06"},
    )


def _build_evidence_pack() -> EvidencePack:
    return EvidencePack(
        evidence_pack_id="ep_1",
        project_key="everydayengel",
        created_at="2026-04-13T10:00:00+00:00",
        summary="Evidence pack",
        snapshot_ids=("as_1",),
        source_refs=("analytics:global_recent",),
        evidence_lines=("TikTok: 20:06",),
    )


def _build_execution_meta() -> ModelExecutionMeta:
    return ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-5.4",
        task_role="analysis_control",
        status="prepared",
        notes=("Slice 2 persistence.",),
    )


def test_analysis_foundation_persistence_creates_snapshot_and_evidence_records() -> None:
    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(), offset=None),
        AirtableRecordList(records=(), offset=None),
    )
    airtable_service.create_record.side_effect = (
        AirtableRecord(record_id="recSnapshot1", fields={"snapshot_id": "as_1"}),
        AirtableRecord(record_id="recEvidence1", fields={"evidence_pack_id": "ep_1"}),
    )
    service = AnalysisFoundationPersistenceService(airtable_service=airtable_service)

    persisted = service.persist(
        project_key="everydayengel",
        job_id="job_1",
        run_id="run_1",
        analysis_snapshots=(_build_snapshot(),),
        evidence_pack=_build_evidence_pack(),
        execution_meta=_build_execution_meta(),
    )

    assert persisted.analysis_snapshots[0].airtable_record_id == "recSnapshot1"
    assert persisted.evidence_pack.airtable_record_id == "recEvidence1"
    assert airtable_service.create_record.call_args_list == [
        call(
            "Analysis Snapshots",
            {
                "snapshot_id": "as_1",
                "project_key": "everydayengel",
                "scope": "platform",
                "created_at": "2026-04-13T10:00:00+00:00",
                "summary_json": json.dumps({"title": "TikTok analysis snapshot", "summary": "TikTok snapshot"}),
                "platform_key": "tiktok",
                "source_refs_json": json.dumps(["docs:project-state"]),
                "analytics_refs_json": json.dumps(["Posts: 4"]),
                "posting_context_json": json.dumps({"enabled": True, "time_local": "20:06"}),
                "rule_context_json": json.dumps(["Audience: Frauen 23-38"]),
                "job_id": "job_1",
                "run_id": "run_1",
            },
            project_key="everydayengel",
        ),
        call(
            "Evidence Packs",
            {
                "evidence_pack_id": "ep_1",
                "project_key": "everydayengel",
                "created_at": "2026-04-13T10:00:00+00:00",
                "summary_json": json.dumps({"summary": "Evidence pack"}),
                "snapshot_ids_json": json.dumps(["as_1"]),
                "source_refs_json": json.dumps(["analytics:global_recent"]),
                "evidence_lines_json": json.dumps(["TikTok: 20:06"]),
                "execution_meta_json": json.dumps(_build_execution_meta().to_snapshot()),
                "job_id": "job_1",
                "run_id": "run_1",
            },
            project_key="everydayengel",
        ),
    ]


def test_analysis_foundation_persistence_updates_existing_records() -> None:
    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = (
        AirtableRecordList(records=(AirtableRecord(record_id="recSnapshot1", fields={"snapshot_id": "as_1"}),), offset=None),
        AirtableRecordList(records=(AirtableRecord(record_id="recEvidence1", fields={"evidence_pack_id": "ep_1"}),), offset=None),
    )
    airtable_service.update_record.side_effect = (
        AirtableRecord(record_id="recSnapshot1", fields={"snapshot_id": "as_1"}),
        AirtableRecord(record_id="recEvidence1", fields={"evidence_pack_id": "ep_1"}),
    )
    service = AnalysisFoundationPersistenceService(airtable_service=airtable_service)

    persisted = service.persist(
        project_key="everydayengel",
        job_id="job_2",
        run_id="run_2",
        analysis_snapshots=(_build_snapshot(),),
        evidence_pack=_build_evidence_pack(),
        execution_meta=_build_execution_meta(),
    )

    assert persisted.analysis_snapshots[0].airtable_record_id == "recSnapshot1"
    assert persisted.evidence_pack.airtable_record_id == "recEvidence1"
    assert airtable_service.create_record.call_count == 0
    assert airtable_service.update_record.call_count == 2


def test_analysis_foundation_persistence_is_non_blocking_on_error() -> None:
    airtable_service = MagicMock()
    airtable_service.find_records.side_effect = Exception("Airtable 403 Forbidden")
    service = AnalysisFoundationPersistenceService(airtable_service=airtable_service)

    persisted = service.persist(
        project_key="everydayengel",
        job_id="job_error",
        run_id="run_error",
        analysis_snapshots=(_build_snapshot(),),
        evidence_pack=_build_evidence_pack(),
        execution_meta=_build_execution_meta(),
    )

    assert persisted.analysis_snapshots[0].airtable_record_id is None
    assert persisted.evidence_pack.airtable_record_id is None
    assert persisted.evidence_pack_record.record_id == "failed"


# --- 422 prevention: all _json fields must be str, not raw Python objects ---

def test_snapshot_fields_are_json_strings_not_raw_objects() -> None:
    """
    Root cause of the live 422: _build_snapshot_fields sent raw dicts/lists to
    Airtable multilineText fields. Airtable cannot parse a JSON object as a text
    field value → 422. Fix: all _json values must be str (json.dumps applied).
    """
    airtable_service = MagicMock()
    airtable_service.find_records.return_value = AirtableRecordList(records=(), offset=None)
    airtable_service.create_record.return_value = AirtableRecord(record_id="rec1", fields={})

    service = AnalysisFoundationPersistenceService(airtable_service=airtable_service)
    service.persist(
        project_key="everydayengel",
        job_id="j",
        run_id="r",
        analysis_snapshots=(_build_snapshot(),),
        evidence_pack=_build_evidence_pack(),
        execution_meta=_build_execution_meta(),
    )

    snapshot_fields: dict = airtable_service.create_record.call_args_list[0].args[1]
    json_field_names = [k for k in snapshot_fields if k.endswith("_json")]
    assert json_field_names, "Expected at least one _json field in snapshot payload"
    for field_name in json_field_names:
        value = snapshot_fields[field_name]
        assert isinstance(value, str), (
            f"Snapshot field '{field_name}' must be a JSON string (str), "
            f"got {type(value).__name__}: {value!r}. "
            "Raw Python objects cause Airtable 422 on multilineText fields."
        )
        # Must also be valid JSON
        json.loads(value)


def test_evidence_pack_fields_are_json_strings_not_raw_objects() -> None:
    """
    Same 422 root cause applies to Evidence Packs: all _json fields must be str.
    """
    airtable_service = MagicMock()
    airtable_service.find_records.return_value = AirtableRecordList(records=(), offset=None)
    airtable_service.create_record.return_value = AirtableRecord(record_id="rec1", fields={})

    service = AnalysisFoundationPersistenceService(airtable_service=airtable_service)
    service.persist(
        project_key="everydayengel",
        job_id="j",
        run_id="r",
        analysis_snapshots=(_build_snapshot(),),
        evidence_pack=_build_evidence_pack(),
        execution_meta=_build_execution_meta(),
    )

    evidence_fields: dict = airtable_service.create_record.call_args_list[1].args[1]
    json_field_names = [k for k in evidence_fields if k.endswith("_json")]
    assert json_field_names, "Expected at least one _json field in evidence pack payload"
    for field_name in json_field_names:
        value = evidence_fields[field_name]
        assert isinstance(value, str), (
            f"Evidence pack field '{field_name}' must be a JSON string (str), "
            f"got {type(value).__name__}: {value!r}. "
            "Raw Python objects cause Airtable 422 on multilineText fields."
        )
        json.loads(value)


def test_snapshot_json_fields_round_trip_correctly() -> None:
    """
    The serialized _json values must parse back to the original data structures.
    Verifies content integrity, not just type correctness.
    """
    airtable_service = MagicMock()
    airtable_service.find_records.return_value = AirtableRecordList(records=(), offset=None)
    airtable_service.create_record.return_value = AirtableRecord(record_id="rec1", fields={})

    service = AnalysisFoundationPersistenceService(airtable_service=airtable_service)
    service.persist(
        project_key="everydayengel",
        job_id="j",
        run_id="r",
        analysis_snapshots=(_build_snapshot(),),
        evidence_pack=_build_evidence_pack(),
        execution_meta=_build_execution_meta(),
    )

    snap_fields: dict = airtable_service.create_record.call_args_list[0].args[1]
    summary = json.loads(snap_fields["summary_json"])
    assert summary["title"] == "TikTok analysis snapshot"
    assert summary["summary"] == "TikTok snapshot"

    source_refs = json.loads(snap_fields["source_refs_json"])
    assert source_refs == ["docs:project-state"]

    analytics = json.loads(snap_fields["analytics_refs_json"])
    assert analytics == ["Posts: 4"]

    ev_fields: dict = airtable_service.create_record.call_args_list[1].args[1]
    ev_summary = json.loads(ev_fields["summary_json"])
    assert ev_summary["summary"] == "Evidence pack"

    exec_meta = json.loads(ev_fields["execution_meta_json"])
    assert exec_meta["provider_name"] == "openai"
    assert exec_meta["model_name"] == "gpt-5.4"
