"""Natural-language row limit parser utilities.

Reusable parser for extracting requested row limits from Chinese/English prompts,
for example: "前50条", "top 100", "all", "全部".
"""
from __future__ import annotations

import re


def parse_requested_top(
    message: str,
    *,
    default_top: int,
    max_top: int,
) -> int:
    """Parse requested row limit from natural language message.

    Rules:
    - Empty/unmatched text -> default_top
    - "all" / "全部" -> max_top
    - "前N条" / "N条" / "top N" -> clamp N into [1, max_top]
    """
    if default_top < 1:
        default_top = 1
    if max_top < default_top:
        max_top = default_top

    text = (message or "").strip().lower()
    if not text:
        return default_top

    if "全部" in text or re.search(r"\ball\b", text):
        return max_top

    patterns = (
        r"(?:前|top\s*)(\d{1,5})\s*(?:条|个|rows?)?",
        r"(\d{1,5})\s*(?:条|个|rows?)",
    )
    for pat in patterns:
        match = re.search(pat, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            n = int(match.group(1))
        except ValueError:
            continue
        if n <= 0:
            return default_top
        return max(1, min(n, max_top))

    return default_top
