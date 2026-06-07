from operator_core.core.routing.writer_routing import WriterRoutingService, WriterFlowType

def test_routing_policy_resolution():
    svc = WriterRoutingService()
    
    # Test nuanced flows
    hook_policy = svc.get_policy("hook")
    assert hook_policy.flow_type == WriterFlowType.NUANCED
    assert hook_policy.default_writer == "gpt-5.4"
    assert hook_policy.fast_writer == "gpt-5.4-mini"
    
    cta_policy = svc.get_policy("/cta")  # test slash stripping
    assert cta_policy.flow_type == WriterFlowType.NUANCED
    assert cta_policy.default_writer == "gpt-5.4"
    
    # Test structured flows
    title_policy = svc.get_policy("title")
    assert title_policy.flow_type == WriterFlowType.STRUCTURED
    assert title_policy.fast_writer == "gpt-5.4-mini"
    
    serie_policy = svc.get_policy("SERIE")  # test case insensitivity
    assert serie_policy.flow_type == WriterFlowType.STRUCTURED

def test_recommended_model_selection():
    svc = WriterRoutingService()
    
    # Default behavior
    assert svc.get_recommended_model("hook") == "gpt-5.4"
    assert svc.get_recommended_model("title") == "gpt-5.4"
    
    # Prefer fast behavior
    assert svc.get_recommended_model("hook", prefer_fast=True) == "gpt-5.4-mini"
    assert svc.get_recommended_model("title", prefer_fast=True) == "gpt-5.4-mini"
    
    # Fallback behavior
    assert svc.get_recommended_model("unknown_flow") == "gpt-5.4"
    assert svc.get_recommended_model("unknown_flow", prefer_fast=True) == "gpt-5.4"

def test_alias_mapping():
    svc = WriterRoutingService()
    
    # draft -> idea
    draft_policy = svc.get_policy("draft")
    assert draft_policy.policy_id == "idea"
    
    # vollauto -> idea
    auto_policy = svc.get_policy("vollauto")
    assert auto_policy.policy_id == "idea"
    
    # rewrite -> mutation
    rewrite_policy = svc.get_policy("rewrite")
    assert rewrite_policy.policy_id == "mutation"
