from __future__ import annotations

import json
from dataclasses import replace

from operator_core.core.analysis_foundation.models import (
    ModelExecutionMeta,
    WeeklyAnalysisArtifact,
    WeeklyAnalysisStatus,
)
from .airtable_service import AirtableRecord, AirtableService

_WEEKLY_ANALYSIS_TABLE = "Weekly Analysis"
_WEEKLY_STATUS_TABLE = "Weekly Analysis Status"

class WeeklyAnalysisPersistenceService:
    def __init__(self, *, airtable_service: AirtableService) -> None:
        self.airtable_service = airtable_service

    def persist(
        self,
        artifact: WeeklyAnalysisArtifact,
    ) -> WeeklyAnalysisArtifact:
        """
        Persist a weekly analysis artifact to Airtable.
        Uses non-blocking safeguard similar to foundation persistence.
        """
        try:
            return self._do_persist(artifact)
        except Exception as exc:
            import logging
            logging.getLogger("operator_core.integrations.weekly_analysis_persistence").warning(
                "weekly analysis persistence failed (non-blocking) | project=%s error=%s",
                artifact.project_key,
                exc,
            )
            return artifact

    def _do_persist(
        self,
        artifact: WeeklyAnalysisArtifact,
    ) -> WeeklyAnalysisArtifact:
        fields = self._build_fields(artifact)
        
        # Upsert logic based on analysis_id
        existing = self.airtable_service.find_records(
            _WEEKLY_ANALYSIS_TABLE,
            project_key=artifact.project_key,
            filter_formula=f"{{analysis_id}}={json.dumps(artifact.analysis_id)}",
            max_records=1,
            fields=("analysis_id",),
        )
        
        if existing.records:
            record = self.airtable_service.update_record(
                _WEEKLY_ANALYSIS_TABLE,
                existing.records[0].record_id,
                fields,
                project_key=artifact.project_key,
            )
        else:
            record = self.airtable_service.create_record(
                _WEEKLY_ANALYSIS_TABLE,
                fields,
                project_key=artifact.project_key,
            )
            
        return replace(artifact, airtable_record_id=record.record_id)

    def load_latest(self, project_key: str) -> WeeklyAnalysisArtifact | None:
        """
        Load the latest weekly analysis artifact for a project.
        Returns None if not found or unreadable.
        """
        try:
            # We don't have built-in sort in list_records yet, so we get the last few
            # and sort manually or just rely on Airtable's default (which is often created time).
            # To be safe, we filter by project and then sort in Python.
            result = self.airtable_service.list_records(
                _WEEKLY_ANALYSIS_TABLE,
                project_key=project_key,
                max_records=5,  # Get a few to find the actual latest
            )
            if not result.records:
                return None

            # Sort by generated_at descending
            records = sorted(
                result.records,
                key=lambda r: str(r.fields.get("generated_at", "")),
                reverse=True
            )
            return self._parse_record(records[0])
        except Exception as exc:
            import logging
            logging.getLogger("operator_core.integrations.weekly_analysis_persistence").warning(
                "weekly analysis load failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _parse_record(self, record: AirtableRecord) -> WeeklyAnalysisArtifact:
        f = record.fields
        
        def get_json_list(key: str) -> tuple[str, ...]:
            val = f.get(key)
            if not val: return ()
            try:
                if isinstance(val, str):
                    data = json.loads(val)
                    if isinstance(data, list):
                        return tuple(str(item) for item in data)
                elif isinstance(val, list):
                    return tuple(str(item) for item in val)
            except Exception:
                pass
            return ()

        def get_json_dict(key: str) -> dict[str, str]:
            val = f.get(key)
            if not val: return {}
            try:
                if isinstance(val, str):
                    data = json.loads(val)
                    if isinstance(data, dict):
                        return {str(k): str(v) for k, v in data.items()}
                elif isinstance(val, dict):
                    return {str(k): str(v) for k, v in val.items()}
            except Exception:
                pass
            return {}

        meta_raw = get_json_dict("execution_meta_json")
        execution_meta = ModelExecutionMeta(
            provider_name=meta_raw.get("provider_name", "unknown"),
            model_name=meta_raw.get("model_name", "unknown"),
            task_role=meta_raw.get("task_role", "unknown"),
            status=meta_raw.get("status", "completed"),
            notes=tuple(meta_raw.get("notes", ())) if isinstance(meta_raw.get("notes"), (list, tuple)) else (),
        )

        return WeeklyAnalysisArtifact(
            analysis_id=str(f.get("analysis_id", "")),
            project_key=str(f.get("project_key", "")),
            analysis_window_start=str(f.get("window_start", "")),
            analysis_window_end=str(f.get("window_end", "")),
            generated_at=str(f.get("generated_at", "")),
            key_winners=get_json_list("key_winners_json"),
            weak_patterns=get_json_list("weak_patterns_json"),
            recommended_content_directions=get_json_list("content_directions_json"),
            recommended_hook_directions=get_json_list("hook_directions_json"),
            recommended_cta_directions=get_json_list("cta_directions_json"),
            recommended_platform_notes=get_json_dict("platform_notes_json"),
            confidence_score=float(f.get("confidence_score") or 0.7),
            evidence_summary=str(f.get("evidence_summary", "")),
            source_refs=get_json_list("source_refs_json"),
            execution_meta=execution_meta,
            job_id=str(f.get("job_id", "")),
            run_id=str(f.get("run_id", "")),
            airtable_record_id=record.record_id,
        )

    def persist_status(self, status: WeeklyAnalysisStatus) -> WeeklyAnalysisStatus:
        """Persist weekly analysis status to Airtable (upsert by project_key)."""
        try:
            fields = self._build_status_fields(status)
            existing = self.airtable_service.find_records(
                _WEEKLY_STATUS_TABLE,
                project_key=status.project_key,
                filter_formula=f"{{project_key}}={json.dumps(status.project_key)}",
                max_records=1,
                fields=("project_key",),
            )
            if existing.records:
                record = self.airtable_service.update_record(
                    _WEEKLY_STATUS_TABLE,
                    existing.records[0].record_id,
                    fields,
                    project_key=status.project_key,
                )
            else:
                record = self.airtable_service.create_record(
                    _WEEKLY_STATUS_TABLE,
                    fields,
                    project_key=status.project_key,
                )
            return replace(status, airtable_record_id=record.record_id)
        except Exception as exc:
            import logging
            logging.getLogger("operator_core.integrations.weekly_analysis_persistence").warning(
                "weekly status persistence failed (non-blocking) | project=%s error=%s",
                status.project_key,
                exc,
            )
            return status

    def load_status(self, project_key: str) -> WeeklyAnalysisStatus | None:
        """Load current status for a project."""
        try:
            result = self.airtable_service.find_records(
                _WEEKLY_STATUS_TABLE,
                project_key=project_key,
                filter_formula=f"{{project_key}}={json.dumps(project_key)}",
                max_records=1,
            )
            if not result.records:
                return None
            return self._parse_status_record(result.records[0])
        except Exception as exc:
            import logging
            logging.getLogger("operator_core.integrations.weekly_analysis_persistence").warning(
                "weekly status load failed | project=%s error=%s",
                project_key,
                exc,
            )
            return None

    def _parse_status_record(self, record: AirtableRecord) -> WeeklyAnalysisStatus:
        f = record.fields
        return WeeklyAnalysisStatus(
            project_key=str(f.get("project_key", "")),
            last_run_at=str(f.get("last_run_at", "")),
            last_success_at=f.get("last_success_at"),
            last_status=str(f.get("last_status", "unknown")),
            latest_analysis_id=f.get("latest_analysis_id"),
            actual_model_used=f.get("actual_model_used"),
            fallback_used=str(f.get("fallback_used")).lower() == "true",
            artifact_age_days=int(f.get("artifact_age_days")) if f.get("artifact_age_days") is not None else None,
            error_summary=f.get("error_summary"),
            airtable_record_id=record.record_id,
        )

    def _build_status_fields(self, status: WeeklyAnalysisStatus) -> dict[str, object]:
        return {
            "project_key": status.project_key,
            "last_run_at": status.last_run_at,
            "last_success_at": status.last_success_at or "",
            "last_status": status.last_status,
            "latest_analysis_id": status.latest_analysis_id or "",
            "actual_model_used": status.actual_model_used or "",
            "fallback_used": "true" if status.fallback_used else "false",
            "artifact_age_days": status.artifact_age_days if status.artifact_age_days is not None else None,
            "error_summary": status.error_summary or "",
        }

    def _build_fields(self, artifact: WeeklyAnalysisArtifact) -> dict[str, object]:
        return {
            "analysis_id": artifact.analysis_id,
            "project_key": artifact.project_key,
            "window_start": artifact.analysis_window_start,
            "window_end": artifact.analysis_window_end,
            "generated_at": artifact.generated_at,
            "key_winners_json": json.dumps(list(artifact.key_winners)),
            "weak_patterns_json": json.dumps(list(artifact.weak_patterns)),
            "content_directions_json": json.dumps(list(artifact.recommended_content_directions)),
            "hook_directions_json": json.dumps(list(artifact.recommended_hook_directions)),
            "cta_directions_json": json.dumps(list(artifact.recommended_cta_directions)),
            "platform_notes_json": json.dumps(dict(artifact.recommended_platform_notes)),
            "confidence_score": artifact.confidence_score,
            "evidence_summary": artifact.evidence_summary,
            "source_refs_json": json.dumps(list(artifact.source_refs)),
            "execution_meta_json": json.dumps(artifact.execution_meta.to_snapshot()),
            "job_id": artifact.job_id or "",
            "run_id": artifact.run_id or "",
        }
