"""Skriveoperasjoner for den smale review-flyten."""

from __future__ import annotations

import sqlite3


def confirm_review(
    conn: sqlite3.Connection,
    *,
    review_id: int,
    note: str | None,
) -> dict[str, str | int | None] | None:
    """Marker én pending review som vurdert, eventuelt med brukerens notat.

    Returnerer ``None`` hvis reviewen ikke finnes eller allerede er vurdert.
    Det gjør handlingen trygg mot dobbeltklikk og utdaterte dashboard-faner.
    """
    cleaned_note = note.strip() if note else None
    if cleaned_note == "":
        cleaned_note = None
    cursor = conn.execute(
        """
        UPDATE session_reviews
           SET status = 'reviewed',
               user_note = ?,
               reviewed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
         WHERE id = ?
           AND status = 'pending'
        """,
        (cleaned_note, review_id),
    )
    if cursor.rowcount != 1:
        return None
    return {
        "id": review_id,
        "status": "reviewed",
        "user_note": cleaned_note,
    }
