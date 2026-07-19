"""Smal DeepSeek-klient for den private coachen.

Modulen kjenner ikke databasen og får aldri fri tilgang til filer eller shell.
Den mottar kun en allerede kuratert kontekst fra API-laget og kan foreløpig
bare returnere tekst. Plan- og Garmin-endringer kommer gjennom egne, eksplisitte
forslags- og bekreftelsesflyter senere.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_TOKENS = 8_000


SYSTEM_PROMPT = """Du er den personlige treningscoachen i Trening.
Skriv på naturlig norsk, kort og konkret. Bygg svaret kun på konteksten som er
gitt under og brukerens melding; ikke dikt opp målinger, fullførte økter eller
synkroniseringer.

Skill tydelig mellom det som er automatisk registrert, din vurdering og det
brukeren selv oppgir. Den deterministiske anbefalingen i konteksten er
grunnlaget; du kan forklare eller utfordre den forsiktig, men ikke late som at
du har kjørt nye beregninger.

Du har ingen skrivetilgang i denne versjonen. Hvis brukeren ber om å endre en
plan, foreslå en konkret justering, men si tydelig at planen ikke er endret
ennå. Påstå aldri at noe er sendt til Garmin. Ikke gi medisinsk diagnose; ved
akutte, sterke eller vedvarende symptomer skal du anbefale kvalifisert
helsehjelp. Maksimalt rundt 180 ord med mindre brukeren eksplisitt ber om mer.
"""


class CoachUnavailableError(RuntimeError):
    """API-nøkkelen mangler eller modellen returnerte ikke brukbar tekst."""


class CoachProviderError(RuntimeError):
    """DeepSeek kunne ikke fullføre en forespørsel."""


@dataclass(frozen=True)
class CoachReply:
    """Tekstsvar fra en modell, uten sideeffekter."""

    answer: str
    model: str


def _message_content(payload: dict[str, Any]) -> str:
    try:
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise CoachProviderError("DeepSeek returnerte ikke et coach-svar") from exc
    if not isinstance(content, str) or not content.strip():
        finish_reason = choice.get("finish_reason")
        raise CoachProviderError(
            "DeepSeek returnerte et tomt coach-svar"
            f" (finish_reason={finish_reason or 'ukjent'})"
        )
    return content.strip()


def _conversation_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    """Normaliser den korte, klientholdte samtalehistorikken.

    DeepSeek er stateless. Historikken sendes derfor med hvert kall, men
    lagres ikke i databasen. API-laget validerer roller og lengder før den
    kommer hit; den defensive filtreringen her hindrer likevel at en vilkårlig
    kontekst endrer systeminstruksen.
    """
    messages: list[dict[str, str]] = []
    history = context.get("conversation_history", [])
    if not isinstance(history, list):
        return messages
    for turn in history[-8:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    return messages


def ask_deepseek_coach(
    question: str,
    context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    http_client: httpx.Client | None = None,
) -> CoachReply:
    """Be DeepSeek om et rent tekstsvar på en kuratert dagskontekst.

    ``http_client`` er et eksplisitt test-hook. Produksjon oppretter en kort
    klient per forespørsel, slik at ingen samtale- eller database-tilstand
    havner hos modellleverandøren mellom kall.
    """
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise CoachUnavailableError("DeepSeek API-nøkkel mangler på serveren")

    selected_model = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL
    raw_max_tokens = os.getenv("DEEPSEEK_MAX_TOKENS")
    try:
        max_tokens = int(raw_max_tokens) if raw_max_tokens else DEFAULT_MAX_TOKENS
    except ValueError:
        max_tokens = DEFAULT_MAX_TOKENS
    max_tokens = max(1_024, max_tokens)
    conversation = _conversation_messages(context)
    model_context = {
        key: value for key, value in context.items() if key != "conversation_history"
    }
    user_content = (
        "KURATERT DAGSKONTEKST (JSON):\n"
        f"{json.dumps(model_context, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"BRUKERENS MELDING:\n{question.strip()}"
    )
    request = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *conversation,
            {"role": "user", "content": user_content},
        ],
        # V4-Pro bruker thinking som standard. Budsjettet må romme både
        # resonnering og et synlig slutt-svar; 700 var for lavt til dette.
        "thinking": {"type": "enabled"},
        "max_tokens": max_tokens,
        "stream": False,
    }

    try:
        if http_client is not None:
            response = http_client.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {key}"},
                json=request,
            )
        else:
            # Thinking-svar kan bruke vesentlig lenger enn en vanlig chat.
            # La UI-et vente på et komplett V4-Pro-svar i stedet for å avbryte
            # mens modellen fremdeles resonnerer.
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                response = client.post(
                    DEEPSEEK_API_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json=request,
                )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise CoachProviderError("DeepSeek kunne ikke nås akkurat nå") from exc
    except ValueError as exc:
        raise CoachProviderError("DeepSeek returnerte ugyldig data") from exc

    return CoachReply(answer=_message_content(payload), model=selected_model)
