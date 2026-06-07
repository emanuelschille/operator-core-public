from __future__ import annotations

import json
from dataclasses import dataclass, replace

from operator_core.core.analysis_foundation.models import AnalysisSnapshot, EvidencePack, ModelExecutionMeta

from .airtable_service import AirtableRecord, AirtableService


_ANALYSIS_SNAPSHOTS_TABLE = "Analysis Snapshots"
_EVIDENCE_PACKS_TABLE = "Evidence Packs"


@dataclass(frozen=True)
class PersistedAnalysisFoundationArtifacts:
    analysis_snapshots: tuple[AnalysisSnapshot, ...]
    evidence_pack: EvidencePack
    snapshot_records: tuple[AirtableRecord, ...]
    evidence_pack_record: AirtableRecord


class AnalysisFoundationPersistenceService:
    def __init__(self, *, airtable_service: AirtableService) -> None:
        self.airtable_service = airtable_service

    def persist(
        self,
        *,
        project_key: str,
        job_id: str,
        run_id: str,
        analysis_snapshots: tuple[AnalysisSnapshot, ...],
        evidence_pack: EvidencePack,
        execution_meta: ModelExecutionMeta,
    ) -> PersistedAnalysisFoundationArtifacts:
        try:
            return self._do_persist(
                project_key=project_key,
                job_id=job_id,
                run_id=run_id,
                analysis_snapshots=analysis_snapshots,
                evidence_pack=evidence_pack,
                execution_meta=execution_meta,
            )
        except Exception as exc:
            import logging
            logging.getLogger("operator_core.integrations.analysis_foundation_persistence").warning(
                "foundation persistence failed (non-blocking) | project=%s error=%s",
                project_key,
                exc,
            )
            return PersistedAnalysisFoundationArtifacts(
                analysis_snapshots=analysis_snapshots,
                evidence_pack=evidence_pack,
                snapshot_records=(),
                evidence_pack_record=AirtableRecord(record_id="failed", fields={}),
            )

    def _do_persist(
        self,
        *,
        project_key: str,
        job_id: str,
        run_id: str,
        analysis_snapshots: tuple[AnalysisSnapshot, ...],
        evidence_pack: EvidencePack,
        execution_meta: ModelExecutionMeta,
    ) -> PersistedAnalysisFoundationArtifacts:
        persisted_snapshots: list[AnalysisSnapshot] = []
        snapshot_records: list[AirtableRecord] = []

        for snapshot in analysis_snapshots:
            record = self._upsert_record(
                table_name=_ANALYSIS_SNAPSHOTS_TABLE,
                id_field="snapshot_id",
                object_id=snapshot.snapshot_id,
                project_key=project_key,
                fields=self._build_snapshot_fields(snapshot=snapshot, job_id=job_id, run_id=run_id),
            )
            persisted_snapshots.append(replace(snapshot, airtable_record_id=record.record_id))
            snapshot_records.append(record)

        evidence_record = self._upsert_record(
            table_name=_EVIDENCE_PACKS_TABLE,
            id_field="evidence_pack_id",
            object_id=evidence_pack.evidence_pack_id,
            project_key=project_key,
            fields=self._build_evidence_pack_fields(
                evidence_pack=evidence_pack,
                execution_meta=execution_meta,
                job_id=job_id,
                run_id=run_id,
            ),
        )

        return PersistedAnalysisFoundationArtifacts(
            analysis_snapshots=tuple(persisted_snapshots),
            evidence_pack=replace(evidence_pack, airtable_record_id=evidence_record.record_id),
            snapshot_records=tuple(snapshot_records),
            evidence_pack_record=evidence_record,
        )

    def _upsert_record(
        self,
        *,
        table_name: str,
        id_field: str,
        object_id: str,
        project_key: str,
        fields: dict[str, object],
    ) -> AirtableRecord:
        existing = self.airtable_service.find_records(
            table_name,
            project_key=project_key,
            filter_formula=_formula_equals(id_field, object_id),
            max_records=1,
            fields=(id_field,),
        )
        if existing.records:
            return self.airtable_service.update_record(
                table_name,
                existing.records[0].record_id,
                fields,
                project_key=project_key,
            )

        return self.airtable_service.create_record(
            table_name,
            fields,
            project_key=project_key,
        )

    def _build_snapshot_fields(
        self,
        *,
        snapshot: AnalysisSnapshot,
        job_id: str,
        run_id: str,
    ) -> dict[str, object]:
        # All _json fields are multilineText in Airtable — must be serialized strings,
        # not raw Python objects. Sending a raw dict/list causes a 422 parse error.
        return {
            "snapshot_id": snapshot.snapshot_id,
            "project_key": snapshot.project_key,
            "scope": snapshot.scope,
            "created_at": snapshot.created_at,
            "summary_json": json.dumps({"title": snapshot.title, "summary": snapshot.summary}),
            "platform_key": snapshot.platform_key,
            "source_refs_json": json.dumps(list(snapshot.source_refs)),
            "analytics_refs_json": json.dumps(list(snapshot.analytics_summary_lines)),
            "posting_context_json": json.dumps(dict(snapshot.posting_context)),
            "rule_context_json": json.dumps(list(snapshot.rule_summary_lines)),
            "job_id": job_id,
            "run_id": run_id,
        }

    def _build_evidence_pack_fields(
        self,
        *,
        evidence_pack: EvidencePack,
        execution_meta: ModelExecutionMeta,
        job_id: str,
        run_id: str,
    ) -> dict[str, object]:
        # All _json fields are multilineText in Airtable — must be serialized strings,
        # not raw Python objects. Sending a raw dict/list causes a 422 parse error.
        return {
            "evidence_pack_id": evidence_pack.evidence_pack_id,
            "project_key": evidence_pack.project_key,
            "created_at": evidence_pack.created_at,
            "summary_json": json.dumps({"summary": evidence_pack.summary}),
            "snapshot_ids_json": json.dumps(list(evidence_pack.snapshot_ids)),
            "source_refs_json": json.dumps(list(evidence_pack.source_refs)),
            "evidence_lines_json": json.dumps(list(evidence_pack.evidence_lines)),
            "execution_meta_json": json.dumps(execution_meta.to_snapshot()),
            "job_id": job_id,
            "run_id": run_id,
        }


def _formula_equals(field_name: str, value: str) -> str:
    return f"{{{field_name}}}={json.dumps(value)}"
