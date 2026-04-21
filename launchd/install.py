"""Installer eller fjern launchd-jobber for Trening.

Renderer `.plist.template`- og `start-bot.sh.template`-filer ved å fylle
inn bruker-spesifikke paths ({{PYTHON}}, {{REPO}}, {{HOME}}, {{CLAUDE_BIN}},
{{TMUX_BIN}} m.fl.) og bootstraper jobbene i brukerens launchctl-domene.

Usage:
    uv run python -m launchd.install install
    uv run python -m launchd.install uninstall
    uv run python -m launchd.install status
    uv run python -m launchd.install kickstart sync   # tving umiddelbar kjøring
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.paths import APP_SUPPORT, LOGS, ensure_runtime_dirs

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "launchd"
INSTALL_DIR = Path.home() / "Library" / "LaunchAgents"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
SCRIPT_DEST_DIR = APP_SUPPORT / "scripts"
SCRIPT_DEST = SCRIPT_DEST_DIR / "start-bot.sh"

LABEL_PREFIX = "com.trening"
JOBS = ["sync", "backup", "bot"]


def _uid() -> int:
    return os.getuid()


def _domain() -> str:
    return f"gui/{_uid()}"


def _label(job: str) -> str:
    return f"{LABEL_PREFIX}.{job}"


def _plist_path(job: str) -> Path:
    return INSTALL_DIR / f"{_label(job)}.plist"


def _template_path(job: str) -> Path:
    return TEMPLATE_DIR / f"{_label(job)}.plist.template"


def _find_binary(name: str) -> str:
    """Bruk `shutil.which` men fall tilbake på vanlige /opt/homebrew-paths
    siden launchd ikke inheriter brukerens PATH."""
    located = shutil.which(name)
    if located:
        return located
    for candidate in (
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"{Path.home()}/.local/bin/{name}",
    ):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"Fant ikke '{name}' på $PATH eller standard-lokasjoner")


def _placeholders() -> dict[str, str]:
    return {
        "{{PYTHON}}": str(VENV_PYTHON),
        "{{REPO}}": str(REPO_ROOT),
        "{{LOGS}}": str(LOGS),
        "{{HOME}}": str(Path.home()),
        "{{APP_SUPPORT}}": str(APP_SUPPORT),
        "{{CLAUDE_BIN}}": _find_binary("claude"),
        "{{TMUX_BIN}}": _find_binary("tmux"),
    }


def _render(content: str) -> str:
    for ph, val in _placeholders().items():
        content = content.replace(ph, val)
    return content


def _render_template(job: str) -> str:
    return _render(_template_path(job).read_text())


def _render_and_install_bot_script() -> None:
    """Render start-bot.sh.template med bruker-paths og kopier til
    ~/Library/Application Support/Trening/scripts/start-bot.sh.

    macOS TCC blokkerer launchd fra å execve() scripts under ~/Documents/,
    så scriptet må leve utenfor Documents/.
    """
    template = (TEMPLATE_DIR / "start-bot.sh.template").read_text()
    rendered = _render(template)
    SCRIPT_DEST_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPT_DEST.write_text(rendered)
    SCRIPT_DEST.chmod(0o755)
    print(f"✓ Rendret start-bot.sh → {SCRIPT_DEST}")


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

    _render_and_install_bot_script()

    for job in JOBS:
        plist = _plist_path(job)
        content = _render_template(job)
        plist.write_text(content)
        print(f"✓ Skrev {plist}")

        # Bootout hvis allerede lastet (for re-last med oppdatert innhold)
        _run_launchctl("bootout", _domain(), str(plist))

        rc, out = _run_launchctl("bootstrap", _domain(), str(plist))
        if rc == 0:
            print(f"✓ Bootstrappet {_label(job)}")
        else:
            print(f"✗ Bootstrap feilet for {job}: {out}", file=sys.stderr)
            return 1

    print("\n✓ Alle jobber installert. Verifiser med:")
    print(f"    launchctl list | grep {LABEL_PREFIX}")
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
    lines = [ln for ln in out.splitlines() if LABEL_PREFIX in ln]
    if not lines:
        print("Ingen trening-jobber registrert")
        return 0
    print("PID    Exit Label")
    for ln in lines:
        print(f"  {ln}")
    return 0


def kickstart(job: str) -> int:
    """Tving umiddelbar kjøring av en jobb (nyttig for debug)."""
    rc, out = _run_launchctl("kickstart", "-k", f"{_domain()}/{_label(job)}")
    print(out or f"✓ Kickstartet {_label(job)}")
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
