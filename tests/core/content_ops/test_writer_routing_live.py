from __future__ import annotations
from typing import Any
import pytest
from operator_core.core.content_ops.service import ContentOpsService
from operator_core.core.routing.writer_routing import WriterRoutingService
from operator_core.core.analysis_foundation.models import AnalysisFoundationResult, ModelExecutionMeta, WriterBrief, EvidencePack
from operator_core.projects.docs import ProjectDocsLoader

class MockOpenAIService:
    def __init__(self):
        self.captured_model = None
    
    def complete_messages(self, **kwargs):
        self.captured_model = kwargs.get("model")
        # Return a dummy response object
        class Response:
            output_text = "Key: Value"
            model = kwargs.get("model") or "default-model"
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
        analysis_snapshots=(),
        writer_brief=WriterBrief(
            brief_id=f"wb_{action_type}",
            project_key="everydayengel",
            created_at="2026-04-13T10:00:00+00:00",
            objective="Test",
            audience="Test",
            constraints=(),
            source_snapshot_ids=(),
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
            snapshot_ids=(),
            source_refs=(),
            evidence_lines=(),
        ),
        execution_meta=execution_meta,
    )

@pytest.fixture
def docs_loader():
    return ProjectDocsLoader()

def test_title_uses_fast_model(docs_loader):
    openai_svc = MockOpenAIService()
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    service.generate_title_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("title")
    )
    
    assert openai_svc.captured_model == "gpt-5.4-mini"

def test_serie_uses_fast_model(docs_loader):
    openai_svc = MockOpenAIService()
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    service.generate_serie_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("serie")
    )
    
    assert openai_svc.captured_model == "gpt-5.4-mini"

def test_caption_uses_fast_model(docs_loader):
    openai_svc = MockOpenAIService()
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    service.generate_caption_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("caption")
    )
    
    assert openai_svc.captured_model == "gpt-5.4-mini"

def test_hook_remains_on_default_path(docs_loader):
    openai_svc = MockOpenAIService()
    service = ContentOpsService(docs_loader=docs_loader, openai_service=openai_svc)
    
    service.generate_hook_from_foundation(
        project_key="everydayengel",
        command_body="test",
        foundation_result=_make_foundation_result("hook")
    )
    
    # It should be gpt-5.4 because we pass it explicitly now
    assert openai_svc.captured_model == "gpt-5.4"
