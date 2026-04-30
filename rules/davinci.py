"""
Formatting rule for DaVinci Resolve.
Informal, lowercase, no punctuation — suitable for timeline markers,
notes, and quick edit annotations.
"""


def format_text(raw_text: str) -> str:
    """
    Strip punctuation and lowercase everything for quick edit notes.
    """
    text = raw_text.strip().lower()

    punctuation = ".,!?;:\"'()-"
    for ch in punctuation:
        text = text.replace(ch, "")

    text = " ".join(text.split())

    return text


RULE_NAME = "DaVinci Resolve — Informal / No Punctuation"
DESCRIPTION = "All lowercase, punctuation removed, clean edit-friendly text."
