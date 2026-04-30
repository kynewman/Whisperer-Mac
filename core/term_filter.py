"""Utilities for keeping OCR and learned vocabulary focused on useful terms."""

from __future__ import annotations

import re


COMMON_WORDS = {
    "a", "about", "above", "actually", "after", "again", "against", "all", "almost", "also",
    "always", "am", "an", "and", "any", "are", "around", "as", "at", "back", "be", "because",
    "been", "before", "being", "below", "between", "both", "button", "buttons", "but", "by",
    "can", "cannot", "cant", "can't", "center", "click", "clicking", "could", "did", "directly",
    "do", "does", "doesnt", "doesn't", "doing", "done", "dont", "don't", "down", "drag",
    "dragging", "drop", "dropdown", "each", "even", "every", "feature", "features", "few", "for", "from", "get", "gets",
    "getting", "go", "goes", "going", "good", "got", "had", "has", "have", "having", "he",
    "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its",
    "immediately", "just", "kind", "large", "last", "like", "little", "make", "makes", "many",
    "maybe", "me", "menu", "more", "most", "much", "my", "near", "need", "needs", "new", "next",
    "no", "not", "now", "of", "off", "okay", "on", "once", "one", "only", "open", "opened",
    "opens", "option", "or", "other", "our", "out", "over", "own", "please", "previous",
    "probably", "put", "really", "right", "same", "screen", "see", "setting", "settings", "she",
    "should", "shouldnt", "shouldn't", "small", "so", "some", "something", "sometimes", "still", "such",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "thing",
    "things", "this", "those", "through", "to", "too", "under", "up", "us", "use", "used",
    "very", "want", "was", "wasnt", "wasn't", "way", "we", "well", "were", "what", "when",
    "whenever", "where", "which", "while", "who", "why", "will", "window", "with", "work",
    "working", "works", "would", "you", "your", "yours",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&@.'+\-_/]{1,}")
PHRASE_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&@.'+\-_/]{2,}"
    r"(?:\s+(?:[A-Z][A-Za-z0-9&@.'+\-_/]{2,}|[&+]\s*[A-Z][A-Za-z0-9.'\-_/]{2,})){1,4}\b"
)


def normalize_term(term: str) -> str:
    """Return a compact term suitable for context prompts and dictionary rows."""
    cleaned = re.sub(r"^[^\w@+]+|[^\w+]+$", "", term.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip("'\"")


def _looks_like_name_or_product(term: str) -> bool:
    if any(ch.isdigit() for ch in term) and any(ch.isalpha() for ch in term):
        return True
    if any(ch in term for ch in ("+", "&", "@", "/", "_")) and any(ch.isalpha() for ch in term):
        return True
    if term.isupper() and 2 <= len(term) <= 12:
        return True
    if re.search(r"[a-z][A-Z]|[A-Z].*[A-Z]", term):
        return True
    return term[:1].isupper() and len(term) >= 4


def is_useful_term(term: str, *, source: str = "") -> bool:
    """Heuristic for filtering out OCR/transcript filler while keeping names."""
    cleaned = normalize_term(term)
    if not cleaned or len(cleaned) > 48:
        return False
    lower = cleaned.lower()
    if lower in COMMON_WORDS:
        return False
    if lower.isdigit():
        return False
    if len(lower) <= 2 and not cleaned.isupper():
        return False

    if " " in cleaned:
        parts = [normalize_term(part) for part in cleaned.split()]
        useful_parts = [part for part in parts if part and part.lower() not in COMMON_WORDS]
        return len(useful_parts) >= 1 and any(_looks_like_name_or_product(part) for part in useful_parts)

    if _looks_like_name_or_product(cleaned):
        return True

    # Lowercase OCR still often contains useful unique app/artist/project terms.
    min_len = 5 if source == "transcription" else 6
    return len(lower) >= min_len and lower not in COMMON_WORDS


def extract_useful_terms(text: str, *, limit: int = 90, source: str = "ocr", include_phrases: bool = True) -> list[str]:
    """Extract stable, de-duplicated terms from OCR or transcript text."""
    seen: set[str] = set()
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = normalize_term(term)
        key = cleaned.lower()
        if not cleaned or key in seen or not is_useful_term(cleaned, source=source):
            return
        seen.add(key)
        terms.append(cleaned)

    if include_phrases:
        for match in PHRASE_RE.finditer(text):
            add(match.group(0))
            if len(terms) >= limit:
                return terms

    for match in TOKEN_RE.finditer(text):
        add(match.group(0))
        if len(terms) >= limit:
            break
    return terms
