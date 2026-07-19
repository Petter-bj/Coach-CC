"""FastAPI-laget for det private Trening-dashboardet."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.api.reviews import confirm_review
from src.api.today import build_today_payload
from src.coaching.reviews import ensure_pending_reviews
from src.db.connection import connect
from src.db.migrations import migrate


def _require_dashboard_access(expected_token: str | None):
    """Godta Tailscale Serve eller et lokalt Bearer-token.

    Uvicorn lytter kun på localhost. Dermed kan Tailscale Serve trygt legge
    ved identitetsheaderen uten at noen på nettverket kan forfalske den. Et
    Bearer-token beholdes for lokal, administrativ verifisering på VPS-en.
    """
    def verify(
        authorization: Annotated[str | None, Header()] = None,
        tailscale_login: Annotated[
            str | None, Header(alias="Tailscale-User-Login")
        ] = None,
    ) -> None:
        if tailscale_login:
            return
        if expected_token is None:
            return
        candidate = authorization.removeprefix("Bearer ") if authorization else ""
        if not secrets.compare_digest(candidate, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return verify


class ReviewConfirmation(BaseModel):
    """Valgfritt brukeravvik når en automatisk øktvurdering bekreftes."""

    note: str | None = Field(default=None, max_length=1000)


def create_app(
    *,
    db_path: Path | str | None = None,
    api_token: str | None = None,
) -> FastAPI:
    """Lag dashboardet og dets private, read-only API."""
    token = api_token if api_token is not None else os.getenv("TRENING_API_TOKEN")
    auth = _require_dashboard_access(token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Sørger for at API-et aldri starter mot en database som mangler en
        # ny, allerede versjonert migrering ved deploy. Samtidig backfiller vi
        # bare manglende, idempotente review-kort fra tidligere matcher.
        with connect(db_path) as conn:
            migrate(conn)
            ensure_pending_reviews(conn)
        yield

    app = FastAPI(
        title="Trening private API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/health", dependencies=[Depends(auth)])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/today", dependencies=[Depends(auth)])
    def today(day: date | None = None) -> dict:
        with connect(db_path) as conn:
            return build_today_payload(conn, day)

    @app.post("/api/reviews/{review_id}/confirm", dependencies=[Depends(auth)])
    def confirm_session_review(
        review_id: int,
        confirmation: ReviewConfirmation,
    ) -> dict[str, str | int | None]:
        with connect(db_path) as conn:
            review = confirm_review(
                conn,
                review_id=review_id,
                note=confirmation.note,
            )
        if review is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Review is missing or has already been confirmed",
            )
        return review

    dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard_preview"
    app.mount(
        "/",
        StaticFiles(directory=dashboard_dir, html=True),
        name="dashboard",
    )

    return app


app = create_app()
