"""Dictation history records, audio retention, and reprocessing."""

from __future__ import annotations

import os
import threading
import time
from typing import List

from core.migrations import get_connection


_lock = threading.Lock()


def save_dictation(
    started_at: str,
    duration_ms: int,
    app_name: str,
    window_title: str,
    mode_id: int | None,
    stt_provider: str,
    stt_model: str,
    raw_transcript: str,
    final_text: str,
    replacements_applied: int = 0,
    llm_processed: int = 0,
    paste_method: str = "clipboard_paste",
    paste_succeeded: int | None = None,
    error: str | None = None,
    audio_path: str | None = None,
) -> int:
    with _lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dictations
                (started_at, duration_ms, app_name, window_title, mode_id,
                 stt_provider, stt_model, raw_transcript, final_text,
                 replacements_applied, llm_processed, paste_method,
                 paste_succeeded, error, audio_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            started_at, duration_ms, app_name, window_title, mode_id,
            stt_provider, stt_model, raw_transcript, final_text,
            replacements_applied, llm_processed, paste_method,
            paste_succeeded, error, audio_path,
        ))
        dictation_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return dictation_id


def save_context(dictation_id: int, source: str, content: str):
    with _lock:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO dictation_contexts (dictation_id, source, content)
            VALUES (?, ?, ?)
        """, (dictation_id, source, content))
        conn.commit()
        conn.close()


def list_dictations(search: str = "", limit: int = 100, offset: int = 0) -> List[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    params: list = []
    query = """
        SELECT d.*, m.name as mode_name
        FROM dictations d
        LEFT JOIN modes m ON d.mode_id = m.id
    """
    if search:
        query += """
            WHERE d.raw_transcript LIKE ? OR d.final_text LIKE ?
                OR d.app_name LIKE ? OR d.error LIKE ?
        """
        like = f"%{search}%"
        params.extend([like, like, like, like])
    query += " ORDER BY d.started_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_dictation(dictation_id: int) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.*, m.name as mode_name
        FROM dictations d
        LEFT JOIN modes m ON d.mode_id = m.id
        WHERE d.id = ?
    """, (dictation_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return None
    result = dict(row)
    cursor.execute("SELECT * FROM dictation_contexts WHERE dictation_id = ?", (dictation_id,))
    result["contexts"] = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return result


def delete_dictation(dictation_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT audio_path FROM dictations WHERE id = ?", (dictation_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return False
    audio_path = row["audio_path"]
    if audio_path and os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except OSError:
            pass
    cursor.execute("DELETE FROM dictations WHERE id = ?", (dictation_id,))
    conn.commit()
    conn.close()
    return True


def reprocess(dictation_id: int, mode_id: int | None = None) -> int | None:
    """Re-run pipeline on stored raw transcript using chosen mode. Returns new dictation id."""
    from core.modes import get_mode
    from core.formatter import format_transcription
    from core.dictionary import apply_replacements

    original = get_dictation(dictation_id)
    if original is None:
        return None

    raw = original.get("raw_transcript") or ""
    if not raw:
        return None

    mode = get_mode(mode_id) if mode_id else None
    if mode is None:
        mode = get_mode(original.get("mode_id")) if original.get("mode_id") else None
    app_name = original.get("app_name", "")
    window_title = original.get("window_title", "")

    formatted = format_transcription(raw, app_name, window_title)
    final_text = apply_replacements(formatted)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    new_id = save_dictation(
        started_at=now,
        duration_ms=original.get("duration_ms", 0),
        app_name=app_name,
        window_title=window_title,
        mode_id=mode.id if mode else original.get("mode_id"),
        stt_provider=original.get("stt_provider", ""),
        stt_model=original.get("stt_model", ""),
        raw_transcript=raw,
        final_text=final_text,
        replacements_applied=1,
        llm_processed=0,
        paste_method=original.get("paste_method", "clipboard_paste"),
        error=None,
        audio_path=None,
    )
    return new_id


def save_error_event(
    started_at: str,
    app_name: str,
    window_title: str,
    mode_id: int | None,
    stt_provider: str,
    stt_model: str,
    error: str,
    duration_ms: int = 0,
):
    save_dictation(
        started_at=started_at,
        duration_ms=duration_ms,
        app_name=app_name,
        window_title=window_title,
        mode_id=mode_id,
        stt_provider=stt_provider,
        stt_model=stt_model,
        raw_transcript="",
        final_text="",
        error=error,
    )
