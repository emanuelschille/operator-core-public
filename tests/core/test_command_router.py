from __future__ import annotations

from operator_core.core.command_router import route_operator_request
from operator_core.core.project_resolver import ResolvedProjectContext


def _project_context() -> ResolvedProjectContext:
    return ResolvedProjectContext(
        project_key="everydayengel",
        display_name="Everyday Engel",
        status="active",
        primary_interface="telegram",
        human_in_the_loop=True,
    )


def test_reply_keyboard_button_texts_route_to_existing_commands() -> None:
    cases = {
        "📋 Tagesplan": "plan_demo",
        "💡 Neue Idee": "idea",
        "📝 Voll Auto": "vollauto",
        "🎣 Hook erstellen": "hook",
        "💬 Caption erstellen": "caption",
        "📊 Projekt-Stand": "status",
        "🎯 Modus": "modus",
        "☰ Menü": "menu",
        }
    for text, expected_command in cases.items():
        routed = route_operator_request(text, _project_context())
        assert routed.is_command is True
        assert routed.is_known_command is True
        assert routed.command_name == expected_command
        assert routed.command_body == ""
