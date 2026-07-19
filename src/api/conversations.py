"""Kort, privat samtalehistorikk for coach-flater som trenger kontinuitet."""

from __future__ import annotations

import sqlite3
from typing import Any


BLOCK_THREAD = "block:planning"
MAX_STORED_MESSAGES = 32
MODEL_CONTEXT_MESSAGES = 12


def conversation_history(
    conn: sqlite3.Connection,
    *,
    thread: str,
    limit: int = MODEL_CONTEXT_MESSAGES,
) -> list[dict[str, str]]:
    """Returner den nyeste delen av én tråd i kronologisk rekkefølge."""
    rows = conn.execute(
        """
        SELECT role, content, model
          FROM coach_conversation_messages
         WHERE thread = ?
         ORDER BY id DESC
         LIMIT ?
        """,
        (thread, limit),
    ).fetchall()
    return [
        {
            "role": row["role"],
            "content": row["content"],
            **({"model": row["model"]} if row["model"] else {}),
        }
        for row in reversed(rows)
    ]


def append_exchange(
    conn: sqlite3.Connection,
    *,
    thread: str,
    question: str,
    answer: str,
    model: str,
) -> list[dict[str, str]]:
    """Lagre én vellykket rundtur og behold bare den korte samtaletråden."""
    conn.executemany(
        """
        INSERT INTO coach_conversation_messages (thread, role, content, model)
        VALUES (?, ?, ?, ?)
        """,
        [
            (thread, "user", question, None),
            (thread, "assistant", answer, model),
        ],
    )
    cutoff = conn.execute(
        """
        SELECT id FROM coach_conversation_messages
         WHERE thread = ?
         ORDER BY id DESC
         LIMIT 1 OFFSET ?
        """,
        (thread, MAX_STORED_MESSAGES - 1),
    ).fetchone()
    if cutoff is not None:
        conn.execute(
            "DELETE FROM coach_conversation_messages WHERE thread = ? AND id < ?",
            (thread, cutoff["id"]),
        )
    return conversation_history(conn, thread=thread)


def client_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Fjern eventuell metadata før den sendes til modellens chat-historikk."""
    return [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in {"user", "assistant"} and item.get("content")
    ]
