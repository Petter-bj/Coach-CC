"""Withings OAuth2 + data-fixture spike.

Kjøres manuelt én gang for å:
1. Fullføre OAuth2 authorization code flow (localhost callback)
2. Lagre access_token + refresh_token i credentials/withings.json
3. Hente siste 30 dager med vekt-målinger som fixture

Forhåndskrav:
- Dev-app registrert på https://developer.withings.com med callback
  http://localhost:8080/callback
- WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET i .env

Usage:
    .venv/bin/python spikes/withings_oauth.py
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import ENV_FILE, WITHINGS_CREDS, ensure_runtime_dirs

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_RAW = REPO_ROOT / "tests" / "fixtures" / "withings" / "raw"

AUTH_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
SCOPE = "user.info,user.metrics,user.activity"

# Measurement types we care about — Withings docs table 2
MEASTYPES = [
    1,    # Weight (kg)
    5,    # Fat Free Mass (kg)
    6,    # Fat Ratio (%)
    8,    # Fat Mass Weight (kg)
    76,   # Muscle Mass
    77,   # Hydration
    88,   # Bone Mass
]


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the redirect from Withings with the auth code."""

    captured_code: str | None = None
    captured_state: str | None = None
    captured_error: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            CallbackHandler.captured_error = params["error"][0]
        else:
            CallbackHandler.captured_code = params.get("code", [None])[0]
            CallbackHandler.captured_state = params.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h1>Withings authorisert</h1>"
            b"<p>Du kan lukke denne fanen og g\xc3\xa5 tilbake til terminalen.</p>"
        )

    def log_message(self, *args: object) -> None:
        pass  # quiet


def save_fixture(name: str, data: object) -> None:
    FIXTURES_RAW.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_RAW / f"{name}.json"
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    print(f"  → {path.relative_to(REPO_ROOT)}")


def wait_for_callback(timeout_s: int = 300) -> tuple[str, str]:
    server = http.server.HTTPServer(("127.0.0.1", 8080), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    deadline = time.time() + timeout_s
    while thread.is_alive() and time.time() < deadline:
        time.sleep(0.25)

    server.server_close()

    if CallbackHandler.captured_error:
        raise RuntimeError(f"OAuth error: {CallbackHandler.captured_error}")
    if not CallbackHandler.captured_code:
        raise TimeoutError("Timed out waiting for OAuth callback")
    return CallbackHandler.captured_code, CallbackHandler.captured_state or ""


def main() -> int:
    ensure_runtime_dirs()

    client_id = os.environ.get("WITHINGS_CLIENT_ID")
    client_secret = os.environ.get("WITHINGS_CLIENT_SECRET")
    redirect_uri = os.environ.get("WITHINGS_REDIRECT_URI", "http://localhost:8080/callback")

    if not client_id or not client_secret:
        print("ERROR: sett WITHINGS_CLIENT_ID og WITHINGS_CLIENT_SECRET i .env", file=sys.stderr)
        return 1

    state = secrets.token_urlsafe(16)
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": SCOPE,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_full_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    print("Åpner nettleser for Withings-autorisasjon...")
    print(f"Hvis nettleser ikke åpner, gå til:\n  {auth_full_url}\n")
    webbrowser.open(auth_full_url)

    print("Venter på callback på http://localhost:8080/callback ...")
    code, got_state = wait_for_callback()
    if got_state != state:
        print(f"ADVARSEL: state mismatch. Forventet {state}, fikk {got_state}", file=sys.stderr)

    print("✓ Autorisasjonskode mottatt — bytter mot tokens...")

    token_resp = httpx.post(
        TOKEN_URL,
        data={
            "action": "requesttoken",
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    token_payload = token_resp.json()

    if token_payload.get("status") != 0:
        print(f"ERROR: token-bytte feilet: {token_payload}", file=sys.stderr)
        return 2

    body = token_payload["body"]
    credentials = {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_at": int(time.time()) + int(body["expires_in"]),
        "userid": body["userid"],
        "scope": body.get("scope"),
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }

    WITHINGS_CREDS.parent.mkdir(parents=True, exist_ok=True)
    with WITHINGS_CREDS.open("w") as f:
        json.dump(credentials, f, indent=2)
    os.chmod(WITHINGS_CREDS, 0o600)
    print(f"✓ Credentials lagret i {WITHINGS_CREDS}")

    # Hent siste 30 dager med vekt-målinger
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    print(f"\nHenter målinger {start.date()} → {end.date()}...")

    measure_resp = httpx.post(
        MEASURE_URL,
        headers={"Authorization": f"Bearer {credentials['access_token']}"},
        data={
            "action": "getmeas",
            "startdate": int(start.timestamp()),
            "enddate": int(end.timestamp()),
            "meastypes": ",".join(str(t) for t in MEASTYPES),
        },
        timeout=30,
    )
    measure_resp.raise_for_status()
    measure_payload = measure_resp.json()

    if measure_payload.get("status") != 0:
        print(f"ERROR: getmeas feilet: {measure_payload}", file=sys.stderr)
        return 3

    groups = measure_payload["body"]["measuregrps"]
    print(f"  {len(groups)} måle-grupper funnet")
    save_fixture("measurements_last_30d", measure_payload)

    # Vis en tolkningseksempel
    if groups:
        latest = groups[0]
        print(f"  Siste måling: {datetime.fromtimestamp(latest['date'], timezone.utc).isoformat()}")
        for m in latest.get("measures", []):
            value = m["value"] * (10 ** m["unit"])
            print(f"    type={m['type']:>3}  verdi={value:.2f}")

    print(f"\nFerdig. Fixture i {FIXTURES_RAW.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\nFAIL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(10)
