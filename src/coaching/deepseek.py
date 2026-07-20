"""Smal DeepSeek-klient for den private coachen.

Modulen kjenner ikke databasen og får aldri fri tilgang til filer eller shell.
Den mottar kun en allerede kuratert kontekst fra API-laget. Modellen kan returnere
tekst og en liten, uapplisert kandidat for skadestatus; alle endringer går gjennom
API-lagets eksplisitte forslag- og bekreftelsesflyt.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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

Du kan foreslå en endring av *brukeroppgitt* skadestatus, men har ingen direkte
skrivetilgang. Returner KUN ett gyldig JSON-objekt uten markdown:
{
  "answer": "Kort svar direkte til brukeren.",
  "injury_proposal": null eller {
    "action": "create" eller "update",
    "injury_id": 123,
    "body_part": "bare ved create",
    "severity": 1,
    "status": "active|healing|resolved",
    "started_at": "YYYY-MM-DD, bare ved create",
    "notes": "kort, nøytral oppsummering av det brukeren selv oppga"
  }
}

Foreslå bare skadeendring når brukeren tydelig beskriver en statusendring eller
en ny plage. For update må ``injury_id`` være en av de aktive skadene i
konteksten. «Kjennes litt bedre» er ikke nok til resolved; spør heller hva som
er symptomfritt og ved hvilken belastning. Du skal aldri diagnostisere, finne
på symptomer eller skrive at noe er lagret: brukeren må godkjenne forslaget
separat. Påstå aldri at noe er sendt til Garmin. Ved akutte, sterke eller
vedvarende symptomer skal du anbefale kvalifisert helsehjelp. Maksimalt rundt
180 ord med mindre brukeren eksplisitt ber om mer.
"""


class CoachUnavailableError(RuntimeError):
    """API-nøkkelen mangler eller modellen returnerte ikke brukbar tekst."""


class CoachProviderError(RuntimeError):
    """DeepSeek kunne ikke fullføre en forespørsel."""


@dataclass(frozen=True)
class CoachReply:
    """Tekstsvar med valgfri, fortsatt uapplisert skadestatus-kandidat."""

    answer: str
    model: str
    injury_proposal: dict[str, Any] | None = None


@dataclass(frozen=True)
class WeeklyCoachReply:
    """Svar for ukecoachen, med valgfrie forslag som aldri skrives direkte.

    ``operations`` og ``hevy_routines`` er bare kandidatdata. API-laget
    validerer dem, lagrer dem som forslag og krever et separat, synlig
    brukerklikk før enten planen eller en Hevy-mal kan opprettes.

    ``hevy_routines`` er den gjeldende liste-formen (flere maler i ett svar).
    ``hevy_routine`` beholdes som bakoverkompatibelt enkelt-felt: hvis bare det
    er satt, folder API-laget det inn i listen.
    """

    answer: str
    model: str
    operations: list[dict[str, Any]]
    hevy_routines: list[dict[str, Any]] = field(default_factory=list)
    hevy_routine: dict[str, Any] | None = None


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
    """Be DeepSeek om tekst og en ufarlig, valgfri skadestatus-kandidat.

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

    content = _message_content(_request_completion(request, api_key=key, http_client=http_client))
    decoded = _json_object_from_content(content)
    if decoded is None:
        # Behold et godt, men ustrukturert modelsvar. Det får aldri med seg
        # en sideeffekt når JSON-kontrakten ikke ble fulgt.
        return CoachReply(answer=content, model=selected_model)
    answer = decoded.get("answer")
    injury_proposal = decoded.get("injury_proposal")
    return CoachReply(
        answer=answer.strip() if isinstance(answer, str) and answer.strip() else "Jeg fikk ikke formulert et tydelig svar. Prøv igjen.",
        model=selected_model,
        injury_proposal=injury_proposal if isinstance(injury_proposal, dict) else None,
    )


WEEKLY_SYSTEM_PROMPT = """Du er den personlige treningscoachen i Trening, i en
ukesplan-samtale. Du får en kuratert ukekontekst med dagens dato, den valgte
ukens datoer (mandag–søndag med norske ukedagsnavn), aktiv blokk/fase, ukens
planlagte og gjennomførte økter, aktive skader og deterministiske begrensninger,
samt en liten coaching-kjerne. Svar på naturlig norsk, konkret og uten å finne
på data.

Du kjenner alltid dagens dato og hele den valgte ukens datoer fra konteksten.
Spør derfor ALDRI om hvilken dato det er, eller hvilken uke det gjelder — det
står i konteksten. Når brukeren nevner en ukedag (f.eks. «tirsdag og fredag»),
bruk den faktiske datoen for den dagen i den valgte uken.

Du kan foreslå endringer og du KAN foreslå Hevy-maler som systemet oppretter i
Hevy etter at brukeren bekrefter. Si aldri at du «ikke kan pushe til Hevy». Si i
stedet at du kan foreslå en mal og opprette den etter bekreftelse. Du har ikke
selv skrivetilgang: brukeren må se og uttrykkelig bekrefte hvert forslag før noe
skjer. Påstå aldri at noe allerede er endret, lagret eller opprettet, og si
aldri at en styrkeøkt er «sendt til Garmin».

Returner KUN ett gyldig JSON-objekt, uten markdown eller tekst rundt, med denne
formen:
{
  "answer": "Kort svar direkte til brukeren.",
  "operations": [
    {"action": "move", "session_id": 123, "to_date": "YYYY-MM-DD", "reason": "kort grunn"},
    {"action": "skip", "session_id": 123, "reason": "kort grunn"},
    {"action": "replace", "session_id": 123, "type": "easy_run", "description": "...", "target_metrics": {"duration_min": 40}, "reason": "kort grunn"},
    {"action": "add", "date": "YYYY-MM-DD", "type": "strength", "description": "...", "target_metrics": {"duration_min": 30}, "reason": "kort grunn"}
  ],
  "hevy_routines": [
    {
      "title": "Fullkropp A",
      "purpose": "Fullkropp A · tirsdag",
      "date": "YYYY-MM-DD (dag i valgt uke)",
      "notes": "Valgfri forklaring",
      "exercises": [
        {
          "exercise": "English Hevy exercise title",
          "rest_seconds": 120,
          "notes": "valgfritt",
          "sets": [
            {"type": "normal", "weight_kg": 60, "reps": 8}
          ]
        }
      ]
    }
  ]
}

Bruk bare session_id-er som står i konteksten, og bare datoer innen den valgte
uken. Returner alltid "operations": [] når brukeren bare spør et spørsmål, når
informasjonen ikke er tilstrekkelig, eller når ingen konkret planendring bør
foreslås.

Sett ``hevy_routines`` bare når brukeren uttrykkelig ber om en Hevy-mal/rutine.
Da lager du ett element per konkret mal brukeren ber om — ber brukeren om maler
for «tirsdag og fredag», gir du to elementer med hver sin ``date`` for de
riktige dagene i den valgte uken (maks 4 maler). Ønsker brukeren én felles mal
for flere dager, kan du levere én mal og forklare i ``answer`` at den brukes
begge dager. Gi hver mal et tydelig ``title`` og en kort ``purpose`` som
«Fullkropp A · tirsdag». Bruk den vanlige engelske tittelen slik øvelsen er
kjent i Hevys øvelseskatalog, og maks 12 øvelser per mal. Hver mal er bare et
forslag brukeren må bekrefte før det opprettes i Hevy. Returner ellers
``hevy_routines``: [].

En eventuell planendring for de samme dagene er et separat plan-diff-forslag
under ``operations`` — den er ikke det samme som å opprette en Hevy-mal.

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
    raw_routines = decoded.get("hevy_routines")
    single_routine = decoded.get("hevy_routine")
    if isinstance(raw_routines, list):
        hevy_routines = [item for item in raw_routines if isinstance(item, dict)]
    elif isinstance(raw_routines, dict):
        hevy_routines = [raw_routines]
    elif isinstance(single_routine, dict):
        # Bakoverkompatibelt: en modell som fortsatt bruker enkelt-feltet.
        hevy_routines = [single_routine]
    else:
        hevy_routines = []
    return WeeklyCoachReply(
        answer=answer.strip() if isinstance(answer, str) and answer.strip() else "Jeg fikk ikke formulert et tydelig svar. Prøv igjen.",
        model=selected_model,
        operations=[item for item in operations if isinstance(item, dict)] if isinstance(operations, list) else [],
        hevy_routines=hevy_routines,
        hevy_routine=single_routine if isinstance(single_routine, dict) else None,
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
