from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from operator_core.core.analysis_foundation.models import AnalysisFoundationResult
    from operator_core.integrations.openai_service import OpenAIService

_log = logging.getLogger("operator_core.core.content_ops.duplicate_guard")

# Used by classify_idea_mode to detect pronoun-free concrete lived moments.
_IDEA_SCENE_CONNECTORS: frozenset[str] = frozenset({
    "beim", "im", "vor", "nach", "während", "seit", "wegen",
})
_IDEA_CONCRETE_VERBS: frozenset[str] = frozenset({
    "sitzen", "stehen", "gehen", "liegen",
    "brauchen", "pause", "checken", "mitmuss",
    "meldet", "spüren", "merken", "schaffen",
})

# Detail terms and morphological near-forms allowed by MIRROR fidelity check.
# Only direct scene/action equivalents are allowed — NOT semantic rewrites.
_MIRROR_NEAR_FORMS: dict[str, frozenset[str]] = {
    "kochen":   frozenset({"koch"}),
    "schwindel": frozenset({"schwindel", "schwindelig"}),
    "schwindelig": frozenset({"schwindel", "schwindelig"}),
    "supermarkt": frozenset({"supermarkt"}),
    "einkaufen": frozenset({"einkauf", "einkaufen"}),
    "rausgehen": frozenset({"rausgeh", "raus gehen"}),
    "doppelt":  frozenset({"doppelt", "zweimal", "zwei mal"}),
    "sitzen":   frozenset({"sitz", "hinsetzen", "hinsetz"}),
    "stehen":   frozenset({"steh"}),
    "gehen":    frozenset({"geh"}),
    "liegen":   frozenset({"lieg"}),
    "brauchen": frozenset({"brauch"}),
    "pause":    frozenset({"paus"}),
    "checken":  frozenset({"check", "prüf", "kontrollier"}),
    "mitmuss":  frozenset({"mitmuss", "mit muss", "mitnehm", "mitbring", "mitmüss", "einpack"}),
    "rücken":   frozenset({"rücken", "ruecken"}),
    "meldet":   frozenset({"meld"}),
    "spüren":   frozenset({"spür"}),
    "merken":   frozenset({"merk"}),
    "schaffen": frozenset({"schaff"}),
    "zehn":     frozenset({"zehn", "10"}),
    "minuten":  frozenset({"minut"}),
}

@dataclass(frozen=True)
class RiskEvaluation:
    level: str  # "low", "medium", "high"
    reason: str
    blocking_items: tuple[str, ...]

@dataclass(frozen=True)
class ThemeRiskEvaluation:
    saturated: bool
    cluster_name: str  # "" if no cluster matched or not saturated
    blocking_items: tuple[str, ...]
    reason: str

@dataclass(frozen=True)
class CoreRepeatEvaluation:
    repeated: bool
    blocking_items: tuple[str, ...]
    reason: str

@dataclass(frozen=True)
class IdeaHistoryReference:
    text: str
    source: str

# Keyword families for everydayengel early-stage theme cooldown.
# If ANY cluster keyword appears in both candidate and a recent reference → that
# reference counts as same-cluster content. One hit is enough to trigger saturation.
_THEME_CLUSTERS: dict[str, tuple[str, ...]] = {
    "muedigkeit_energie": (
        "müde", "müdigkeit", "wach", "energie", "erschöpfung", "erschöpft",
        "schlaf", "powernap", "power nap", "erholung", "ausgeruht", "energielos",
    ),
    "kliniktasche_vorbereitung": (
        "kliniktasche", "krankenhaustasche", "packliste", "krankenhaus",
        "klinik", "geburtstasche", "geburtsplan",
    ),
    "morgenroutine": (
        "morgenroutine", "morgen routine", "aufwachen", "start in den tag",
        "morgens aufstehen", "tagesstart",
    ),
    "babybauch_koerper": (
        "babybauch", "bauch wächst", "körpergefühl", "körper verändert",
        "bauch zeigen", "körperveränderung",
    ),
}

# Per-cluster "nearby consequence space" — pivot candidates should stay in this space.
# Keyword presence earns a bonus; anti-consequence presence earns a penalty.
_THEME_CONSEQUENCE_SPACES: dict[str, tuple[str, ...]] = {
    "muedigkeit_energie": (
        # Pausing / taking breaks
        "pause", "pausen", "pausieren", "kurz hin", "hinsetzen", "ausruhen", "hinlegen",
        # Rhythm / pacing change
        "rhythmus", "tempo", "takt", "langsam", "verlangsam", "pacing", "schritt",
        # Body-limit signals
        "genug", "limit", "grenze", "kraft", "ausdauer",
        # Tasks feel heavier / shorter
        "schwerer", "anstrengend", "aufwand", "kraftaufwand", "kurzatm",
        # Stamina / concentration
        "konzentration", "fokus", "überforder", "overwhelm",
        # Planning around energy, daily load
        "planung", "einteilen", "haushalt", "aufgabe", "tagesablauf", "routine",
        "früher fertig", "früher schlapp", "schnell erschöpf",
    ),
    "kliniktasche_vorbereitung": (
        "packen", "vorbereiten", "checkliste", "liste", "organisieren",
        "geburt", "krankenhaus", "realität", "realisiert",
    ),
    "morgenroutine": (
        "morgen", "aufstehen", "morgens", "tag beginnt", "früh",
        "routine", "ablauf", "vorbereitung",
    ),
    "babybauch_koerper": (
        "bauch", "wächst", "form", "größe", "anpassen", "verändert",
    ),
}

# Per-cluster drift-penalty keywords — pivot into these spaces is penalized.
_THEME_ANTI_CONSEQUENCE: dict[str, tuple[str, ...]] = {
    "muedigkeit_energie": (
        # Unrelated physical friction — shoes/bending are NOT energy consequences
        "schuhe", "schuh", "schuhband", "schürsenkel", "schnürsenkel",
        "bücken", "bückt", "gebückt",
        # Wardrobe / clothing topic
        "jeans", "kleidung", "kleiderschrank", "outfit", "garderob", "mode",
        # Room setup
        "babyzimmer", "einrichten", "deko", "ordnen", "ordnungshelfer",
        # Cooking as the primary topic (valid as a scene, but not a Müdigkeit consequence)
        # N.B. "Kochen macht müde" IS a consequence; penalty only when cooking is the main topic
    ),
}

# Candidate ideas containing these markers are exempt from theme cooldown —
# they represent deliberate follow-ups or meaningful updates.
_FOLLOWUP_MARKERS: tuple[str, ...] = (
    "update", "follow-up", "followup", "teil 2", "teil 3", "folge 2",
    "ergänzung", "fortsetzung", "neue erkenntnisse", "aktuell neu",
)

class DuplicateRiskGuard:
    """
    Evaluates if a new idea is too similar to recent content, existing drafts, or recent ideas.
    
    Early-stage channel logic: 
    - Novelty is prioritized over family reuse.
    - Stricter on same-core-video repetition.
    """
    
    def __init__(self, openai_service: "OpenAIService | None" = None) -> None:
        self._openai_service = openai_service

    def evaluate(
        self,
        *,
        project_key: str,
        candidate_idea: str,
        foundation_result: "AnalysisFoundationResult",
        recent_posts: Sequence[str] = (),
        recent_drafts: Sequence[str] = (),
        recent_ideas: Sequence[str] = (),
        recent_history: Sequence[IdeaHistoryReference] = (),
    ) -> RiskEvaluation:
        """
        Determines the duplicate risk level for a candidate idea.
        Combines heuristic keyword matching with word-overlap similarity.
        """
        # 1. Collect all reference items
        # Each reference is a tuple: (text, source_category)
        references: list[tuple[str, str]] = []
        
        # From platform snapshots (recent posts)
        for snapshot in foundation_result.analysis_snapshots:
            for line in snapshot.analytics_summary_lines:
                if "Hook examples:" in line:
                    # Strip prefix
                    clean_line = line.replace("Hook examples:", "").strip()
                    # Extract individual hooks
                    hooks = clean_line.split("|")
                    for h in hooks:
                        h_clean = h.strip()
                        if h_clean:
                            references.append((h_clean, "recent_post"))
        
        # From weekly analysis
        if foundation_result.weekly_analysis:
            wa = foundation_result.weekly_analysis
            for w in wa.key_winners:
                references.append((w, "weekly_winner"))
            for wp in wa.weak_patterns:
                references.append((wp, "weekly_weak_pattern"))

        # From recent operational state (passed from service)
        for d in recent_drafts:
            references.append((d, "existing_draft"))
        for i in recent_ideas:
            references.append((i, "recent_idea"))

        # From explicit posted content state
        for p in recent_posts:
            references.append((p, "recent_post"))
        for ref in recent_history:
            if ref.text.strip():
                references.append((ref.text, ref.source or "recent_history"))

        # 2. Matching Logic
        candidate_low = candidate_idea.lower()
        
        high_risk_found = []
        medium_risk_found = []

        # Heuristic: specific high-risk keywords for everydayengel
        # These are topics we want to avoid repeating too closely in early stage
        high_risk_keywords = ["müde", "müdigkeit", "wach bleiben", "schlaf", "kliniktasche", "krankenhaustasche"]

        for ref_text, category in references:
            similarity = self._calculate_similarity(candidate_idea, ref_text)
            ref_low = ref_text.lower()
            core_repeat = self._is_same_core(candidate_idea, ref_text)
            
            # Check for keyword overlap
            keyword_match = any(k in candidate_low and k in ref_low for k in high_risk_keywords)
            
            # Strict thresholds for early-stage channel.
            # keyword_match uses a lower threshold (0.15) because the registered
            # keywords are highly specific (müdigkeit, kliniktasche, …) — even a
            # small token overlap combined with a keyword hit is reliably same-core.
            # Without the lower threshold, long-form candidates (many filler tokens)
            # dilute similarity below 0.30 and slip past the guard.
            if core_repeat or similarity >= 0.5 or (similarity >= 0.15 and keyword_match):
                high_risk_found.append(f"[{category}] {ref_text} (Sim: {similarity:.2f})")
            elif similarity >= 0.25:
                medium_risk_found.append(f"[{category}] {ref_text} (Sim: {similarity:.2f})")

        if high_risk_found:
            return RiskEvaluation(
                level="high",
                reason="Zu hohe Ähnlichkeit mit existierenden Inhalten oder Entwürfen. Das Projekt ist in einer frühen Phase; Novelty hat Vorrang vor Wiederholung.",
                blocking_items=tuple(high_risk_found[:3])
            )
            
        if medium_risk_found:
            return RiskEvaluation(
                level="medium",
                reason="Mittleres Duplikatsrisiko. Die Idee sollte geschärft oder um einen neuen Aspekt ergänzt werden.",
                blocking_items=tuple(medium_risk_found[:3])
            )

        return RiskEvaluation(level="low", reason="Geringes Duplikatsrisiko.", blocking_items=())

    def evaluate_core_repeat(
        self,
        *,
        candidate_idea: str,
        foundation_result: "AnalysisFoundationResult",
        recent_posts: Sequence[str] = (),
        recent_drafts: Sequence[str] = (),
        recent_ideas: Sequence[str] = (),
        recent_history: Sequence[IdeaHistoryReference] = (),
    ) -> CoreRepeatEvaluation:
        """Deterministic same-core check for already posted or recently generated idea moments."""
        references: list[tuple[str, str]] = []
        for snapshot in foundation_result.analysis_snapshots:
            for line in snapshot.analytics_summary_lines:
                if "Hook examples:" in line:
                    for h in line.replace("Hook examples:", "").strip().split("|"):
                        h_clean = h.strip()
                        if h_clean:
                            references.append((h_clean, "recent_post"))
        for p in recent_posts:
            references.append((p, "recent_post"))
        for d in recent_drafts:
            references.append((d, "existing_draft"))
        for i in recent_ideas:
            references.append((i, "recent_idea"))
        for ref in recent_history:
            if ref.text.strip():
                references.append((ref.text, ref.source or "recent_history"))

        blocking = [
            f"[{category}] {ref_text}"
            for ref_text, category in references
            if self._is_same_core(candidate_idea, ref_text)
        ]
        if blocking:
            return CoreRepeatEvaluation(
                repeated=True,
                blocking_items=tuple(blocking[:3]),
                reason="Gleicher Alltagsmoment oder gleiche Reibung wie bereits gepostete/gespeicherte Idee.",
            )
        return CoreRepeatEvaluation(repeated=False, blocking_items=(), reason="")

    def _calculate_similarity(self, a: str, b: str) -> float:
        """Overlap ratio of meaningful tokens, including simple substring matching."""
        # Simple stop words filter for German/English common filler
        stop_words = {"und", "oder", "aber", "für", "mit", "den", "dem", "das", "die", "der", "ein", "eine", "einer", "eines"}
        
        def tokenize(text: str) -> set[str]:
            # Normalize and keep only words > 2 chars
            words = re.findall(r'\w+', text.lower())
            return {w for w in words if len(w) > 2 and w not in stop_words}
        
        set_a = tokenize(a)
        set_b = tokenize(b)
        if not set_a or not set_b:
            return 0.0
        
        # Count intersections including substring matches for compounding (e.g. Morgenroutine vs Routine)
        matches = 0
        used_b = set()
        for wa in set_a:
            found_match = False
            for wb in set_b:
                if wb in used_b:
                    continue
                
                # Exact match
                if wa == wb:
                    found_match = True
                # Substring match for longer words (compounding)
                elif (len(wa) > 4 and wa in wb) or (len(wb) > 4 and wb in wa):
                    found_match = True
                
                if found_match:
                    matches += 1
                    used_b.add(wb)
                    break
        
        # Use overlap ratio (matches divided by size of smaller set)
        return matches / min(len(set_a), len(set_b))

    _CORE_GROUPS: dict[str, tuple[str, ...]] = {
        "scene_cooking": ("kochen", "küche", "kueche"),
        "scene_supermarket": ("supermarkt", "einkaufen", "einkauf"),
        "scene_leaving": ("rausgehen", "raus gehen", "losgehen", "tür", "tuer"),
        "scene_standing": ("stehen", "stand", "zehn minuten", "10 minuten"),
        "action_sitting": ("sitzen", "hinsetzen", "setz", "sitz"),
        "action_pause": ("pause", "pausen", "pausieren", "ausruhen"),
        "action_checking": ("doppelt", "zweimal", "zwei mal", "checken", "prüfen", "pruefen", "kontrollieren"),
        "action_packing": ("mitmuss", "mit muss", "mitnehmen", "mitnehm", "einpacken", "einpack"),
        "symptom_dizziness": ("schwindel", "schwindelig"),
        "symptom_back": ("rücken", "ruecken"),
        "symptom_tired": ("müde", "mued", "müdigkeit", "erschöpfung", "erschoepfung", "wach bleiben"),
        "prep_clinic_bag": ("kliniktasche", "krankenhaustasche", "geburtstasche"),
    }
    _CORE_SCENE_GROUPS: frozenset[str] = frozenset({
        "scene_cooking", "scene_supermarket", "scene_leaving", "scene_standing",
    })
    _CORE_FRICTION_GROUPS: frozenset[str] = frozenset({
        "action_sitting", "action_pause", "action_checking", "action_packing",
        "symptom_dizziness", "symptom_back", "symptom_tired", "prep_clinic_bag",
    })

    @classmethod
    def _core_markers(cls, text: str) -> set[str]:
        low = text.lower()
        return {
            group
            for group, needles in cls._CORE_GROUPS.items()
            if any(needle in low for needle in needles)
        }

    @classmethod
    def _is_same_core(cls, candidate: str, reference: str) -> bool:
        candidate_markers = cls._core_markers(candidate)
        reference_markers = cls._core_markers(reference)
        overlap = candidate_markers & reference_markers
        if len(overlap) >= 3:
            return True
        return (
            bool(overlap & cls._CORE_SCENE_GROUPS)
            and bool(overlap & cls._CORE_FRICTION_GROUPS)
        )

    def evaluate_theme_risk(
        self,
        *,
        candidate_idea: str,
        recent_drafts: Sequence[str] = (),
        recent_ideas: Sequence[str] = (),
    ) -> ThemeRiskEvaluation:
        """
        Checks whether the candidate's theme cluster is already saturated in recent content.
        Stricter than same-core detection: one matching reference is enough to block.
        Candidates with explicit follow-up markers are exempt.
        """
        candidate_low = candidate_idea.lower()

        # Exempt justified follow-ups
        if any(marker in candidate_low for marker in _FOLLOWUP_MARKERS):
            return ThemeRiskEvaluation(
                saturated=False,
                cluster_name="",
                blocking_items=(),
                reason="Gerechtfertigtes Follow-up oder Update erkannt – Theme-Cooldown übersprungen.",
            )

        # Detect candidate's cluster
        candidate_cluster: str | None = None
        for cluster_name, keywords in _THEME_CLUSTERS.items():
            if any(kw in candidate_low for kw in keywords):
                candidate_cluster = cluster_name
                break

        if candidate_cluster is None:
            return ThemeRiskEvaluation(
                saturated=False,
                cluster_name="",
                blocking_items=(),
                reason="Kein bekanntes Theme-Cluster erkannt – kein Theme-Cooldown.",
            )

        # Check saturation: does ANY recent reference belong to the same cluster?
        cluster_keywords = _THEME_CLUSTERS[candidate_cluster]
        blocking: list[str] = []
        sources: list[tuple[str, str]] = [
            *((r, "recent_draft") for r in recent_drafts),
            *((r, "recent_idea") for r in recent_ideas),
        ]
        for ref, source in sources:
            if any(kw in ref.lower() for kw in cluster_keywords):
                blocking.append(f"[{source}] {ref}")
                if len(blocking) >= 3:
                    break

        if blocking:
            return ThemeRiskEvaluation(
                saturated=True,
                cluster_name=candidate_cluster,
                blocking_items=tuple(blocking),
                reason=(
                    f"Theme-Cluster '{candidate_cluster}' bereits in aktuellen Inhalten vertreten. "
                    "Frühe Kanalphase: Novelty hat Vorrang – Pivot zu anderem Themenbereich erforderlich."
                ),
            )

        return ThemeRiskEvaluation(
            saturated=False,
            cluster_name=candidate_cluster,
            blocking_items=(),
            reason="Theme-Cluster erkannt, aber noch nicht saturiert.",
        )

    def is_pivot_eligible(self, candidate: str, cluster_name: str) -> bool:
        """
        Hard eligibility gate for pivot candidates.

        For clusters with a defined consequence space:
          - MUST contain ≥1 consequence keyword (stays near the cluster's burden)
          - MUST NOT contain any anti-consequence keyword (hasn't drifted away)
        For unknown/undefined clusters: all candidates are eligible.

        This is evaluated BEFORE scoring — ineligible candidates are removed
        from competition entirely, not just penalised.
        """
        if cluster_name not in _THEME_CONSEQUENCE_SPACES:
            return True
        low = candidate.lower()
        anti_kws = _THEME_ANTI_CONSEQUENCE.get(cluster_name, ())
        if any(kw in low for kw in anti_kws):
            return False
        consequence_kws = _THEME_CONSEQUENCE_SPACES[cluster_name]
        return any(kw in low for kw in consequence_kws)

    def consequence_space_score(self, candidate: str, cluster_name: str) -> float:
        """
        Reward/penalty based on how well a pivot candidate aligns with the
        consequence space of the saturated cluster.

        Returns:
          +1.5  when candidate contains ≥1 consequence keyword for the cluster
          -1.5  when candidate contains ≥1 anti-consequence keyword (drifted too far)
           0.0  when cluster has no definition or neither signal fires
        Anti-consequence penalty takes precedence over consequence bonus.
        """
        if cluster_name not in _THEME_CONSEQUENCE_SPACES:
            return 0.0
        low = candidate.lower()
        anti_kws = _THEME_ANTI_CONSEQUENCE.get(cluster_name, ())
        if any(kw in low for kw in anti_kws):
            return -1.5
        consequence_kws = _THEME_CONSEQUENCE_SPACES[cluster_name]
        if any(kw in low for kw in consequence_kws):
            return 1.5
        return 0.0

    def generate_theme_pivot_alternatives(
        self,
        *,
        project_key: str,
        original_idea: str,
        theme_risk: ThemeRiskEvaluation,
        platform: str,
        prompt_context: str = "",
        sharpen_mode: bool = False,
    ) -> list[str]:
        """
        Generates 3 fresh ideas OUTSIDE the saturated theme cluster.
        Used when theme-cooldown fires instead of same-core duplicate guard.
        """
        if self._openai_service is None:
            return ["Theme-Pivot A: [Stub]", "Theme-Pivot B: [Stub]", "Theme-Pivot C: [Stub]"]

        blocked_cluster_desc = theme_risk.cluster_name.replace("_", "/")

        # Build consequence-space guidance block when cluster is known
        c_space_kws = _THEME_CONSEQUENCE_SPACES.get(theme_risk.cluster_name, ())
        anti_kws = _THEME_ANTI_CONSEQUENCE.get(theme_risk.cluster_name, ())
        if c_space_kws:
            c_space_block = (
                "KONSEQUENZ-RAUM: Bleib thematisch nahe an der Konsequenz des gesperrten Clusters.\n"
                f"Erlaubte Nachbar-Themen (wenigstens eines muss erkennbar sein): "
                f"{', '.join(c_space_kws[:10])}\n"
                + (
                    f"VERBOTEN (zu weit vom Konsequenz-Raum entfernt): {', '.join(anti_kws[:6])}\n"
                    if anti_kws else ""
                )
                + "Wechsle NICHT zu einem komplett anderen Körperproblem nur weil es filmtauglich ist.\n\n"
            )
        else:
            c_space_block = ""

        if sharpen_mode and prompt_context.strip():
            anker_block = (
                f"SCHÄRFEN-MODUS: Die Nutzerin hat eine konkrete Szene beschrieben: '{prompt_context}'\n"
                "Generiere Ideen AUSSERHALB des gesperrten Clusters, aber BLEIB in diesem Problemraum.\n"
                "Wechsle NICHT zu Schuhen, Kleidung, Rezepten oder generischem Symptom-Reden.\n\n"
            )
        elif prompt_context.strip():
            anker_block = (
                f"ANKER AUS DEM USER-PROMPT (wenn vorhanden, bleib in dieser Szene oder diesem Problemraum):\n"
                f"'{prompt_context}'\n\n"
            )
        else:
            anker_block = ""

        system_prompt = (
            "Du hilfst Julia, ehrliche Alltagsmomente aus der Schwangerschaft für den TikTok-Kanal everydayengel zu formulieren.\n"
            "Julias Stil: direkt, alltagsnah, erste Person — keine generischen Schwangerschafts-Tipps, kein Content-Sprech.\n\n"
            "Eine Idee wurde blockiert, weil ihr Theme-Cluster bereits vertreten ist.\n"
            f"GESPERRTES THEME-CLUSTER: '{blocked_cluster_desc}'\n"
            f"BLOCKIERENDE REFERENZEN: {', '.join(theme_risk.blocking_items)}\n\n"
            "DEINE AUFGABE: Generiere 3 frische Ideen AUSSERHALB dieses Theme-Clusters.\n\n"
            "HARD RULES — ABSOLUT VERBOTEN (keine Ausnahmen, keine Umgehung):\n"
            f"- NICHT: Alles rund um '{blocked_cluster_desc}' – auch keine Synonyme oder Micro-Angles\n"
            "- NICHT: Generische Schwangerschafts-Lifestyle-Themen (Babynamen, Standard-Ernährung, allgemeine Tipps)\n"
            "- NICHT: Kleiderschrank, Jeans, Outfits, Room-Setup, Deko, Ordnungshelfer\n"
            "- NICHT: Umbrella-Konzepte, Dachthemen, Sammelkategorien\n"
            "- NICHT: 'Versteckte Helden', 'stille Helfer', 'kleine Begleiter' oder ähnliche Objekt-Buckets\n"
            "- NICHT: 'Dinge die unverzichtbar werden', 'Dinge die plötzlich wichtig sind' — zu generisch\n"
            "- NICHT: 'Schwangerschaftsalltag im Überblick', 'verschiedene Aspekte', 'lustigste Momente'\n"
            "- NICHT: Irgendetwas das wie eine Listicle-Überschrift klingt\n\n"
            "WAS STATTDESSEN GEFORDERT IST — jede Idee muss:\n"
            "- Einen einzigen konkreten Alltagsmoment beschreiben\n"
            "- Körperliche oder praktische Reibung in EINER echten Szene haben\n"
            "- Einen klaren Vorher-Nachher-Shift zeigen ODER einen überraschenden Körper-Moment\n"
            "- In Julias eigener Stimme formuliert sein (erste Person, ein Satz)\n"
            "- Sich wie ein ehrliches Geständnis anfühlen, nicht wie ein Thema\n\n"
            "SOFT GUIDANCE — bevorzuge diese Bereiche (kein Zwang, NOVELTY ZUERST):\n"
            "- Gedanken / ehrliche Beobachtungen aus dem Alltag\n"
            "- Kleine Erleichterungen in einem konkreten, anderen Problembereich\n"
            "- Veränderte Praktikabilität im Alltag — ein Verhalten das sich sichtbar gewandelt hat\n\n"
            "STIMME (immer einhalten):\n"
            "- Erste Person ('ich', 'mir', 'mein') — nicht dritte Person oder neutral\n"
            "- Ein einziger Satz — kein zweiter Satz, keine Regieanweisung\n"
            "- Kein Imperativ ('Filme', 'Versuch', 'Zeig', 'Schau') — Julia spricht, sie gibt keine Anleitung\n"
            "- Kein Headline-Stil mit Doppelpunkt ('Die Tür-Challenge: ...')\n"
            "- Kein Label-Prefix ('Mini-Szene', 'Challenge', 'Talking Head', 'ehrlicher Einblick')\n\n"
            "SO SOLL ES KLINGEN (Zielton — nicht kopieren, nur als Stimmreferenz):\n"
            "✓ 'Mir ist erst letzte Woche aufgefallen, dass ich beim Aufstehen vom Sofa inzwischen eine Hand brauche.'\n"
            "✓ 'Meine Handtasche passt nicht mehr über den Bauch — ich trage sie jetzt anders.'\n"
            "✓ 'Zum ersten Mal hab ich beim Bücken wirklich Hilfe gebraucht.'\n\n"
            "NICHT SO:\n"
            "✗ 'Filme eine Szene in der du zeigst wie ...'\n"
            "✗ 'Die alltägliche Tür-Challenge: wenn der Bauch ...'\n"
            "✗ 'Mini-Szene: Ich versuche ...'\n\n"
            + c_space_block
            + anker_block
            + "Antworte auf Deutsch, FORMAT:\n"
            "1. <Idee A>\n"
            "2. <Idee B>\n"
            "3. <Idee C>\n"
            "KEINE Erklärungen, kein Smalltalk."
        )

        response = self._openai_service.complete_messages(
            system_prompt=system_prompt,
            user_prompt=f"Generiere 3 frische Ideen außerhalb des gesperrten Theme-Clusters für {platform}.",
            temperature=0.9,
        )

        return self._clean_generated_ideas(response.output_text)

    def generate_alternatives(
        self,
        *,
        project_key: str,
        original_idea: str,
        risk_evaluation: RiskEvaluation,
        platform: str,
        prompt_context: str = "",
        sharpen_mode: bool = False,
    ) -> list[str]:
        """
        Generates 3 alternative angles within the same family but with clear novelty.
        """
        if self._openai_service is None:
            return ["Alternative A: [Stub]", "Alternative B: [Stub]", "Alternative C: [Stub]"]

        if sharpen_mode and prompt_context.strip():
            anker_block = (
                f"SCHÄRFEN-MODUS: Die Nutzerin hat diesen konkreten Alltagsmoment beschrieben: '{prompt_context}'\n"
                "Deine Aufgabe: Schärfe GENAU diesen Moment — ersetze ihn NICHT durch ein verwandtes Thema.\n\n"
                "KRITISCH: Bewahre die EXAKTE KERN-REIBUNG.\n"
                "- Wenn Pausen beim Einkaufen → bleib bei Pausen beim Einkaufen\n"
                "- Wechsle NICHT zu: Aufzug, Begegnungen im Laden, anderen Orten, Nebenaspekten\n"
                "- Wechsle NICHT zu: Schuhen, Rezepten, Kleidung, generischem Symptom-Reden\n"
                "- Ersetze NICHT die Kern-Reibung durch eine benachbarte Sub-Szene\n\n"
                "NICHT ERLAUBT: Ideen die NUR thematisch verwandt sind, aber eine ANDERE Reibung beschreiben.\n"
                "Das Ergebnis muss sich anfühlen wie 'derselbe Moment + dieselbe Reibung, nur filmtauglicher'.\n\n"
            )
        elif prompt_context.strip():
            anker_block = (
                f"ANKER AUS DEM USER-PROMPT (wenn vorhanden, bleib in dieser Szene):\n"
                f"'{prompt_context}'\n\n"
            )
        else:
            anker_block = ""

        system_prompt = (
            "Du hilfst Julia, ehrliche Alltagsmomente aus der Schwangerschaft als TikTok-Ideen zu formulieren.\n"
            "Julias Stil: direkt, alltagsnah, erste Person — keine generischen Schwangerschafts-Tipps, kein Content-Sprech.\n\n"
            "Die Nutzerin hat eine Idee vorgeschlagen, die einem bereits geposteten Video, einem Entwurf oder einer kürzlich generierten Idee zu ähnlich ist.\n"
            "DEINE AUFGABE: Erstelle 3 alternative Ideen im selben Problemraum, aber mit klarer NEUARTIGKEIT.\n\n"
            "HARD RULES (keine Ausnahmen):\n"
            "- NICHT: Shopping, Supermarkt, Produktlisten, Outfit, Kleiderschrank, Jeans, Deko, Room-Setup\n"
            "- NICHT: Generische Lifestyle-Tipps, allgemeine Ratgeber-Töne oder abstrakte Übersichten\n"
            "- NICHT: Meta-Konzepte, Spielereien oder weit hergeholte Perspektivwechsel\n\n"
            "SOFT GUIDANCE – bevorzuge diese Everydayengel-Fit-Signale:\n"
            "- Kleine konkrete Alltagsbeobachtung in Julias eigener Stimme\n"
            "- Körperliche oder praktische Reibung in einer echten Szene\n"
            "- Sichtbar verändertes Verhalten oder veränderte Praktikabilität\n\n"
            "STIMME (immer einhalten):\n"
            "- Erste Person ('ich', 'mir', 'mein') — nicht dritte Person oder neutral\n"
            "- Ein einziger Satz — kein zweiter Satz, keine Regieanweisung\n"
            "- Kein Imperativ ('Filme', 'Versuch', 'Zeig', 'Schau') — Julia spricht, sie gibt keine Anleitung\n"
            "- Kein Headline-Stil mit Doppelpunkt ('Die Tür-Challenge: ...')\n"
            "- Kein Label-Prefix ('Mini-Szene', 'Challenge', 'Talking Head', 'ehrlicher Einblick')\n\n"
            "SO SOLL ES KLINGEN (Zielton — nicht kopieren):\n"
            "✓ 'Mir ist erst letzte Woche aufgefallen, dass ich beim Aufstehen vom Sofa inzwischen eine Hand brauche.'\n"
            "✓ 'Mein Rücken meldet sich jetzt schon nach zehn Minuten Stehen — das kannte ich vorher nicht.'\n\n"
            "NICHT SO:\n"
            "✗ 'Filme eine Szene in der du zeigst wie ...'\n"
            "✗ 'Die alltägliche Tür-Challenge: wenn der Bauch ...'\n"
            "✗ 'Mini-Szene: Ich versuche ...'\n\n"
            "Kontext der blockierten Idee:\n"
            f"Idee: {original_idea}\n"
            f"Grund der Sperre: {risk_evaluation.reason}\n"
            f"Blockierende Referenzen: {', '.join(risk_evaluation.blocking_items)}\n\n"
            + anker_block
            + "Antworte ausschließlich auf Deutsch im folgenden Format:\n"
            "1. <Idee A>\n"
            "2. <Idee B>\n"
            "3. <Idee C>\n"
            "KEINE Erklärungen, kein Smalltalk."
        )

        response = self._openai_service.complete_messages(
            system_prompt=system_prompt,
            user_prompt=f"Generiere 3 frische Alternativen für {platform}.",
            temperature=0.8
        )

        return self._clean_generated_ideas(response.output_text)

    @staticmethod
    def _clean_generated_ideas(output_text: str) -> list[str]:
        lines = [line.strip() for line in output_text.splitlines() if line.strip()]
        cleaned: list[str] = []
        for line in lines:
            text = re.sub(r"^\d+\.\s*", "", line).strip()
            text = text.replace("*", "").strip()
            text = re.sub(r"^(?:idee|angle|theme-pivot|alternative)\s*[a-z0-9_-]*\s*:\s*", "", text, flags=re.IGNORECASE).strip()
            if text:
                cleaned.append(text)
        return cleaned[:3]


class IdeaDistiller:
    """
    Lightweight heuristic distillation of a selected idea to its single strongest moment.

    Strips production wrappers (Talking Head, Dann 2–3…, CTA, ehrlicher Einblick),
    trailing "und zeige wie" clauses, and multi-sentence production instructions.
    Preserves meaning, scene anchors, and the core friction.
    Operates purely on text — no LLM call, no I/O.
    """

    # Label prefixes to strip from the start (case-insensitive)
    _PREFIX_RE = re.compile(
        r"^(?:ehrlicher?\s+(?:einblick|monolog|blick|ton)|"
        r"mini-?doku\s*:|mini-?szene\s*:|talking\s+head\s*:|"
        r"challenge\s*:|ein\s+neuer\s+blick\s*:|monolog\s*:|idee\s*:)\s*",
        re.IGNORECASE,
    )

    # Headline-colon: strips "Die X-Challenge: " / "Ein ehrlicher Blick: " style labels.
    # Matches only when the pre-colon text (≤60 chars) contains a known packaging word.
    _HEADLINE_COLON_RE = re.compile(
        r"^[^:]{3,60}\b(?:challenge|einblick|blick|mini.?szene|monolog|szene)[^:]*:\s*",
        re.IGNORECASE,
    )

    # Inline trailing clause patterns — strip from match point to end of string
    _INLINE_STRIP = [
        # "und zeige, wie ich..." / "und zeige ich..."
        re.compile(r"\s*,?\s*und\s+zeige[,\s].*$", re.IGNORECASE | re.DOTALL),
        # "– ich zeige, wie..." / "- ich zeige wie..."
        re.compile(r"\s*[–\-]\s*ich\s+zeige[,\s].*$", re.IGNORECASE | re.DOTALL),
        # "und wie ich trotzdem..."
        re.compile(r"\s*,?\s*und\s+wie\s+ich\s+trotzdem.*$", re.IGNORECASE | re.DOTALL),
        # ". Statt zu sitzen/stehen/gehen [production]"
        re.compile(r"\.\s+Statt\s+zu\s+(?:sitzen|stehen|gehen).*$", re.IGNORECASE | re.DOTALL),
        # "| Ehrlicher Einblick / CTA / Talking Head …"
        re.compile(
            r"\s*\|\s*(?:ehrlicher?\s+einblick|cta\b|talking\s+head|"
            r"frage\s+am\s+ende|aufruf\s+zur).*$",
            re.IGNORECASE | re.DOTALL,
        ),
        # "– Filme eine Szene …" / "– Versuch mal …" / "– Zeig wie …"
        re.compile(
            r"\s*[–\-]\s*(?:filme|versuch|zeig|schau|probier)\w*\b.*$",
            re.IGNORECASE | re.DOTALL,
        ),
    ]

    # Patterns that mark a sentence as a production instruction (not the core idea)
    _PRODUCTION_SENT_RE = re.compile(
        r"^(?:dann\s|statt\s+zu\s|und\s+zeige|ich\s+zeige[,\s]|"
        r"talking\s+head|cta\b|frage\s+am\s+ende|frage\s+an\s+die\s|"
        r"das\s+passt|mini-?doku|greife\s+ich\s+nach|"
        r"ehrlicher?\s+einblick|so\s+zeige\s+ich|"
        r"filme\s|versuch[,\s]|versuch\s+mal\b|schau\s+mal\b|probier\w*\s)",
        re.IGNORECASE,
    )

    # Sentence boundary splitter
    _SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

    def distill(self, idea: str, *, anchor_tokens: tuple[str, ...] = ()) -> str:
        """
        Return the single strongest-moment version of the idea.
        If no wrapper patterns are detected the input is returned unchanged.
        """
        text = idea.strip()
        if not text:
            return text

        # 1. Strip label prefix
        text = self._PREFIX_RE.sub("", text).strip()

        # 1b. Strip headline-colon label ("Die X-Challenge: content" → "content")
        text = self._HEADLINE_COLON_RE.sub("", text).strip()

        # 2. Strip inline trailing mechanics
        for pat in self._INLINE_STRIP:
            text = pat.sub("", text).strip()

        # 3. Multi-sentence: drop sentences that are production instructions
        sentences = [s.strip() for s in self._SENT_SPLIT.split(text) if s.strip()]
        if len(sentences) >= 2:
            core = [s for s in sentences if not self._PRODUCTION_SENT_RE.match(s)]
            if core:
                if anchor_tokens:
                    # Prefer sentence with most anchor token hits
                    core.sort(
                        key=lambda s: sum(1 for a in anchor_tokens if a in s.lower()),
                        reverse=True,
                    )
                text = core[0]
            else:
                text = sentences[0]  # fallback: keep first sentence

        return text.strip(" .,–-").strip()


@dataclass(frozen=True)
class IdeaQualityScore:
    idea: str
    score: float
    reward_hits: tuple[str, ...]
    penalty_hits: tuple[str, ...]


class IdeaQualityGate:
    """
    Heuristic scorer for everydayengel idea candidates.
    Rewards body/moment/discovery signals; penalises shopping/abstract/generic-lifestyle copy.
    Operates purely on text — no LLM call, no I/O.
    """

    MINIMUM_WINNER_SCORE: float = -1.0
    MIRROR_FIDELITY_THRESHOLD: float = 1.0
    MIRROR_MAX_RETRIES: int = 2

    # (regex pattern, label) — each hit = +1
    # Existing patterns encode body/moment/discovery signals.
    # New patterns (marked NEW) encode Master-level micro-specificity:
    # concrete daily-practical friction, body signals, before/after contrast,
    # micro-scene anchoring, realization hooks, and practical-shift language.
    _REWARD_PATTERNS: tuple[tuple[str, str], ...] = (
        # Body sensation — includes "fühlt sich / fühlen sich / anfühlt" (NEW)
        (r"spür|spüren|gespürt|fühlt\s+sich|fühlen\s+sich|anfühlt", "body_sensation"),
        (r"weh|schmerz|drückt|zieht|kribbelt", "physical_friction"),
        (r"plötzlich|unerwartet|überraschend", "surprise_moment"),
        (r"gestern|heute morgen|letzte[ns]?\b|diesen morgen|diese woche", "filmable_moment"),
        (r"zum ersten mal|erstmals|das erste mal", "milestone_moment"),
        (r"niemand hat mir gesagt|hätte ich gewusst|wusste ich nicht", "discovery_hook"),
        (r"konkret\b|direkt\b|tatsächlich\b", "concrete_marker"),
        (r"verändert\b|anders als|nicht mehr\b|seitdem\b", "shift_marker"),
        # NEW: explicit body-as-subject language ("Mein Körper sagt früher genug")
        (r"mein\s+körper\b|der\s+körper\b|körper\s+sagt\b|körper\s+gibt\b|körper\s+meldet\b|körper\s+braucht\b", "body_signal"),
        # NEW: "früher" as before/after contrast anchor ("früher praktisch, jetzt nicht mehr")
        (r"\bfrüher\b", "before_after"),
        # NEW: micro-scene anchor ("beim Kochen", "während ich", "als ich aufstand")
        (r"beim\s+\w+en\b|während\s+ich\b|als\s+ich\b|in\s+d(?:em|iesem)\s+moment\b", "micro_scene"),
        # NEW: genuine realization / discovery hook ("mir ist aufgefallen", "auf einmal")
        (r"mir\s+ist\s+aufgefallen|hab(?:e)?\s+gemerkt|auf\s+einmal\b|hätte\s+nicht\s+gedacht|ich\s+merke\s+jetzt\b", "realization_hook"),
        # NEW: practical shift — changed capability or new daily necessity
        # ("geht nicht mehr", "Einkaufen braucht jetzt Pausen", "jetzt noch sitzen")
        (r"geht\s+nicht\s+mehr\b|kann\s+nicht\s+mehr\b|klappt\s+nicht\s+mehr\b"
         r"|brauch\w*\s+(?:jetzt|pausen?|länger)\b|jetzt\s+noch\b", "practical_shift"),
        # NEW: contrast observation ("Der Unterschied zwischen Hunger und …")
        (r"\bunterschied\s+zwischen\b|\bunterschied\s+(?:zu|ob)\b", "contrast_observation"),
    )

    # (regex pattern, label) — each hit = -1
    # Existing patterns penalise shopping/wardrobe/room/listicle/abstract/generic.
    # NEW patterns penalise polished summary framing, multi-core blur, and broad
    # pregnancy-lifestyle generalisation — the characteristic bot anti-patterns.
    _PENALTY_PATTERNS: tuple[tuple[str, str], ...] = (
        # shopping: \b word boundaries prevent false positives on "einkaufen" (verb)
        # so "Einkaufen braucht jetzt Pausen" (Master-like) is NOT penalised,
        # but "Tipps für den Einkauf" or "kaufen" standalone IS penalised.
        (r"\bsupermarkt\b|\beinkauf\b|\bkaufen\b|\bbestellen\b|\bprodukt\b", "shopping"),
        (r"outfit\b|kleidung|garderob|mode\b|kleidungsstück|kleiderschrank|jeans|umstandsjeans", "wardrobe"),
        # room_setup: extended with "kinderzimmer" and "zimmer organisieren" (NEW)
        (r"babyzimmer\s+einrichten|kinderzimmer\b|zimmer\s+(?:organisieren|gestalten|einrichten)"
         r"|deko\b|einrichtung\b|ordnungshelfer|kleine\s+räume|room-setup", "room_setup"),
        (r"tipps\s+für|liste\s+von|methoden\b|strategien\b|ratgeber\b|tipps\s+und\s+tricks", "listicle"),
        (r"konzept\b|aspekt\b|perspektiv|verschiedene\b|überblick\b|zusammenfassung\b", "abstract"),
        (r"allgemein\b|generell\b|grundsätzlich\b|im\s+allgemeinen", "generic_lifestyle"),
        # NEW: meta-summary framing ("in diesem Video …", "darum geht es")
        (r"in\s+diesem\s+video\b|in\s+diesem\s+post\b|dieser\s+content\b|darum\s+geht\s+es\b|das\s+thema\s+ist\b", "meta_summary"),
        # NEW: multi-core blur ("nicht nur … sondern auch", "einerseits … andererseits")
        (r"einerseits\b|andererseits\b|nicht\s+nur\b", "multicore_blur"),
        # NEW: broad pregnancy generalisation — extended with Schwangerschaftsalltag wrapper
        (r"viele\s+schwangere\b|schwangere\s+frauen\b|als\s+(?:großes\s+)?thema\b|schwangerschaftsthema\b|typisch\s+für\b|schwangerschaftsalltag\b", "broad_pregnancy"),
        # NEW: umbrella hero / helper concepts — use \w* to match inflected forms
        # ("versteckten Helden", "stille Helfer", "kleine Helfer")
        (r"versteckt\w*\s+held|stille\w*\s+held|kleine\w*\s+held|stille\s+helfer|kleine\s+helfer", "pivot_hero_concept"),
        # NEW: object-collection framing — two separate entries so both can fire independently
        # "Dinge die/dass/welche X werden" — classic collection-list opener
        (r"\bdinge\s+(?:die|dass|welche)\b", "collection_framing"),
        # "unverzichtbar" — marks "things you suddenly can't live without" style packaging
        (r"\bunverzichtbar\b", "object_collection"),
    )

    # Stop words excluded from anchor extraction — prepositions, pronouns, conjunctions,
    # plus platform/command tokens that carry no scene information.
    _ANCHOR_STOP_WORDS: frozenset[str] = frozenset({
        "und", "oder", "aber", "für", "mit", "den", "dem", "das", "die", "der",
        "ein", "eine", "einer", "ich", "mir", "mich", "weil", "dass", "wenn",
        "ist", "bin", "hat", "wird", "seit", "beim", "bitte", "gib", "neue",
        "idee", "idea", "tiktok", "instagram", "reel", "video", "inhalt",
        "mein", "meine", "meinen", "meiner", "muss", "kann", "will", "auch",
        "noch", "dann", "jetzt", "immer", "sehr", "mehr", "schon", "mal",
    })

    def score(self, idea: str) -> IdeaQualityScore:
        low = idea.lower()
        reward_hits = tuple(
            label for pattern, label in self._REWARD_PATTERNS if re.search(pattern, low)
        )
        penalty_hits = tuple(
            label for pattern, label in self._PENALTY_PATTERNS if re.search(pattern, low)
        )
        return IdeaQualityScore(
            idea=idea,
            score=float(len(reward_hits) - len(penalty_hits)),
            reward_hits=reward_hits,
            penalty_hits=penalty_hits,
        )

    def extract_prompt_anchors(self, user_prompt: str) -> tuple[str, ...]:
        """
        Extract meaningful scene/action tokens from the user prompt.
        Returns empty tuple for short/broad prompts — no anchor bias applied.
        """
        words = re.findall(r'\w+', user_prompt.lower())
        tokens = tuple(
            w for w in words
            if len(w) > 3 and w not in self._ANCHOR_STOP_WORDS
        )
        return tokens

    @staticmethod
    def is_concrete_prompt(anchor_tokens: tuple[str, ...]) -> bool:
        """
        True when the user provided a concrete scene prompt (≥ 3 scene tokens).
        Used to decide GENERATE MODE vs SHARPEN MODE.
        """
        return len(anchor_tokens) >= 3

    @staticmethod
    def mirror_fidelity_score(
        candidate: str,
        anchor_tokens: tuple[str, ...],
    ) -> float:
        """
        Fraction of explicit MIRROR detail anchors recognizably preserved in candidate.
        Only checks tokens with a small allowlist in _MIRROR_NEAR_FORMS. These are
        the critical scene/action/body details known to drift in MIRROR mode.
        Returns 1.0 when no allowlisted anchors are found (gate not applicable).
        Range: 0.0–1.0.
        """
        fidelity_terms = [t for t in anchor_tokens if t in _MIRROR_NEAR_FORMS]
        if not fidelity_terms:
            return 1.0
        low = candidate.lower()
        hits = sum(
            1
            for token in fidelity_terms
            if any(f in low for f in (_MIRROR_NEAR_FORMS.get(token, frozenset()) | {token}))
        )
        return hits / len(fidelity_terms)

    @staticmethod
    def is_mirror_prompt_echo(candidate: str, user_prompt: str) -> bool:
        """
        True when the candidate merely wraps the raw MIRROR prompt in first person.
        This blocks prompt echoes like "Ich muss beim Kochen plötzlich sitzen wegen
        Schwindel" while allowing natural sentences that keep the same anchors.
        """
        prompt = re.sub(r"\s+", " ", re.sub(r"[^\wäöüÄÖÜß]+", " ", user_prompt.lower())).strip()
        if not prompt or len(prompt.split()) < 3:
            return False
        candidate_norm = re.sub(
            r"\s+", " ", re.sub(r"[^\wäöüÄÖÜß]+", " ", candidate.lower())
        ).strip()
        return prompt in candidate_norm

    @staticmethod
    def classify_idea_mode(user_prompt: str) -> str:
        """
        Classify a /idea user prompt as 'mirror' or 'ideation'.

        MIRROR when either:
          1. first-person pronoun present (ich / mir / mein*)
          2. scene connector (beim, im, vor, …) AND concrete verb/action present
             — detects lived moments even without first-person phrasing.
        IDEATION: empty, pure keyword pile, or no concrete-situation signal.

        Intentionally small and deterministic — two explicit rule tiers.
        """
        if not user_prompt.strip():
            return "ideation"
        tokens = set(re.findall(r'\b\w+\b', user_prompt.lower()))
        if tokens & {"ich", "mir"} or any(t.startswith("mein") for t in tokens):
            return "mirror"
        if tokens & _IDEA_SCENE_CONNECTORS and tokens & _IDEA_CONCRETE_VERBS:
            return "mirror"
        return "ideation"

    def anchor_score(
        self,
        candidate: str,
        anchors: tuple[str, ...],
        *,
        sharpen_mode: bool = False,
    ) -> float:
        """
        Bonus awarded when the candidate preserves tokens from the user's concrete prompt.
        In SHARPEN MODE the weight is tripled (max +6.0) to decisively enforce scene preservation.
        Zero when anchors is empty (broad/empty prompt).
        """
        if not anchors:
            return 0.0
        low = candidate.lower()
        hits = sum(1 for a in anchors if a in low)
        raw = (hits / len(anchors)) * 3.0
        if sharpen_mode:
            return min(6.0, raw * 3.0)
        return min(2.0, raw)

    def pick_best(
        self,
        candidates: list[str],
        *,
        anchor_tokens: tuple[str, ...] = (),
        sharpen_mode: bool = False,
    ) -> tuple[str, float]:
        """
        Return (best_idea_text, combined_score).
        Combined score = heuristic score + anchor bonus (0 when no anchors).
        In SHARPEN MODE anchor weight is tripled (max +6.0) to decisively beat
        heuristically-strong but scene-drifted alternatives.
        Raises ValueError if candidates is empty.
        """
        if not candidates:
            raise ValueError("pick_best: candidates list is empty")
        scored = [self.score(c) for c in candidates]
        def _total(qs: IdeaQualityScore) -> float:
            return qs.score + self.anchor_score(qs.idea, anchor_tokens, sharpen_mode=sharpen_mode)
        best = max(scored, key=_total)
        return best.idea, _total(best)
