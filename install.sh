#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BIN_DIR="${HOME}/.local/bin"
COMMAND="${BIN_DIR}/PolyGram"
mkdir -p "$BIN_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: Python 3 is required." >&2
  exit 1
fi

python3 -m pip install -r "$REPO_DIR/requirements.txt"
chmod +x "$REPO_DIR/polygram.py" "$REPO_DIR/polygram"

# Install a real wrapper with an absolute repository path. This avoids the
# common symlink problem where $0 points at ~/.local/bin instead of the repo.
cat > "$COMMAND" <<EOF
#!/usr/bin/env bash
exec python3 "$REPO_DIR/polygram.py" "\$@"
EOF
chmod 755 "$COMMAND"

# Make the command available in new Termux sessions.
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if [ -f "$HOME/.bashrc" ] && grep -Fqx "$PATH_LINE" "$HOME/.bashrc"; then
  :
elif [ -f "$HOME/.bashrc" ]; then
  printf '\n# PolyGram command\n%s\n' "$PATH_LINE" >> "$HOME/.bashrc"
else
  printf '%s\n' "$PATH_LINE" > "$HOME/.bashrc"
fi

export PATH="$BIN_DIR:$PATH"

echo
echo "PolyGram installed successfully."
echo "Command: $COMMAND"
echo
echo "Start now:"
echo "  PolyGram"
echo
echo "The command will also work after restarting Termux."
