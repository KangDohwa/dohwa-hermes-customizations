#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_tree=${1:?"usage: scripts/verify.sh <clean-upstream-checkout> [python]"}
python_bin=${2:-"$source_tree/.venv/bin/python"}

if [[ ! -x "$python_bin" ]]; then
  printf 'Python executable not found: %s\n' "$python_bin" >&2
  exit 2
fi

expected_commit=$(
  "$python_bin" -c 'import sys, tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["commit"])' \
    "$root/UPSTREAM.toml"
)
actual_commit=$(git -C "$source_tree" rev-parse HEAD)
if [[ "$actual_commit" != "$expected_commit" ]]; then
  printf 'Upstream mismatch: expected %s, got %s\n' "$expected_commit" "$actual_commit" >&2
  exit 2
fi

worktree=$(mktemp -d "${TMPDIR:-/tmp}/hermes-customizations.XXXXXX")
rmdir "$worktree"
cleanup() {
  git -C "$source_tree" worktree remove --force "$worktree" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "$source_tree" worktree add --detach "$worktree" "$expected_commit" >/dev/null
git -C "$worktree" apply --check "$root/patches/discord-dynamic-presence.patch"
git -C "$worktree" apply "$root/patches/discord-dynamic-presence.patch"
install -Dm644 "$root/overlays/discord_presence.py" \
  "$worktree/plugins/platforms/discord/presence.py"
install -Dm644 "$root/tests/test_discord_presence.py" \
  "$worktree/tests/gateway/test_discord_presence.py"
install -Dm644 "$root/tests/test_discord_presence_integration.py" \
  "$worktree/tests/gateway/test_discord_presence_integration.py"

git -C "$worktree" diff --check
(
  cd "$worktree"
  PYTHONPATH="$worktree" "$python_bin" -m py_compile \
    gateway/run.py \
    plugins/platforms/discord/adapter.py \
    plugins/platforms/discord/presence.py \
    tests/gateway/test_discord_presence.py \
    tests/gateway/test_discord_presence_integration.py
  PYTHONPATH="$worktree" "$python_bin" -m pytest -q \
    tests/gateway/test_discord_presence.py \
    tests/gateway/test_discord_presence_integration.py
)

printf 'Verified Hermes customization against %s\n' "$expected_commit"
