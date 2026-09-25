#!/usr/bin/env sh
# Install qa-audit in a virtual environment (docs/qa-audit-guide.md §2).
#
#   ./scripts/install.sh                 # chromium only
#   ./scripts/install.sh --all-browsers  # chromium, firefox and webkit
#   ./scripts/install.sh --with-deps     # also the system libraries the browsers need (Linux, uses sudo)
#
# Then: .venv/bin/qa-audit https://example.com   (or activate: . .venv/bin/activate)
set -eu

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
BROWSERS="chromium"
DEPS=""
for arg in "$@"; do
  case "$arg" in
    --all-browsers) BROWSERS="chromium firefox webkit" ;;
    --with-deps) DEPS="--with-deps" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || { echo "Python 3.12 or newer is required (set PYTHON=...)" >&2; exit 1; }

[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[visual]"
# shellcheck disable=SC2086
.venv/bin/python -m playwright install $DEPS $BROWSERS
.venv/bin/qa-audit doctor
echo
echo "Installed. Run: .venv/bin/qa-audit https://example.com   (only sites you are authorized to test)"
