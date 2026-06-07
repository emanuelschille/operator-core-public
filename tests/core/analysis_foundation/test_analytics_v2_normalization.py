from __future__ import annotations

import pytest
from operator_core.core.analysis_foundation.v2.normalization import TaxonomyNormalizer

def test_normalize_cta() -> None:
    norm = TaxonomyNormalizer()
    assert norm.normalize_cta("Was hilft euch beim Schlaf?") == "community_question"
    assert norm.normalize_cta("Was ist eure Meinung dazu?") == "opinion_poll"
    assert norm.normalize_cta("Folgt für mehr!") == "engagement_prompt"
    assert norm.normalize_cta("Link in Bio") == "external_link"
    assert norm.normalize_cta("") == "none"

def test_normalize_format() -> None:
    norm = TaxonomyNormalizer()
    assert norm.normalize_format("talking head") == "talking_head"
    assert norm.normalize_format("b-roll + voice") == "b_roll_voiceover"
    assert norm.normalize_format("talking + nebenbei") == "hybrid"
    assert norm.normalize_format("") == "talking_head"

def test_normalize_serie() -> None:
    norm = TaxonomyNormalizer()
    assert norm.normalize_serie("Alltag verändert sich") == "alltag_schwangerschaft"
    assert norm.normalize_serie("Gedanken / Beobachtungen") == "gedanken_beobachtungen"
    assert norm.normalize_serie("Routinen") == "routinen"
    assert norm.normalize_serie("") == "alltag_schwangerschaft"

def test_normalize_hook() -> None:
    norm = TaxonomyNormalizer()
    # Rule A: Dinge family
    assert norm.normalize_hook("Dinge die sich verändern") == "list_intro"
    assert norm.normalize_hook("Dinge ich vermisse") == "list_intro"
    
    # Rule B: Topic fragments
    assert norm.normalize_hook("Mein Appetit in der Schwangerschaft") == "topic_fragment"
    assert norm.normalize_hook("Werdende Mama") == "topic_fragment"
    assert norm.normalize_hook("Immer das gleiche") == "topic_fragment"
    
    # Rule C: Question dominance
    assert norm.normalize_hook("Kurs lohnt sich das?") == "direct_question"
    
    # Rule D: Singular vs List
    assert norm.normalize_hook("Eine Sache die mir hilft") == "first_person_moment"
    
    # Rhetorical frames
    assert norm.normalize_hook("So sieht mein Morgen aus") == "first_person_moment"
    assert norm.normalize_hook("Kennst du das?") == "second_person_appeal"
    assert norm.normalize_hook("Ich vermisse plötzlich...") == "contrarian_claim"

def test_hook_pattern_vocabulary_presence() -> None:
    from operator_core.core.analysis_foundation.v2.normalization import HOOK_PATTERN_VOCABULARY
    required_keys = {
        "first_person_moment", "second_person_appeal", "direct_question", 
        "contrarian_claim", "list_intro", "topic_fragment", "unclear"
    }
    assert set(HOOK_PATTERN_VOCABULARY.keys()) == required_keys
