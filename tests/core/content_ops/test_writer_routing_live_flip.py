from __future__ import annotations

import pytest
from typing import Any
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.core.content_ops.proposal_store import ContentProposal
from operator_core.core.analysis_foundation.models import (
    AnalysisFoundationResult,
    ModelExecutionMeta,
    WriterBrief,
    EvidencePack,
    AnalysisSnapshot
)
from operator_core.projects.docs import ProjectDocsLoader
from operator_core.core.routing.writer_routing import WriterRoutingService

class MockOpenAIService:
    def __init__(self, fail_preferred: bool = False):
        self.captured_model = "INITIAL"
        self.fail_preferred = fail_preferred
    
    def complete_messages(self, **kwargs):
        requested_model = kwargs.get("model") or "gpt-default"
        self.captured_model = requested_model
        
        # Simulate fallback if requested_model is gpt-5.4 and we configured it to "fail"
        if self.fail_preferred and requested_model == "gpt-5.4" and kwargs.get("fallback_to_default"):
            actual_model = "gpt-4o-fallback"
        else:
            actual_model = requested_model

        # Return a dummy response object
        class Response:
            output_text = "Key: Value"
            model = actual_model
        return Response()

def _make_foundation_result(action_type: str) -> AnalysisFoundationResult:
    execution_meta = ModelExecutionMeta(
        provider_name="openai",
        model_name="gpt-test",
        task_role="analysis_control",
        status="prepared",
    )
    return AnalysisFoundationResult(
        lane_name="analysis_foundation",
        project_key="everydayengel",
        action_type="analysis_snapshot",
        title="Analysis foundation snapshot",
        summary="Prepared analysis foundation",
        analysis_snapshots=(
            AnalysisSnapshot(
                snapshot_id="as_test",
                project_key="everydayengel",
                scope="platform",
                created_at="2026-04-13T10:00:00+00:00",
                title="Test snapshot",
                summary="Test",
                platform_key="tiktok",
                analytics_summary_lines=(),
                rule_summary_lines=(),
                source_refs=(),
            ),
        ),
        writer_brief=WriterBrief(
            brief_id=f"wb_{action_type}",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Test",
            audience="Test",
            constraints=(),
            source_snapshot_ids=("as_test",),
            provider_name="openai",
            model_name="gpt-test",
            task_role="writer",
            execution_meta=execution_meta,
        ),
        evidence_pack=EvidencePack(
            evidence_pack_id="ep_test",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            summary="Evidence",
            snapshot_ids=("as_test",),
            source_refs=(),
            evidence_lines=(),
        ),
        execution_meta=execution_meta,
    )

@pytest.fixture
def docs_loader():
    return ProjectDocsLoader()

def test_idea_captures_actual_model(docs_loader):
    openai_svc = MockOpenAIService(fail_preferred=False)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    result = service.generate_idea_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("idea")
    )
    
    # Preferred is gpt-5.4
    assert result.content_result.model_name == "gpt-5.4"

def test_title_captures_actual_model(docs_loader):
    openai_svc = MockOpenAIService(fail_preferred=False)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    result = service.generate_title_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("title")
    )
    
    # Preferred is gpt-5.4-mini
    assert result.content_result.model_name == "gpt-5.4-mini"

def test_fallback_captures_actual_model(docs_loader):
    # FORCE failure of preferred model
    openai_svc = MockOpenAIService(fail_preferred=True)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    result = service.generate_idea_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("idea")
    )
    
    assert result.content_result.model_name == "gpt-4o-fallback"

def test_followup_captures_actual_model(docs_loader):
    # FORCE failure of preferred model
    openai_svc = MockOpenAIService(fail_preferred=True)
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    proposal = ContentProposal(
        proposal_id="p_test",
        project_key="everydayengel",
        action_type="idea",
        platform="tiktok",
        fields={"Key": "Value"}
    )
    
    result = service.generate_followup_from_foundation(
        project_key="everydayengel",
        proposal=proposal,
        instruction="make it better",
        foundation_result=_make_foundation_result("mutation"),
        mutation_mode="followup"
    )
    
    assert result.content_result.model_name == "gpt-4o-fallback"
