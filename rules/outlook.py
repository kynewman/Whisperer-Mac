"""
Formatting rule for Microsoft Outlook (and general business/email contexts).
Formal tone, standard punctuation, proper capitalization.
"""


def format_text(raw_text: str) -> str:
    """
    Take raw Whisper output and return business-ready formatted text.
    """
    text = raw_text.strip()
    if not text:
        return text

    text = text[0].upper() + text[1:]

    if text and text[-1] not in ".!?":
        text += "."

    replacements = {
        " i ": " I ",
        " i'": " I'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


RULE_NAME = "Outlook — Formal / Business"
DESCRIPTION = "Standard punctuation, capitalized sentences, professional tone."
