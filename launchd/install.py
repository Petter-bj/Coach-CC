"""Installer eller fjern launchd-jobber for Trening.

Fyller ut {{PYTHON}}, {{REPO}}, {{LOGS}}-placeholders i .plist.template-filer
og bootstraper dem i brukerens launchctl-domene.

Usage:
    uv run python -m launchd.install install
    uv run python -m launchd.install uninstall
    uv run python -m launchd.install status
    uv run python -m launchd.install kickstart sync   # tving umiddelbar kjøring
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.paths import APP_SUPPORT, LOGS, ensure_runtime_dirs

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "launchd"
INSTALL_DIR = Path.home() / "Library" / "LaunchAgents"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

JOBS = ["sync", "backup", "bot"]


def _uid() -> int:
    return os.getuid()


def _domain() -> str:
    return f"gui/{_uid()}"


def _plist_path(job: str) -> Path:
    return INSTALL_DIR / f"com.petter.trening.{job}.plist"


def _template_path(job: str) -> Path:
    return TEMPLATE_DIR / f"com.petter.trening.{job}.plist.template"


def _render_template(job: str) -> str:
    template = _template_path(job).read_text()
    return (
        template
        .replace("{{PYTHON}}", str(VENV_PYTHON))
        .replace("{{REPO}}", str(REPO_ROOT))
        .replace("{{LOGS}}", str(LOGS))
        .replace("{{HOME}}", str(Path.home()))
        .replace("{{APP_SUPPORT}}", str(APP_SUPPORT))
    )


def _copy_bot_script() -> None:
    """Kopier start-bot.sh til ~/Library/Application Support/Trening/scripts/.

    macOS TCC blokkerer launchd fra å execve() scripts under ~/Documents/,
    men Python-binær via symlink til Homebrew er tillatt. Vi må ha scriptet
    utenfor Documents for å kjøre det fra launchd.
    """
    import shutil
    src = TEMPLATE_DIR / "start-bot.sh"
    dest_dir = APP_SUPPORT / "scripts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "start-bot.sh"
    shutil.copy2(src, dest)
    dest.chmod(0o755)
    print(f"✓ Kopierte {src.name} → {dest}")


def _run_launchctl(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["launchctl", *args],
        capture_output=True, text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def install() -> int:
    """Generer plists og bootstrap i launchd."""
    if not VENV_PYTHON.exists():
        print(f"✗ venv ikke funnet: {VENV_PYTHON}", file=sys.stderr)
        print("  Kjør `uv sync` først.", file=sys.stderr)
        return 1

    ensure_runtime_dirs()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    # Kopier bot-script utenfor Documents/ (TCC-grunner)
    _copy_bot_script()

    for job in JOBS:
        plist = _plist_path(job)
        content = _render_template(job)
        plist.write_text(content)
        print(f"✓ Skrev {plist}")

        # Bootout hvis allerede lastet (for å re-laste med evt. endret innhold)
        _run_launchctl("bootout", _domain(), str(plist))

        rc, out = _run_launchctl("bootstrap", _domain(), str(plist))
        if rc == 0:
            print(f"✓ Bootstrappet com.petter.trening.{job}")
        else:
            print(f"✗ Bootstrap feilet for {job}: {out}", file=sys.stderr)
            return 1

    print("\n✓ Alle jobber installert. Verifiser med:")
    print(f"    launchctl list | grep com.petter.trening")
    return 0


def uninstall() -> int:
    for job in JOBS:
        plist = _plist_path(job)
        if not plist.exists():
            continue
        _run_launchctl("bootout", _domain(), str(plist))
        plist.unlink()
        print(f"✓ Fjernet {plist.name}")
    return 0


def status() -> int:
    rc, out = _run_launchctl("list")
    lines = [ln for ln in out.splitlines() if "com.petter.trening" in ln]
    if not lines:
        print("Ingen trening-jobber registrert")
        return 0
    print("PID    Exit Label")
    for ln in lines:
        print(f"  {ln}")
    return 0


def kickstart(job: str) -> int:
    """Tving umiddelbar kjøring av en jobb (nyttig for debug)."""
    label = f"com.petter.trening.{job}"
    rc, out = _run_launchctl("kickstart", "-k", f"{_domain()}/{label}")
    print(out or f"✓ Kickstartet {label}")
    return rc


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    cmd = argv[1]
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    if cmd == "status":
        return status()
    if cmd == "kickstart":
        if len(argv) < 3 or argv[2] not in JOBS:
            print(f"Usage: kickstart <{' | '.join(JOBS)}>", file=sys.stderr)
            return 1
        return kickstart(argv[2])
    print(f"Ukjent kommando: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
