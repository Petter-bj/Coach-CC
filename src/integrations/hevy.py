"""Smal skriveklient for Hevy-rutiner (maler), uten modell- eller DB-tilgang."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx


API_BASE = "https://api.hevyapp.com/v1"
PAGE_SIZE = 10


class HevyRoutineError(RuntimeError):
    """Hevy er ikke konfigurert, svarer feil eller mangler en øvelse."""


def _key() -> str:
    value = os.getenv("HEVY_API_KEY")
    if not value:
        raise HevyRoutineError("Hevy er ikke konfigurert på dashboard-serveren")
    return value


def _headers() -> dict[str, str]:
    return {
        "api-key": _key(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _normalise_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _exercise_templates(*, http_client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Hent hele katalogen siden Hevy krever exercise_template_id ved opprettelse."""
    client = http_client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
    close_client = http_client is None
    templates: list[dict[str, Any]] = []
    try:
        page = 1
        while True:
            response = client.get(
                f"{API_BASE}/exercise_templates",
                headers=_headers(),
                params={"page": page, "pageSize": PAGE_SIZE},
            )
            response.raise_for_status()
            payload = response.json()
            batch = payload.get("exercise_templates") or []
            if not isinstance(batch, list):
                raise HevyRoutineError("Hevy svarte med en ukjent øvelsesliste")
            templates.extend(item for item in batch if isinstance(item, dict))
            if page >= (payload.get("page_count") or 1) or not batch:
                break
            page += 1
    except httpx.HTTPError as exc:
        raise HevyRoutineError("Kunne ikke hente øvelseslisten fra Hevy") from exc
    except ValueError as exc:
        raise HevyRoutineError("Hevy svarte med ugyldige data") from exc
    finally:
        if close_client:
            client.close()
    return templates


def _exercise_id(name: str, templates: list[dict[str, Any]]) -> str:
    target = _normalise_title(name)
    matches = [
        item for item in templates
        if isinstance(item.get("id"), str)
        and isinstance(item.get("title"), str)
        and _normalise_title(item["title"]) == target
    ]
    if len(matches) == 1:
        return matches[0]["id"]
    available = [item["title"] for item in templates if isinstance(item.get("title"), str)]
    similar = [title for title in available if target in _normalise_title(title)][:3]
    suggestion = f" Prøv: {', '.join(similar)}." if similar else ""
    raise HevyRoutineError(f"Fant ikke en entydig Hevy-øvelse for «{name}».{suggestion}")


def create_routine(
    routine: dict[str, Any],
    *,
    http_client: httpx.Client | None = None,
) -> str:
    """Opprett én konkret Hevy-rutine etter eksplisitt brukerbekreftelse."""
    templates = _exercise_templates(http_client=http_client)
    exercises = []
    for exercise in routine["exercises"]:
        payload = {
            "exercise_template_id": _exercise_id(exercise["exercise"], templates),
            "sets": exercise["sets"],
        }
        for key in ("rest_seconds", "notes"):
            if key in exercise:
                payload[key] = exercise[key]
        exercises.append(payload)
    payload = {
        "routine": {
            "title": routine["title"],
            "folder_id": None,
            "notes": routine.get("notes") or "",
            "exercises": exercises,
        }
    }
    client = http_client or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
    close_client = http_client is None
    try:
        response = client.post(f"{API_BASE}/routines", headers=_headers(), json=payload)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise HevyRoutineError("Hevy kunne ikke opprette malen akkurat nå") from exc
    except ValueError as exc:
        raise HevyRoutineError("Hevy svarte med ugyldige data etter opprettelsen") from exc
    finally:
        if close_client:
            client.close()
    created = body.get("routine") if isinstance(body, dict) else None
    routine_id = (created or body).get("id") if isinstance(created or body, dict) else None
    if not isinstance(routine_id, str) or not routine_id:
        raise HevyRoutineError("Hevy bekreftet ikke ID-en til den nye malen")
    return routine_id
