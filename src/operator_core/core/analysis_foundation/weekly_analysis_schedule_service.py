from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import TYPE_CHECKING

from operator_core.core.analysis_foundation.models import WeeklyAnalysisStatus

if TYPE_CHECKING:
    from operator_core.core.analysis_foundation.weekly_analysis_service import WeeklyAnalysisService
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService

_log = logging.getLogger("operator_core.core.analysis_foundation.weekly_analysis_schedule_service")

# Check interval (once every 6 hours is plenty for a weekly task)
_CHECK_INTERVAL_SECONDS = 6 * 3600
# Refresh interval: run if latest artifact is older than this
_REFRESH_THRESHOLD_DAYS = 7

class WeeklyAnalysisScheduleService:
    """
    Background service that ensures a fresh Weekly Analysis artifact exists.
    """
    def __init__(
        self,
        *,
        weekly_analysis_service: WeeklyAnalysisService,
        persistence_service: WeeklyAnalysisPersistenceService,
        project_key: str = "everydayengel",
    ) -> None:
        self._weekly_analysis_svc = weekly_analysis_service
        self._persistence_svc = persistence_service
        self._project_key = project_key

    def run_until_stopped(self, stop_event: Event) -> None:
        _log.info("weekly analysis schedule service starting | project=%s", self._project_key)
        
        # Immediate first check on startup
        try:
            self._check_and_refresh()
        except Exception as exc:
            _log.error("weekly analysis initial refresh failed | error=%s", exc)

        while not stop_event.is_set():
            try:
                # Sleep for interval or until stopped
                if stop_event.wait(timeout=_CHECK_INTERVAL_SECONDS):
                    break
                
                self._check_and_refresh()
            except Exception as exc:
                _log.error("weekly analysis schedule loop error | error=%s", exc)
                # Brief back-off if error
                stop_event.wait(timeout=60)

        _log.info("weekly analysis schedule service stopped")

    def _check_and_refresh(self) -> None:
        """
        1. Load latest artifact
        2. Check if fresh enough
        3. If not, trigger full run
        4. Record status
        """
        _log.debug("weekly analysis: checking freshness | project=%s", self._project_key)
        
        now = datetime.now(timezone.utc)
        latest = self._persistence_svc.load_latest(project_key=self._project_key)
        
        should_run = False
        age_days = None

        if latest is None:
            _log.info("weekly analysis: no artifact found | project=%s -> triggering first run", self._project_key)
            should_run = True
        else:
            try:
                # fromisoformat handles Z in 3.11+, but we normalize for safety
                gen_dt = datetime.fromisoformat(latest.generated_at.replace("Z", "+00:00"))
                if gen_dt.tzinfo is None:
                    gen_dt = gen_dt.replace(tzinfo=timezone.utc)

                age = now - gen_dt
                age_days = age.days
                if age > timedelta(days=_REFRESH_THRESHOLD_DAYS):
                    _log.info(
                        "weekly analysis: stale artifact found | project=%s analysis_id=%s age_days=%s -> refreshing",
                        self._project_key,
                        latest.analysis_id,
                        age.days,
                    )
                    should_run = True
                else:
                    _log.debug(
                        "weekly analysis: artifact is fresh | project=%s analysis_id=%s age_days=%s",
                        self._project_key,
                        latest.analysis_id,
                        age.days,
                    )
            except (ValueError, TypeError) as exc:
                _log.warning("weekly analysis: unparseable date | project=%s error=%s -> triggering safety refresh", self._project_key, exc)
                should_run = True

        if not should_run:
            self._record_status(
                last_status="skipped_fresh",
                latest_artifact=latest,
                age_days=age_days,
            )
            return

        try:
            artifact = self._weekly_analysis_svc.run_weekly_analysis(
                project_key=self._project_key,
                job_id="auto_weekly_refresh",
                run_id=now.strftime("%Y%m%d_%H%M%S"),
            )
            self._record_status(
                last_status="success",
                latest_artifact=artifact,
                age_days=0,
            )
        except Exception as exc:
            _log.error("weekly analysis: run failed | project=%s error=%s", self._project_key, exc)
            self._record_status(
                last_status="failed",
                latest_artifact=latest,
                age_days=age_days,
                error_summary=str(exc),
            )

    def _record_status(
        self,
        last_status: str,
        latest_artifact: any | None = None,
        age_days: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        """Helper to build and persist status record."""
        now_str = datetime.now(timezone.utc).isoformat()
        
        # Load previous status to preserve last_success_at
        prev = self._persistence_svc.load_status(project_key=self._project_key)
        last_success_at = prev.last_success_at if prev else None
        if last_status == "success":
            last_success_at = now_str

        status = WeeklyAnalysisStatus(
            project_key=self._project_key,
            last_run_at=now_str,
            last_success_at=last_success_at,
            last_status=last_status,
            latest_analysis_id=latest_artifact.analysis_id if latest_artifact else None,
            actual_model_used=latest_artifact.execution_meta.model_name if latest_artifact else None,
            fallback_used=False, # We don't track this explicitly in artifact yet, but preferred model is logged
            artifact_age_days=age_days,
            error_summary=error_summary,
        )
        self._persistence_svc.persist_status(status)
