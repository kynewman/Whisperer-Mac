"""Mode profiles and auto-activation rules."""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import List

from core.migrations import get_connection


@dataclasses.dataclass
class Mode:
    id: int | None = None
    name: str = ""
    is_builtin: bool = False
    description: str = ""
    stt_provider: str | None = None
    stt_model: str | None = None
    language: str = "en"
    formatting_prompt: str = ""
    output_format: str = "plain"
    llm_enabled: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_prompt: str = ""
    paste_method: str = "clipboard_paste"
    auto_send: bool = False
    ctx_ocr: bool = True
    ctx_selected_text: bool = False
    ctx_clipboard: bool = False
    enabled: bool = True
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Mode":
        return cls(
            id=row["id"],
            name=row["name"],
            is_builtin=bool(row["is_builtin"]),
            description=row["description"] or "",
            stt_provider=row["stt_provider"],
            stt_model=row["stt_model"],
            language=row["language"] or "en",
            formatting_prompt=row["formatting_prompt"] or "",
            output_format=row["output_format"] or "plain",
            llm_enabled=bool(row["llm_enabled"]),
            llm_provider=row["llm_provider"],
            llm_model=row["llm_model"],
            llm_prompt=row["llm_prompt"] or "",
            paste_method=row["paste_method"] or "clipboard_paste",
            auto_send=bool(row["auto_send"]),
            ctx_ocr=bool(row["ctx_ocr"]),
            ctx_selected_text=bool(row["ctx_selected_text"]),
            ctx_clipboard=bool(row["ctx_clipboard"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )


BUILTIN_MODES: list[dict] = [
    {"name": "Voice", "description": "Raw transcription with minimal formatting.", "formatting_prompt": "", "output_format": "plain"},
    {"name": "Message", "description": "Clean up dictated message for chat apps.", "formatting_prompt": "Clean up the following dictated message for sending in a chat app. Keep it conversational and concise.", "output_format": "plain"},
    {"name": "Email", "description": "Polished professional email body.", "formatting_prompt": "Rewrite the following dictated text as a polished professional email body. Preserve the meaning exactly.", "output_format": "plain"},
    {"name": "Note", "description": "Clear, well-punctuated prose.", "formatting_prompt": "Clean up the following dictated text into clear, well-punctuated prose.", "output_format": "plain"},
    {"name": "Coding", "description": "Code comment or docstring formatting.", "formatting_prompt": "Reformat the following dictated text as a code comment or docstring.", "output_format": "plain"},
    {"name": "Meeting", "description": "Structured meeting notes with bullet points.", "formatting_prompt": "Rewrite the following dictated notes as structured meeting notes with bullet points.", "output_format": "markdown"},
    {"name": "DaVinci Marker", "description": "Informal, lowercase, no punctuation for edit markers.", "formatting_prompt": "Convert to all lowercase and remove punctuation for quick edit notes.", "output_format": "plain"},
    {"name": "Screenwriting", "description": "Basic screenwriting formatting heuristics.", "formatting_prompt": "Apply basic screenwriting formatting: scene headings in ALL CAPS, character names in ALL CAPS.", "output_format": "plain"},
    {"name": "Custom", "description": "User-defined mode.", "formatting_prompt": "", "output_format": "plain"},
]


def seed_builtins():
    """Insert built-in modes if they do not already exist."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM deleted_builtin_modes")
    deleted_builtins = {str(row["name"]).lower() for row in cursor.fetchall()}
    for data in BUILTIN_MODES:
        if data["name"].lower() in deleted_builtins:
            continue
        cursor.execute("SELECT id FROM modes WHERE name = ?", (data["name"],))
        if cursor.fetchone() is None:
            cursor.execute("""
                INSERT INTO modes
                    (name, is_builtin, description, formatting_prompt, output_format, enabled)
                VALUES (?, 1, ?, ?, ?, 1)
            """, (data["name"], data["description"], data["formatting_prompt"], data["output_format"]))
    conn.commit()
    conn.close()


def add_mode(
    name: str,
    description: str = "",
    formatting_prompt: str = "",
    output_format: str = "plain",
    llm_enabled: bool = False,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_prompt: str = "",
    paste_method: str = "clipboard_paste",
    auto_send: bool = False,
    ctx_ocr: bool = True,
    ctx_selected_text: bool = False,
    ctx_clipboard: bool = False,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO modes
            (name, description, formatting_prompt, output_format,
             llm_enabled, llm_provider, llm_model, llm_prompt,
             paste_method, auto_send, ctx_ocr, ctx_selected_text, ctx_clipboard)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name, description, formatting_prompt, output_format,
        int(llm_enabled), llm_provider, llm_model, llm_prompt,
        paste_method, int(auto_send), int(ctx_ocr), int(ctx_selected_text), int(ctx_clipboard),
    ))
    mode_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return mode_id


def list_modes(enabled_only: bool = False) -> List[Mode]:
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM modes"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY is_builtin DESC, name ASC"
    cursor.execute(query)
    rows = [Mode.from_row(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_mode(mode_id: int) -> Mode | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM modes WHERE id = ?", (mode_id,))
    row = cursor.fetchone()
    conn.close()
    return Mode.from_row(row) if row else None


def get_mode_by_name(name: str) -> Mode | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM modes WHERE name = ?", (name,))
    row = cursor.fetchone()
    conn.close()
    return Mode.from_row(row) if row else None


def update_mode(mode_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "stt_provider", "stt_model", "language",
        "formatting_prompt", "output_format", "llm_enabled", "llm_provider",
        "llm_model", "llm_prompt", "paste_method", "auto_send",
        "ctx_ocr", "ctx_selected_text", "ctx_clipboard", "enabled",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    # Convert booleans to int for SQLite
    for idx, key in enumerate(fields.keys()):
        if isinstance(values[idx], bool):
            values[idx] = int(values[idx])
    values.append(mode_id)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE modes SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def delete_mode(mode_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, is_builtin FROM modes WHERE id = ?", (mode_id,))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return False
    if row["is_builtin"]:
        cursor.execute("""
            INSERT INTO deleted_builtin_modes (name, deleted_at)
            VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET deleted_at = CURRENT_TIMESTAMP
        """, (row["name"],))
    cursor.execute("DELETE FROM auto_activation_rules WHERE mode_id = ?", (mode_id,))
    cursor.execute("DELETE FROM modes WHERE id = ?", (mode_id,))
    conn.commit()
    conn.close()
    return True


def add_auto_rule(mode_id: int, match_type: str, match_value: str, priority: int = 0, enabled: bool = True) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO auto_activation_rules (mode_id, match_type, match_value, priority, enabled)
        VALUES (?, ?, ?, ?, ?)
    """, (mode_id, match_type, match_value, priority, int(enabled)))
    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def list_auto_rules(mode_id: int | None = None) -> List[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    if mode_id is not None:
        cursor.execute("""
            SELECT * FROM auto_activation_rules WHERE mode_id = ? ORDER BY priority DESC, id ASC
        """, (mode_id,))
    else:
        cursor.execute("SELECT * FROM auto_activation_rules ORDER BY priority DESC, id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def delete_auto_rule(rule_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_activation_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()


def resolve_active_mode(active_app: str = "", window_title: str = "") -> Mode:
    """Return the mode that matches the current foreground app, or Voice as fallback."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.*, r.match_type, r.match_value
        FROM modes m
        JOIN auto_activation_rules r ON r.mode_id = m.id
        WHERE r.enabled = 1 AND m.enabled = 1
        ORDER BY r.priority DESC, r.id ASC
    """)
    for row in cursor.fetchall():
        match_type = row["match_type"]
        match_value = row["match_value"].lower()
        if match_type == "process" and match_value in active_app.lower():
            conn.close()
            return Mode.from_row(row)
        if match_type == "window_title" and match_value in window_title.lower():
            conn.close()
            return Mode.from_row(row)
        if match_type == "exe_path" and match_value in active_app.lower():
            conn.close()
            return Mode.from_row(row)
    conn.close()
    fallback = get_mode_by_name("Voice")
    return fallback if fallback else Mode(name="Voice")
