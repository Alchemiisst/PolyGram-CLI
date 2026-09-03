#!/usr/bin/env sh
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Error: Python 3 is required.' >&2
  exit 1
fi

python3 -m pip install --user -r "$REPO_DIR/requirements.txt"
chmod +x "$REPO_DIR/polygram" "$REPO_DIR/polygram.py"
ln -sf "$REPO_DIR/polygram" "$BIN_DIR/PolyGram"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *)
    printf '\nAdd this directory to PATH, then restart Termux:\n'
    printf '  export PATH="$HOME/.local/bin:$PATH"\n'
    printf '\nFor permanent setup, add that line to ~/.bashrc.\n'
    ;;
esac

printf '\nPolyGram installed. Start it with:\n  PolyGram\n'
