"""FastAPI-laget for det private Trening-dashboardet."""

from __future__ import annotations

import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.staticfiles import StaticFiles

from src.api.today import build_today_payload
from src.db.connection import connect


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


def create_app(
    *,
    db_path: Path | str | None = None,
    api_token: str | None = None,
) -> FastAPI:
    """Lag dashboardet og dets private, read-only API."""
    token = api_token if api_token is not None else os.getenv("TRENING_API_TOKEN")
    auth = _require_dashboard_access(token)
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

    dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard_preview"
    app.mount(
        "/",
        StaticFiles(directory=dashboard_dir, html=True),
        name="dashboard",
    )

    return app


app = create_app()
