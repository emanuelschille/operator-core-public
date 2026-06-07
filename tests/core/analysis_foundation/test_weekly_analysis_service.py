from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    ModelExecutionMeta,
    WeeklyAnalysisArtifact,
)
from operator_core.core.analysis_foundation.weekly_analysis_service import WeeklyAnalysisService


def _make_mock_foundation_result() -> AnalysisFoundationResult:
    return MagicMock(spec=AnalysisFoundationResult, analysis_snapshots=())


def _make_mock_openai_response(text: str, model: str = "gpt-test") -> MagicMock:
    resp = MagicMock()
    resp.output_text = text
    resp.model = model
    return resp


def test_weekly_analysis_service_orchestrates_run() -> None:
    # 1. Setup mocks
    foundation_svc = MagicMock()
    foundation_svc.handle.return_value = _make_mock_foundation_result()
    
    persistence_svc = MagicMock()
    persistence_svc.persist.side_effect = lambda x: x
    
    openai_svc = MagicMock()
    openai_svc.complete_messages.return_value = _make_mock_openai_response(
        "WINNERS: High retention hooks | Daily routines\n"
        "WEAK: Long intros\n"
        "CONTENT: Behind the scenes\n"
        "HOOKS: Question hooks\n"
        "CTAS: Direct questions\n"
        "EVIDENCE: Last 7 days of platform data\n"
        "CONFIDENCE: 0.85"
    )
    
    now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc)
    service = WeeklyAnalysisService(
        foundation_service=foundation_svc,
        persistence_service=persistence_svc,
        openai_service=openai_svc,
        now_provider=lambda: now,
    )
    
    # 2. Execute
    artifact = service.run_weekly_analysis(project_key="everydayengel")
    
    # 3. Verify
    assert isinstance(artifact, WeeklyAnalysisArtifact)
    assert artifact.project_key == "everydayengel"
    assert artifact.generated_at == now.isoformat()
    assert artifact.key_winners == ("High retention hooks", "Daily routines")
    assert artifact.weak_patterns == ("Long intros",)
    assert artifact.recommended_content_directions == ("Behind the scenes",)
    assert artifact.confidence_score == 0.85
    assert artifact.evidence_summary == "Last 7 days of platform data"
    
    # Verify explicit model request
    openai_svc.complete_messages.assert_called_once()
    kwargs = openai_svc.complete_messages.call_args.kwargs
    assert kwargs["model"] == "gpt-5.4"
    assert kwargs["fallback_to_default"] is True

    # Verify persistence was called
    persistence_svc.persist.assert_called_once()
    # Verify foundation was called
    foundation_svc.handle.assert_called_once_with(
        project_key="everydayengel",
        action_type="analysis_snapshot",
        command_body="Weekly synthesis run",
    )


def test_weekly_analysis_service_handles_missing_keys() -> None:
    foundation_svc = MagicMock()
    foundation_svc.handle.return_value = _make_mock_foundation_result()
    persistence_svc = MagicMock()
    persistence_svc.persist.side_effect = lambda x: x
    openai_svc = MagicMock()
    # No winners/weak in this response
    openai_svc.complete_messages.return_value = _make_mock_openai_response(
        "CONTENT: Test\n"
        "CONFIDENCE: 0.5"
    )
    
    service = WeeklyAnalysisService(
        foundation_service=foundation_svc,
        persistence_service=persistence_svc,
        openai_service=openai_svc,
    )
    
    artifact = service.run_weekly_analysis(project_key="test")
    
    assert artifact.key_winners == ()
    assert artifact.weak_patterns == ()
    assert artifact.recommended_content_directions == ("Test",)
    assert artifact.confidence_score == 0.5


def test_weekly_analysis_captures_actual_model_on_fallback() -> None:
    foundation_svc = MagicMock()
    foundation_svc.handle.return_value = _make_mock_foundation_result()
    persistence_svc = MagicMock()
    persistence_svc.persist.side_effect = lambda x: x
    
    openai_svc = MagicMock()
    # Simulate a response from a fallback model
    openai_svc.complete_messages.return_value = _make_mock_openai_response(
        "CONTENT: Fallback content\nCONFIDENCE: 0.8",
        model="gpt-4o-fallback"
    )
    
    service = WeeklyAnalysisService(
        foundation_service=foundation_svc,
        persistence_service=persistence_svc,
        openai_service=openai_svc,
    )
    
    artifact = service.run_weekly_analysis(project_key="everydayengel")
    
    # Should capture the REAL final model used
    assert artifact.execution_meta.model_name == "gpt-4o-fallback"
