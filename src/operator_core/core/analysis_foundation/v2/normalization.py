from __future__ import annotations

import logging
from typing import Mapping

_log = logging.getLogger("operator_core.core.analysis_foundation.v2.normalization")

HOOK_PATTERN_VOCABULARY = {
    "first_person_moment": "Der Hook zentriert sich auf das Erlebnis oder die Beobachtung der Sprecherin (z.B. 'So sieht mein... aus').",
    "second_person_appeal": "Der Hook adressiert direkt das Erleben oder die Wiedererkennung des Zuschauers (z.B. 'Dinge, die du kennst').",
    "direct_question": "Die erste Zeile ist eine direkte Frage an den Zuschauer (z.B. 'Lohnt sich das?', 'Ist das unnötig?').",
    "contrarian_claim": "Die Eröffnung stellt eine Behauptung auf, die gegen einen Standard drückt (z.B. 'Dinge, die ich plötzlich vermisse').",
    "list_intro": "Expliziter Einstieg über eine Aufzählung (z.B. '3 Dinge...') oder Listen-Einrahmung (z.B. 'Dinge die...'). Hinweis: Singulare ('Eine Sache') zählen NICHT dazu.",
    "topic_fragment": "Kurze Themen-Marker, Nominalphrasen oder Label-artige Titel ohne Satzstruktur (z.B. 'Werdende Mama', 'Mein Appetit in der Schwangerschaft').",
    "unclear": "Keine eindeutige Zuordnung möglich."
}


class TaxonomyNormalizer:
    """Rules-based normalization for content taxonomy."""
    
    def normalize_cta(self, raw_cta: str) -> str:
        """Map raw CTA text to cta_typ_norm."""
        raw = str(raw_cta or "").strip().lower()
        if not raw:
            return "none"
        
        # Current audit shows mostly questions
        if "?" in raw:
            if any(w in raw for w in ["meinung", "eure", "wuerdet", "würdet", "oder"]):
                return "opinion_poll"
            return "community_question"
            
        if any(w in raw for w in ["like", "save", "speicher", "folgen", "folgt", "plus"]):
            return "engagement_prompt"
            
        if any(w in raw for w in ["link", "bio", "story"]):
            return "external_link"
            
        return "soft_cta"

    def normalize_format(self, raw_format: str) -> str:
        """Map raw format text to format_typ_norm."""
        raw = str(raw_format or "").strip().lower()
        if not raw:
            return "talking_head" # Default for project
            
        if "talking" in raw:
            if "b-roll" in raw or "voice" in raw or "nebenbei" in raw:
                return "hybrid"
            return "talking_head"
            
        if "b-roll" in raw or "voice" in raw:
            return "b_roll_voiceover"
            
        if "vlog" in raw:
            return "vlog_style"
            
        return "talking_head"

    def normalize_serie(self, raw_serie: str) -> str:
        """Map raw serie/thema text to serie_thema_norm."""
        raw = str(raw_serie or "").strip().lower()
        if not raw:
            return "alltag_schwangerschaft"
            
        if "alltag" in raw:
            return "alltag_schwangerschaft"
        if "gedanken" in raw or "beobacht" in raw:
            return "gedanken_beobachtungen"
        if "erleichterung" in raw:
            return "kleine_erleichterungen"
        if "pflege" in raw or "koerper" in raw or "körper" in raw:
            return "pfleger_koerper"
        if "routine" in raw:
            return "routinen"
        if "vorbereit" in raw:
            return "vorbereitung"
            
        return "alltag_schwangerschaft"

    def normalize_hook(self, raw_hook: str) -> str:
        """Map raw hook text to hook_pattern based on approved rhetorical frame rules."""
        raw = str(raw_hook or "").strip().lower()
        if not raw:
            return "unclear"

        # 1. second_person_appeal (Viewer's recognition/experience)
        # We prioritize this over direct_question for "Kennst du das?"
        if any(w in raw for w in ["du ", "dir ", "eure", "kennst"]):
            return "second_person_appeal"

        # 2. direct_question (Interrogative addressed to viewer, even if topic-led)
        if "?" in raw:
            return "direct_question"

        # 3. list_intro (Explicit count or "Dinge ..." list framing)
        # Plural numbers or 'dinge' family
        if any(w in raw for w in ["dinge", "tipps", "tricks", "gründe", "gründe"]):
            # Rule A/3: "Eine Sache" is NOT a list intro
            if "eine sache" not in raw:
                return "list_intro"

        # 4. topic_fragment (Static topic labels or noun phrases)
        # Check for typical fragment patterns (short, no verbs, or title-like)
        # Even if they have "mein", if they act as a label: topic_fragment
        fragments = [
            "werdende mama", "appetit", "immer das gleiche", 
            "wachhalten", "schwangerschaftsmorgen", "geburtsvorbereitung"
        ]
        if any(f in raw for f in fragments):
            return "topic_fragment"

        # 5. contrarian_claim (Assertion against implied default)
        if any(w in raw for w in ["vermisse", "anders", "plötzlich"]):
            return "contrarian_claim"

        # 6. first_person_moment (Speaker's experience/observation)
        if any(w in raw for w in ["ich", "mein", "mir"]):
            return "first_person_moment"

        return "unclear"


    def map_record(self, fields: Mapping[str, any]) -> dict[str, str]:
        """Apply all normalizations to a record's fields."""
        return {
            "cta_typ_norm": self.normalize_cta(fields.get("cta_typ")),
            "format_typ_norm": self.normalize_format(fields.get("format_typ")),
            "serie_thema_norm": self.normalize_serie(fields.get("serie_thema")),
            "hook_pattern": self.normalize_hook(fields.get("hook_kurz")),
        }
