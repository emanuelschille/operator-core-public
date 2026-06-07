from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from operator_core.core.analysis_foundation.models import ModelExecutionMeta, WeeklyAnalysisArtifact

if TYPE_CHECKING:
    from operator_core.core.analysis_foundation.service import AnalysisFoundationService
    from operator_core.integrations.openai_service import OpenAIService
    from operator_core.integrations.weekly_analysis_persistence import WeeklyAnalysisPersistenceService

_log = logging.getLogger("operator_core.core.analysis_foundation.weekly_analysis_service")

_PREFERRED_MODEL = "gpt-5.4"

class WeeklyAnalysisService:
    def __init__(
        self,
        *,
        foundation_service: AnalysisFoundationService,
        persistence_service: WeeklyAnalysisPersistenceService,
        openai_service: OpenAIService,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._foundation_service = foundation_service
        self._persistence_service = persistence_service
        self._openai_service = openai_service
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def run_weekly_analysis(
        self,
        *,
        project_key: str,
        job_id: str | None = None,
        run_id: str | None = None,
    ) -> WeeklyAnalysisArtifact:
        """
        Orchestrate a full weekly analysis run:
        1. Gather context from foundation service
        2. Synthesize using LLM
        3. Persist to Airtable
        """
        now = self._now_provider().astimezone(timezone.utc)
        window_end = now
        window_start = now - timedelta(days=7)
        
        # 1. Gather foundation context
        # We use the existing analysis_snapshot action to get platform rollups
        foundation = self._foundation_service.handle(
            project_key=project_key,
            action_type="analysis_snapshot",
            command_body="Weekly synthesis run",
        )
        
        # 2. Synthesize using LLM
        artifact = self._synthesize_analysis(
            project_key=project_key,
            foundation=foundation,
            window_start=window_start,
            window_end=window_end,
            now=now,
            job_id=job_id,
            run_id=run_id,
        )
        
        # 3. Persist
        persisted = self._persistence_service.persist(artifact)
        
        _log.info(
            "weekly analysis completed | project=%s analysis_id=%s airtable_id=%s",
            project_key,
            persisted.analysis_id,
            persisted.airtable_record_id,
        )
        
        return persisted

    def _synthesize_analysis(
        self,
        *,
        project_key: str,
        foundation: any,  # AnalysisFoundationResult
        window_start: datetime,
        window_end: datetime,
        now: datetime,
        job_id: str | None,
        run_id: str | None,
    ) -> WeeklyAnalysisArtifact:
        # Build prompt from foundation snapshots
        context_lines = []
        for snapshot in foundation.analysis_snapshots:
            context_lines.append(f"--- {snapshot.title} ({snapshot.scope}) ---")
            context_lines.extend(snapshot.analytics_summary_lines)
            context_lines.extend(snapshot.rule_summary_lines)
            context_lines.append("")

        system_prompt = (
            "Du bist ein Senior Content Strategist für everydayengel.\n"
            "Deine Aufgabe ist es, die Performance der letzten Woche zu analysieren und "
            "konkrete, datengestützte Empfehlungen für die kommende Woche zu geben.\n\n"
            "Analysiere die folgenden Snapshots und extrahiere:\n"
            "1. Gewinner-Muster (was funktioniert gerade besonders gut?)\n"
            "2. Schwache Muster (was sollte vermieden werden?)\n"
            "3. Empfohlene Inhaltsrichtungen\n"
            "4. Empfohlene Hook-Stile\n"
            "5. Empfohlene CTA-Richtungen\n\n"
            "Antworte EXAKT in diesem Format:\n"
            "WINNERS: <Punkt 1> | <Punkt 2>\n"
            "WEAK: <Punkt 1> | <Punkt 2>\n"
            "CONTENT: <Richtung 1> | <Richtung 2>\n"
            "HOOKS: <Stil 1> | <Stil 2>\n"
            "CTAS: <Richtung 1> | <Richtung 2>\n"
            "EVIDENCE: <Kurze Zusammenfassung der Datenbasis>\n"
            "CONFIDENCE: <Zahl zwischen 0.0 und 1.0>"
        )
        
        user_prompt = "\n".join(context_lines)
        
        # Use explicit preferred model with fallback enabled
        response = self._openai_service.complete_messages(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=_PREFERRED_MODEL,
            fallback_to_default=True,
        )
        
        # Basic parsing
        lines = response.output_text.strip().split("\n")
        parsed = {}
        for line in lines:
            if ":" in line:
                key, val = line.split(":", 1)
                parsed[key.strip().upper()] = val.strip()

        def split_val(k: str) -> tuple[str, ...]:
            val = parsed.get(k, "")
            if not val: return ()
            return tuple(item.strip() for item in val.split("|") if item.strip())

        execution_meta = ModelExecutionMeta(
            provider_name="openai",
            model_name=response.model,
            task_role="weekly_analysis_synthesizer",
            status="completed",
        )

        return WeeklyAnalysisArtifact(
            analysis_id=f"wa_{uuid4().hex}",
            project_key=project_key,
            analysis_window_start=window_start.isoformat(),
            analysis_window_end=window_end.isoformat(),
            generated_at=now.isoformat(),
            key_winners=split_val("WINNERS"),
            weak_patterns=split_val("WEAK"),
            recommended_content_directions=split_val("CONTENT"),
            recommended_hook_directions=split_val("HOOKS"),
            recommended_cta_directions=split_val("CTAS"),
            recommended_platform_notes={},  # Future expansion
            confidence_score=float(parsed.get("CONFIDENCE", "0.7")),
            evidence_summary=parsed.get("EVIDENCE", "Basis: Platform Snapshots"),
            source_refs=tuple(dict.fromkeys(ref for s in foundation.analysis_snapshots for ref in s.source_refs)),
            execution_meta=execution_meta,
            job_id=job_id,
            run_id=run_id,
        )
