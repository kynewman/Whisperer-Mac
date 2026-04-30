"""Font helpers tuned for smooth Windows UI text."""

from PyQt6.QtGui import QFont, QFontDatabase


SAN_FRANCISCO_FAMILIES = (
    "SF Pro Text",
    "SF Pro",
    "SF Pro Display",
    "San Francisco Pro",
    ".AppleSystemUIFont",
    "Segoe UI Variable Text",
    "Segoe UI",
)


def san_francisco_family() -> str:
    installed = set(QFontDatabase.families())
    for family in SAN_FRANCISCO_FAMILIES:
        if family in installed:
            return family
    return "Segoe UI"


def san_francisco(size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(san_francisco_family(), size, weight)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font
