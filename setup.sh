#!/usr/bin/env bash
# Local setup only: no API key, model requests or Azure deployment.
set -euo pipefail
trap 'printf "\nSetup stopped at line %s. Keep the first error above.\n" "$LINENO" >&2' ERR

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
PYTHON_SERIES="${1:-3.12}"
case "$PYTHON_SERIES" in
    3.12|3.15) ;;
    *) printf 'Usage: bash setup.sh [3.12|3.15]\n' >&2; exit 2 ;;
esac
ENV_DIR=".venv${PYTHON_SERIES/./}"

PYTHON_BIN=""
FROM_PATH="$(command -v "python$PYTHON_SERIES" || true)"
for candidate in "$FROM_PATH" "/Library/Frameworks/Python.framework/Versions/$PYTHON_SERIES/bin/python$PYTHON_SERIES" "$HOME/.local/bin/python$PYTHON_SERIES"; do
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c 'import sys; want=tuple(map(int,sys.argv[1].split("."))); raise SystemExit(0 if sys.version_info[:2] == want else 1)' "$PYTHON_SERIES" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
if [[ -z "$PYTHON_BIN" ]]; then
    printf 'Python %s was not found. Downloading its installer is not the same as installing it.\n' "$PYTHON_SERIES" >&2
    printf 'Install that Python version, open a new Terminal, then rerun setup.\n' >&2
    exit 2
fi
printf 'Using: %s\n' "$PYTHON_BIN"
"$PYTHON_BIN" --version

# pycares 4.x and PyYAML 6.0.3 need source builds on CPython 3.15.
if [[ "$PYTHON_SERIES" == "3.15" && "$(uname -s)" == "Darwin" ]]; then
    if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
        printf 'macOS Command Line Tools are required to build dependencies.\n' >&2
        printf 'Run: xcode-select --install\nComplete that installation, then rerun: bash setup.sh\n' >&2
        exit 2
    fi
elif [[ "$PYTHON_SERIES" == "3.15" ]] && ! command -v cc >/dev/null 2>&1; then
    printf 'A C compiler is required to build dependencies. Install your platform build tools, then retry.\n' >&2
    exit 2
fi

if [[ -e "$ENV_DIR" ]]; then
    if [[ ! -x "$ENV_DIR/bin/python" ]]; then
        printf '%s exists but is incomplete. Use a fresh extracted folder; existing files were preserved.\n' "$ENV_DIR" >&2
        exit 2
    fi
    "$ENV_DIR/bin/python" -c 'import sys; want=tuple(map(int,sys.argv[1].split("."))); raise SystemExit(0 if sys.version_info[:2] == want else 1)' "$PYTHON_SERIES"
else
    "$PYTHON_BIN" -m venv "$ENV_DIR"
fi

"$ENV_DIR/bin/python" -m ensurepip --upgrade
"$ENV_DIR/bin/python" -m pip install --upgrade pip
"$ENV_DIR/bin/python" -m pip install --require-hashes -r requirements.lock
"$ENV_DIR/bin/python" -m pip install --force-reinstall --no-deps wheels/msc_iac_experiments-1.2.0-py3-none-any.whl
"$ENV_DIR/bin/python" -m pip check
"$ENV_DIR/bin/python" scripts/install_terraform.py
"$ENV_DIR/bin/python" -m pytest -q

if [[ ! -e config.json && ! -e reference-inputs && ! -e schemas ]]; then
    "$ENV_DIR/bin/iac-exp" init
    "$ENV_DIR/bin/python" - "$ENV_DIR" <<'PY'
import json
import sys
from pathlib import Path
p = Path('config.json')
config = json.loads(p.read_text())
config['terraform_bin'] = str(Path('.tools/terraform').resolve())
config['checkov_bin'] = str(Path(sys.argv[1], 'bin', 'checkov').resolve())
p.write_text(json.dumps(config, indent=2) + '\n')
PY
else
    printf 'Existing configuration was preserved. Check executable paths in config.json before verification.\n'
fi
printf '\nSetup complete using Python %s. CLI: %s/bin/iac-exp\n' "$PYTHON_SERIES" "$ENV_DIR"
printf 'Read START_HERE_EL.md for Gemini API key, verification and experiments.\n'
