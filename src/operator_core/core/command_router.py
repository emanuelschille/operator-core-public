from __future__ import annotations

from dataclasses import dataclass

from operator_core.core.project_resolver import ResolvedProjectContext


KNOWN_COMMANDS = {
    "neu",
    "idea",
    "serie",
    "title",
    "hook",
    "cta",
    "caption",
    "draft",
    "vollauto",
    "variant",
    "state",
    "rules",
    "assumptions",
    "decisions",
    "context",
    "offer_match",
    "product_fit",
    "cta_direction",
    "monetization_fit",
    "recommendation_ready",
    "performance_review",
    "learning_extract",
    "hypothesis",
    "next_step",
    "pattern_check",
    "page_brief",
    "funnel_direction",
    "routing_hint",
    "page_structure",
    "offer_path",
    "review",
    "status",
    "job",
    "help",
    "ping",
    "start",
    "menu",
    "menu_callback",
    "modus",
    "platform_mode_callback",
    "plan_demo",
    "plan_demo_callback",
    "text_action_callback",
    "content_ops_callback",
}

_BUTTON_TEXT_COMMANDS = {
    "☰ menü": "menu",
    "📋 tagesplan": "plan_demo",
    "💡 neue idee": "idea",
    "📝 entwurf aus idee": "vollauto",
    "📝 voll auto": "vollauto",
    "🎣 hook erstellen": "hook",
    "🪝 cta erstellen": "cta",
    "💬 caption erstellen": "caption",
    "📊 projekt-stand": "status",
    "🎯 modus": "modus",
}


@dataclass(frozen=True)
class RoutedCommand:
    project_key: str
    raw_text: str
    normalized_text: str
    is_command: bool
    is_known_command: bool
    command_name: str
    command_body: str


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _extract_command_name(first_token: str) -> tuple[bool, str]:
    if first_token.startswith("/"):
        token = first_token[1:].split("@", 1)[0].strip().lower()
        return True, token

    lowered = first_token.lower().strip()
    if lowered in KNOWN_COMMANDS:
        return True, lowered

    return False, lowered


def route_operator_request(
    text: str,
    project_context: ResolvedProjectContext,
) -> RoutedCommand:
    raw_text = text
    normalized_text = _normalize_text(text)
    lowered_normalized = normalized_text.casefold()

    if lowered_normalized in _BUTTON_TEXT_COMMANDS:
        command_name = _BUTTON_TEXT_COMMANDS[lowered_normalized]
        return RoutedCommand(
            project_key=project_context.project_key,
            raw_text=raw_text,
            normalized_text=normalized_text,
            is_command=True,
            is_known_command=True,
            command_name=command_name,
            command_body="",
        )

    if not normalized_text:
        return RoutedCommand(
            project_key=project_context.project_key,
            raw_text=raw_text,
            normalized_text="",
            is_command=False,
            is_known_command=False,
            command_name="empty",
            command_body="",
        )

    first_token, *rest_tokens = normalized_text.split(" ")
    is_command, command_name = _extract_command_name(first_token)
    command_body = " ".join(rest_tokens).strip()

    if is_command:
        return RoutedCommand(
            project_key=project_context.project_key,
            raw_text=raw_text,
            normalized_text=normalized_text,
            is_command=True,
            is_known_command=command_name in KNOWN_COMMANDS,
            command_name=command_name,
            command_body=command_body,
        )

    return RoutedCommand(
        project_key=project_context.project_key,
        raw_text=raw_text,
        normalized_text=normalized_text,
        is_command=False,
        is_known_command=False,
        command_name="message",
        command_body=normalized_text,
    )
