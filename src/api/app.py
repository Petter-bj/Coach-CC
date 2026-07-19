"""FastAPI-laget for det private Trening-dashboardet."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.api.coach import build_coach_context
from src.api.reviews import (
    confirm_review,
    pending_review_context,
    save_reconsidered_review,
)
from src.api.today import build_today_payload
from src.coaching.deepseek import (
    CoachProviderError,
    CoachReply,
    CoachUnavailableError,
    ask_deepseek_coach,
)
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


class CoachMessage(BaseModel):
    """Én fritekstmelding til coachen; ingen planendring kan skje her."""

    message: str = Field(min_length=1, max_length=2_000)


CoachResponder = Callable[[str, dict[str, Any]], CoachReply]


def create_app(
    *,
    db_path: Path | str | None = None,
    api_token: str | None = None,
    coach_responder: CoachResponder | None = None,
) -> FastAPI:
    """Lag dashboardet og dets private, read-only API."""
    token = api_token if api_token is not None else os.getenv("TRENING_API_TOKEN")
    auth = _require_dashboard_access(token)
    responder = coach_responder or ask_deepseek_coach

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

    @app.post("/api/coach/chat", dependencies=[Depends(auth)])
    def coach_chat(message: CoachMessage) -> dict[str, str | bool]:
        question = message.message.strip()
        if not question:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="Message cannot be blank")
        with connect(db_path) as conn:
            context = build_coach_context(conn)
        try:
            reply = responder(question, context)
        except CoachUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Coachen er ikke konfigurert ennå",
            ) from exc
        except CoachProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Coachen er midlertidig utilgjengelig. Prøv igjen.",
            ) from exc
        return {
            "answer": reply.answer,
            "model": reply.model,
            # Gjør kontrakten eksplisitt: denne første sløyfen kan bare lese.
            "changes_applied": False,
        }

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

    @app.post("/api/reviews/{review_id}/reconsider", dependencies=[Depends(auth)])
    def reconsider_session_review(
        review_id: int,
        confirmation: ReviewConfirmation,
    ) -> dict[str, Any]:
        """La coachen vurdere samme økt på nytt etter et brukeravvik.

        Et avvik er ikke en bekreftelse. Den oppdaterte vurderingen beholdes
        derfor i det gule kortet til brukeren uttrykkelig markerer den som
        vurdert i et separat steg.
        """
        note = confirmation.note.strip() if confirmation.note else ""
        if not note:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Et avvik eller notat må skrives før vurderingen kan oppdateres",
            )

        with connect(db_path) as conn:
            review_context = pending_review_context(conn, review_id=review_id)
            if review_context is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Review is missing or has already been confirmed",
                )
            context = build_coach_context(conn)

        context["review_in_progress"] = {
            **review_context,
            "user_reported_deviation": note,
        }
        question = (
            "Brukeren har rapportert et avvik for den registrerte økten. "
            "Vurder bare denne økten på nytt opp mot planen og de faktiske "
            "tallene. Brukerens opplysning er fasit når Garmin kan ha "
            "feilestimert innendørs. Svar direkte til brukeren i 2–5 "
            f"setninger. Avviket er: {note}"
        )
        try:
            reply = responder(question, context)
        except CoachUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Coachen er ikke konfigurert ennå",
            ) from exc
        except CoachProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Coachen er midlertidig utilgjengelig. Prøv igjen.",
            ) from exc

        with connect(db_path) as conn:
            review = save_reconsidered_review(
                conn,
                review_id=review_id,
                note=note,
                coach_comment=reply.answer,
            )
        if review is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Review was confirmed while the coach was considering it",
            )
        return {"review": review, "model": reply.model, "changes_applied": False}

    dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard_preview"
    app.mount(
        "/",
        StaticFiles(directory=dashboard_dir, html=True),
        name="dashboard",
    )

    return app


app = create_app()
