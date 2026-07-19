"""FastAPI-laget for det private Trening-dashboardet."""

from __future__ import annotations

import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from src.api.today import build_today_payload
from src.db.connection import connect


def _require_token(expected_token: str | None):
    """Returner en dependency som validerer Bearer-token når det er satt."""
    def verify(authorization: Annotated[str | None, Header()] = None) -> None:
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


def create_app(
    *,
    db_path: Path | str | None = None,
    api_token: str | None = None,
) -> FastAPI:
    """Lag en app. Sett `TRENING_API_TOKEN` i produksjon for Bearer-sikring."""
    token = api_token if api_token is not None else os.getenv("TRENING_API_TOKEN")
    auth = _require_token(token)
    app = FastAPI(
        title="Trening private API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/health", dependencies=[Depends(auth)])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/today", dependencies=[Depends(auth)])
    def today(day: date | None = None) -> dict:
        with connect(db_path) as conn:
            return build_today_payload(conn, day)

    return app


app = create_app()
