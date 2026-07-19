"""FastAPI-laget for det private Trening-dashboardet."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.api.coach import build_coach_context
from src.api.conversations import (
    BLOCK_THREAD,
    append_exchange,
    client_history,
    conversation_history,
)
from src.api.blocks import build_block_payload
from src.api.block_planning import (
    apply_block_proposal,
    build_block_coach_context,
    create_block_proposal,
    discard_block_proposal,
    validate_block_proposal,
)
from src.api.reviews import (
    confirm_review,
    pending_review_context,
    save_reconsidered_review,
)
from src.api.today import build_today_payload
from src.api.week import build_day_log, build_week_overview
from src.api.week_planning import (
    apply_proposal,
    build_week_coach_context,
    create_proposal,
    discard_proposal,
    validate_operations,
)
from src.api.workout import build_workout_detail
from src.coaching.deepseek import (
    CoachProviderError,
    CoachReply,
    CoachUnavailableError,
    BlockCoachReply,
    WeeklyCoachReply,
    ask_deepseek_block_coach,
    ask_deepseek_coach,
    ask_deepseek_week_coach,
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


class CoachHistoryMessage(BaseModel):
    """Én tidligere tur i en kort, klientholdt coach-samtale."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2_000)


class CoachMessage(BaseModel):
    """Én fritekstmelding til coachen; ingen planendring kan skje her."""

    message: str = Field(min_length=1, max_length=2_000)
    history: list[CoachHistoryMessage] = Field(default_factory=list, max_length=8)


CoachResponder = Callable[[str, dict[str, Any]], CoachReply]
WeeklyCoachResponder = Callable[[str, dict[str, Any]], WeeklyCoachReply]
BlockCoachResponder = Callable[[str, dict[str, Any]], BlockCoachReply]


def create_app(
    *,
    db_path: Path | str | None = None,
    api_token: str | None = None,
    coach_responder: CoachResponder | None = None,
    weekly_coach_responder: WeeklyCoachResponder | None = None,
    block_coach_responder: BlockCoachResponder | None = None,
) -> FastAPI:
    """Lag dashboardet og dets private API med eksplisitte bekreftelsesflyter."""
    token = api_token if api_token is not None else os.getenv("TRENING_API_TOKEN")
    auth = _require_dashboard_access(token)
    responder = coach_responder or ask_deepseek_coach
    week_responder = weekly_coach_responder or ask_deepseek_week_coach
    block_responder = block_coach_responder or ask_deepseek_block_coach

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

    @app.get("/api/week", dependencies=[Depends(auth)])
    def week(start: date | None = None) -> dict:
        with connect(db_path) as conn:
            return build_week_overview(conn, start)

    @app.get("/api/blocks", dependencies=[Depends(auth)])
    def blocks(day: date | None = None) -> dict:
        with connect(db_path) as conn:
            return build_block_payload(conn, day)

    @app.get("/api/blocks/coach/history", dependencies=[Depends(auth)])
    def block_coach_history() -> dict[str, list[dict[str, str]]]:
        with connect(db_path) as conn:
            return {"messages": conversation_history(conn, thread=BLOCK_THREAD)}

    @app.post("/api/blocks/coach", dependencies=[Depends(auth)])
    def block_coach_chat(message: CoachMessage) -> dict[str, Any]:
        """Diskuter én blokk og lagre bare en uapplisert strategidiff."""
        question = message.message.strip()
        if not question:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="Message cannot be blank")
        with connect(db_path) as conn:
            context = build_block_coach_context(conn)
            stored_history = conversation_history(conn, thread=BLOCK_THREAD)
        # Historikk fra VPS-en er fasit. Et nytt klientvindu kan dermed hente
        # samme samtale, mens client-history bare fungerer som en myk overgang
        # for en allerede åpen side under deploy.
        context["conversation_history"] = client_history(stored_history) or [
            turn.model_dump() for turn in message.history
        ]
        try:
            reply = block_responder(question, context)
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

        validated = validate_block_proposal(reply.proposal, context=context)
        proposal = None
        with connect(db_path) as conn:
            if validated is not None:
                candidate, target_block_id = validated
                proposal = create_block_proposal(
                    conn,
                    target_block_id=target_block_id,
                    question=question,
                    coach_answer=reply.answer,
                    proposal=candidate,
                )
            history = append_exchange(
                conn,
                thread=BLOCK_THREAD,
                question=question,
                answer=reply.answer,
                model=reply.model,
            )
        return {
            "answer": reply.answer,
            "model": reply.model,
            "changes_applied": False,
            "proposal": proposal,
            "messages": history,
        }

    @app.get("/api/days/{day}", dependencies=[Depends(auth)])
    def day_log(day: date) -> dict:
        with connect(db_path) as conn:
            return build_day_log(conn, day)

    @app.get("/api/workouts/{workout_id}", dependencies=[Depends(auth)])
    def workout_detail(workout_id: int) -> dict:
        with connect(db_path) as conn:
            detail = build_workout_detail(conn, workout_id)
        if detail is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Workout was not found")
        return detail

    @app.post("/api/coach/chat", dependencies=[Depends(auth)])
    def coach_chat(message: CoachMessage) -> dict[str, str | bool]:
        question = message.message.strip()
        if not question:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="Message cannot be blank")
        with connect(db_path) as conn:
            context = build_coach_context(conn)
        if message.history:
            context["conversation_history"] = [turn.model_dump() for turn in message.history]
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

    @app.post("/api/weeks/{week_start}/coach", dependencies=[Depends(auth)])
    def week_coach_chat(week_start: date, message: CoachMessage) -> dict[str, Any]:
        """Svar om én uke og eventuelt lagre en *uapplisert* endringsdiff."""
        question = message.message.strip()
        if not question:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="Message cannot be blank")
        with connect(db_path) as conn:
            context = build_week_coach_context(conn, week_start)
        if message.history:
            context["conversation_history"] = [turn.model_dump() for turn in message.history]
        try:
            reply = week_responder(question, context)
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

        operations = validate_operations(reply.operations, week_context=context)
        proposal = None
        if operations:
            with connect(db_path) as conn:
                proposal = create_proposal(
                    conn,
                    week_start=context["scope"]["week_start"],
                    question=question,
                    coach_answer=reply.answer,
                    operations=operations,
                )
        return {
            "answer": reply.answer,
            "model": reply.model,
            "changes_applied": False,
            "proposal": proposal,
        }

    @app.post("/api/week-proposals/{proposal_id}/apply", dependencies=[Depends(auth)])
    def apply_week_proposal(proposal_id: int) -> dict[str, Any]:
        with connect(db_path) as conn:
            proposal = apply_proposal(conn, proposal_id)
        if proposal is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Forslaget finnes ikke, er tomt eller er allerede håndtert",
            )
        return proposal

    @app.post("/api/week-proposals/{proposal_id}/discard", dependencies=[Depends(auth)])
    def discard_week_proposal(proposal_id: int) -> dict[str, Any]:
        with connect(db_path) as conn:
            discarded = discard_proposal(conn, proposal_id)
        if not discarded:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Forslaget finnes ikke eller er allerede håndtert",
            )
        return {"id": proposal_id, "status": "discarded"}

    @app.post("/api/block-proposals/{proposal_id}/apply", dependencies=[Depends(auth)])
    def apply_saved_block_proposal(proposal_id: int) -> dict[str, Any]:
        with connect(db_path) as conn:
            proposal = apply_block_proposal(conn, proposal_id)
        if proposal is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Blokkforslaget finnes ikke, overlapper en annen blokk eller er allerede håndtert",
            )
        return proposal

    @app.post("/api/block-proposals/{proposal_id}/discard", dependencies=[Depends(auth)])
    def discard_saved_block_proposal(proposal_id: int) -> dict[str, Any]:
        with connect(db_path) as conn:
            discarded = discard_block_proposal(conn, proposal_id)
        if not discarded:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Blokkforslaget finnes ikke eller er allerede håndtert",
            )
        return {"id": proposal_id, "status": "discarded"}

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
