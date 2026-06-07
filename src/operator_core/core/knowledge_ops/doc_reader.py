from __future__ import annotations

import re


def extract_section(content: str, heading_fragment: str) -> str | None:
    """Return the body text of the first ## section whose heading contains heading_fragment."""
    lines = content.splitlines()
    in_section = False
    body_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            if heading_fragment.lower() in line.lower():
                in_section = True
            continue
        if in_section:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    return body if body else None


def first_sentences(text: str, n: int = 2) -> str:
    """Return up to n sentences from text, trimmed."""
    parts: list[str] = []
    for sentence in text.replace("\n", " ").split("."):
        stripped = sentence.strip()
        if stripped:
            parts.append(stripped)
        if len(parts) >= n:
            break
    return ". ".join(parts).strip(" .")


_NUMBERED_ITEM_RE = re.compile(r"^\d+\.\s")


def list_items(text: str, max_items: int = 5) -> list[str]:
    """Extract bullet / numbered list items from a section body."""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "### ")):
            item = stripped.lstrip("-*# ").strip()
            if item:
                items.append(item)
        elif _NUMBERED_ITEM_RE.match(stripped):
            item = _NUMBERED_ITEM_RE.sub("", stripped).strip()
            if item:
                items.append(item)
        if len(items) >= max_items:
            break
    return items


def trim(text: str, max_chars: int = 180) -> str:
    """Trim text to max_chars, appending … if cut."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"
