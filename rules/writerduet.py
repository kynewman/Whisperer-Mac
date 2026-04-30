"""
Formatting rule for WriterDuet (screenwriting software).

This is a PLACEHOLDER rule you can customise. Screenwriting has very specific
formatting conventions (scene headings, action lines, dialogue, parentheticals).
Modify the logic below to match your workflow.

Common screenwriting conventions this stub demonstrates:
  - Scene headings are ALL CAPS and start with INT. or EXT.
  - Character names above dialogue are ALL CAPS.
  - Dialogue is left as-is (natural casing).
  - Parentheticals are wrapped in parentheses and lowercase.
"""

import re


def format_text(raw_text: str) -> str:
    """
    Apply basic screenwriting formatting heuristics.
    Customise this function to match your personal WriterDuet workflow.
    """
    text = raw_text.strip()
    if not text:
        return text

    scene_heading_pattern = re.compile(
        r"^(int\.|ext\.|int/ext\.|i/e\.)\s*", re.IGNORECASE
    )
    if scene_heading_pattern.match(text):
        return text.upper()

    if text.startswith("(") and text.endswith(")"):
        return text.lower()

    if len(text.split()) <= 3 and text.isupper():
        return text.upper()

    if text and text[-1] not in ".!?":
        text += "."

    return text


RULE_NAME = "WriterDuet — Screenwriting (Placeholder)"
DESCRIPTION = (
    "Placeholder rule for screenwriting formatting. "
    "Edit rules/writerduet.py to customise scene headings, "
    "dialogue, action lines, and parentheticals."
)
