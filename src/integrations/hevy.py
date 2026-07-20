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


def _routine_id_from_response(body: Any) -> str | None:
    """Hent en ID fra Hevys normale respons, uten å anta én eksakt innpakking."""
    if not isinstance(body, dict):
        return None
    routine = body.get("routine")
    candidate = routine if isinstance(routine, dict) else body
    routine_id = candidate.get("id")
    return routine_id if isinstance(routine_id, str) and routine_id else None


def _routine_matches_created_payload(candidate: dict[str, Any], payload: dict[str, Any]) -> bool:
    """Er dette sannsynligvis akkurat malen vi nettopp sendte til Hevy?"""
    routine = candidate.get("routine") if isinstance(candidate.get("routine"), dict) else candidate
    expected = payload["routine"]
    if routine.get("title") != expected["title"]:
        return False

    # Rutine-listen inneholder normalt øvelsene. Når den gjør det, bruker vi
    # dem som ekstra vern mot å knytte et gammelt likt navngitt utkast til et
    # nytt forslag.
    exercises = routine.get("exercises")
    if not isinstance(exercises, list):
        return True
    expected_ids = [item["exercise_template_id"] for item in expected["exercises"]]
    candidate_ids = [item.get("exercise_template_id") for item in exercises if isinstance(item, dict)]
    return candidate_ids == expected_ids


def _find_recently_created_routine_id(
    client: httpx.Client,
    payload: dict[str, Any],
) -> str | None:
    """Finn en mal igjen hvis Hevy opprettet den, men unnlot å gi oss ID-en.

    Dette brukes *kun* etter et vellykket POST-svar uten ID. Det er dermed en
    sikkerhetsventil mot en inkonsistent respons, ikke en forhåndssjekk som kan
    gjøre en vanlig, bevisst ny mal til en uventet gjenbruk av en gammel.
    """
    matches: list[dict[str, Any]] = []
    try:
        page = 1
        while True:
            response = client.get(
                f"{API_BASE}/routines",
                headers=_headers(),
                params={"page": page, "pageSize": PAGE_SIZE},
            )
            response.raise_for_status()
            body = response.json()
            batch = body.get("routines") or []
            if not isinstance(batch, list):
                return None
            matches.extend(
                item for item in batch
                if isinstance(item, dict) and _routine_matches_created_payload(item, payload)
            )
            if page >= (body.get("page_count") or 1) or not batch:
                break
            page += 1
    except (httpx.HTTPError, ValueError):
        return None

    # API-et oppgir nyeste rutiner først. Vi bruker bare første mulige treff
    # når det har en reell Hevy-ID; ellers lar vi forslaget stå urørt.
    for match in matches:
        routine_id = _routine_id_from_response(match)
        if routine_id:
            return routine_id
    return None


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
        try:
            body = response.json()
        except ValueError:
            body = {}
        routine_id = _routine_id_from_response(body)
        if routine_id:
            return routine_id

        routine_id = _find_recently_created_routine_id(client, payload)
        if routine_id:
            return routine_id
        raise HevyRoutineError(
            "Hevy opprettet muligens malen, men bekreftet ikke ID-en. Sjekk Hevy før du prøver igjen."
        )
    except httpx.HTTPError as exc:
        raise HevyRoutineError("Hevy kunne ikke opprette malen akkurat nå") from exc
    finally:
        if close_client:
            client.close()
