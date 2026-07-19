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


@dataclass(frozen=True)
class WeeklyCoachReply:
    """Svar for ukecoachen, med et valgfritt forslag som aldri skrives direkte.

    ``operations`` er bare kandidatdata. API-laget validerer hver operasjon
    mot øktene i den aktuelle uken, lagrer den som et forslag og krever en
    separat bekreftelse fra brukeren før ``planned_sessions`` endres.
    """

    answer: str
    model: str
    operations: list[dict[str, Any]]


@dataclass(frozen=True)
class BlockCoachReply:
    """Svar for blokkcoachen med en valgfri, uapplisert blokkproposisjon."""

    answer: str
    model: str
    proposal: dict[str, Any] | None


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


def _max_tokens() -> int:
    raw_max_tokens = os.getenv("DEEPSEEK_MAX_TOKENS")
    try:
        value = int(raw_max_tokens) if raw_max_tokens else DEFAULT_MAX_TOKENS
    except ValueError:
        value = DEFAULT_MAX_TOKENS
    return max(1_024, value)


def _request_completion(
    request: dict[str, Any],
    *,
    api_key: str,
    http_client: httpx.Client | None,
) -> dict[str, Any]:
    """Utfør ett begrenset modellkall og normaliser leverandørfeil."""
    try:
        if http_client is not None:
            response = http_client.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=request,
            )
        else:
            # Thinking-svar kan bruke vesentlig lenger enn en vanlig chat.
            # La UI-et vente på et komplett V4-Pro-svar i stedet for å avbryte
            # mens modellen fremdeles resonnerer.
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                response = client.post(
                    DEEPSEEK_API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=request,
                )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise CoachProviderError("DeepSeek kunne ikke nås akkurat nå") from exc
    except ValueError as exc:
        raise CoachProviderError("DeepSeek returnerte ugyldig data") from exc


def _json_object_from_content(content: str) -> dict[str, Any] | None:
    """Tolker et strengt JSON-svar, også hvis modellen likevel brukte fence."""
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1]
        if candidate.endswith("```"):
            candidate = candidate[:-3]
        candidate = candidate.strip()
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


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
        "max_tokens": _max_tokens(),
        "stream": False,
    }

    return CoachReply(
        answer=_message_content(_request_completion(request, api_key=key, http_client=http_client)),
        model=selected_model,
    )


WEEKLY_SYSTEM_PROMPT = """Du er den personlige treningscoachen i Trening, i en
ukesplan-samtale. Du får bare en kuratert ukeplan og oppsummert, relevant
treningskontekst. Svar på naturlig norsk, konkret og uten å finne på data.

Du kan foreslå en endring, men har ikke selv skrivetilgang. En bruker må se og
uttrykkelig bekrefte et diff-forslag før planen endres. Returner KUN ett gyldig
JSON-objekt, uten markdown eller tekst rundt, med denne formen:
{
  "answer": "Kort svar direkte til brukeren.",
  "operations": [
    {"action": "move", "session_id": 123, "to_date": "YYYY-MM-DD", "reason": "kort grunn"},
    {"action": "skip", "session_id": 123, "reason": "kort grunn"},
    {"action": "replace", "session_id": 123, "type": "easy_run", "description": "...", "target_metrics": {"duration_min": 40}, "reason": "kort grunn"},
    {"action": "add", "date": "YYYY-MM-DD", "type": "strength", "description": "...", "target_metrics": {"duration_min": 30}, "reason": "kort grunn"}
  ]
}

Bruk bare session_id-er som står i konteksten, og bare datoer innen den
aktuelle uken. Returner alltid "operations": [] når brukeren bare spør et
spørsmål, når informasjonen ikke er tilstrekkelig, eller når ingen konkret
planendring bør foreslås. Ikke påstå at noe er endret eller sendt til Garmin.
Ikke gi medisinsk diagnose; anbefal kvalifisert helsehjelp ved akutte, sterke
eller vedvarende symptomer."""


def ask_deepseek_week_coach(
    question: str,
    context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    http_client: httpx.Client | None = None,
) -> WeeklyCoachReply:
    """Be modellen om tekst og en *ufarlig kandidat* til ukesplan-diff.

    Hvis modellen ikke følger JSON-kontrakten beholder vi teksten som svar,
    men forkaster alle endringsforslag. Slik får et uperfekt modell-svar aldri
    sideeffekt på planen.
    """
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise CoachUnavailableError("DeepSeek API-nøkkel mangler på serveren")

    selected_model = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL
    conversation = _conversation_messages(context)
    model_context = {
        key: value for key, value in context.items() if key != "conversation_history"
    }
    request = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": WEEKLY_SYSTEM_PROMPT},
            *conversation,
            {
                "role": "user",
                "content": (
                    "KURATERT UKEKONTEKST (JSON):\n"
                    f"{json.dumps(model_context, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    f"BRUKERENS MELDING:\n{question.strip()}"
                ),
            },
        ],
        "thinking": {"type": "enabled"},
        "max_tokens": _max_tokens(),
        "stream": False,
    }
    content = _message_content(_request_completion(request, api_key=key, http_client=http_client))
    decoded = _json_object_from_content(content)
    if decoded is None:
        return WeeklyCoachReply(answer=content, model=selected_model, operations=[])

    answer = decoded.get("answer")
    operations = decoded.get("operations")
    return WeeklyCoachReply(
        answer=answer.strip() if isinstance(answer, str) and answer.strip() else "Jeg fikk ikke formulert et tydelig svar. Prøv igjen.",
        model=selected_model,
        operations=[item for item in operations if isinstance(item, dict)] if isinstance(operations, list) else [],
    )


BLOCK_SYSTEM_PROMPT = """Du er den personlige treningscoachen i Trening, i en
samtale om treningsblokker. En blokk er periodiseringen over ukene. Du får
bare en kuratert blokkkontekst og skal ikke finne på historikk, symptomer eller
målinger.

Diskuter mål, rammer og avveininger naturlig på norsk. Når brukeren tydelig ber
deg opprette eller endre en blokk og informasjonen er god nok, kan du foreslå
én komplett blokk. Du har aldri selv skrivetilgang: brukeren må se og godkjenne
en diff før noe lagres. Returner KUN ett gyldig JSON-objekt uten markdown:
{
  "answer": "Kort svar direkte til brukeren.",
  "proposal": null eller {
    "action": "create" eller "update",
    "name": "kort blokknavn",
    "phase": "base|build|peak|taper|recovery",
    "start_date": "YYYY-MM-DD (mandag)",
    "goal": "det viktigste målet med blokken",
    "notes": "valgfri overordnet forklaring",
    "weeks": [
      {"focus": "...", "progression_note": "...", "planned_volume_note": "...", "is_deload": false}
    ]
  }
}

En ny blokk skal normalt ha 3–8 uker, med en deload-uke når det er fornuftig.
Hvis brukeren bare vil diskutere, hvis dato/rammer mangler, eller hvis du er
usikker: returner "proposal": null og spør eller forklar heller. Ikke opprett
enkeltøkter her, ikke påstå at noe er lagret eller sendt til Garmin, og ikke gi
medisinske diagnoser."""


def ask_deepseek_block_coach(
    question: str,
    context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str | None = None,
    http_client: httpx.Client | None = None,
) -> BlockCoachReply:
    """La modellen diskutere strategien og eventuelt levere en blokk-kandidat."""
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise CoachUnavailableError("DeepSeek API-nøkkel mangler på serveren")

    selected_model = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL
    conversation = _conversation_messages(context)
    model_context = {
        key: value for key, value in context.items() if key != "conversation_history"
    }
    request = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": BLOCK_SYSTEM_PROMPT},
            *conversation,
            {
                "role": "user",
                "content": (
                    "KURATERT BLOKKONTEKST (JSON):\n"
                    f"{json.dumps(model_context, ensure_ascii=False, separators=(',', ':'))}\n\n"
                    f"BRUKERENS MELDING:\n{question.strip()}"
                ),
            },
        ],
        "thinking": {"type": "enabled"},
        "max_tokens": _max_tokens(),
        "stream": False,
    }
    content = _message_content(_request_completion(request, api_key=key, http_client=http_client))
    decoded = _json_object_from_content(content)
    if decoded is None:
        return BlockCoachReply(answer=content, model=selected_model, proposal=None)
    answer = decoded.get("answer")
    proposal = decoded.get("proposal")
    return BlockCoachReply(
        answer=answer.strip() if isinstance(answer, str) and answer.strip() else "Jeg fikk ikke formulert et tydelig svar. Prøv igjen.",
        model=selected_model,
        proposal=proposal if isinstance(proposal, dict) else None,
    )
