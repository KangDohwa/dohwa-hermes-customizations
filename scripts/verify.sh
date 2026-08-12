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
git -C "$worktree" apply --check \
  "$root/patches/explicit-web-backend-fail-closed.patch"
git -C "$worktree" apply \
  "$root/patches/explicit-web-backend-fail-closed.patch"
git -C "$worktree" apply --check \
  "$root/patches/lifecycle-guard-nul-forward-fix.patch"
git -C "$worktree" apply \
  "$root/patches/lifecycle-guard-nul-forward-fix.patch"
git -C "$worktree" apply --check \
  "$root/patches/codex-account-usage-pool-metadata.patch"
git -C "$worktree" apply \
  "$root/patches/codex-account-usage-pool-metadata.patch"
install -Dm644 "$root/overlays/discord_presence.py" \
  "$worktree/plugins/platforms/discord/presence.py"
install -Dm644 "$root/tests/test_discord_presence.py" \
  "$worktree/tests/gateway/test_discord_presence.py"
install -Dm644 "$root/tests/test_discord_presence_integration.py" \
  "$worktree/tests/gateway/test_discord_presence_integration.py"
install -Dm644 "$root/tests/test_web_backend_fail_closed.py" \
  "$worktree/tests/tools/test_web_backend_fail_closed.py"
install -Dm644 "$root/tests/test_codex_account_usage_pool_metadata.py" \
  "$worktree/tests/agent/test_codex_account_usage_pool_metadata.py"

git -C "$worktree" diff --check
(
  cd "$worktree"
  PYTHONPATH="$worktree" "$python_bin" -m py_compile \
    agent/account_usage.py \
    cron/lifecycle_guard.py \
    gateway/run.py \
    plugins/platforms/discord/adapter.py \
    plugins/platforms/discord/presence.py \
    tools/web_tools.py \
    tests/gateway/test_discord_presence.py \
    tests/gateway/test_discord_presence_integration.py \
    tests/hermes_cli/test_gateway_restart_loop.py \
    tests/tools/test_web_backend_fail_closed.py \
    tests/agent/test_codex_account_usage_pool_metadata.py
  PYTHONPATH="$worktree" "$python_bin" -m pytest -q \
    -W error::ResourceWarning \
    tests/gateway/test_discord_presence.py \
    tests/gateway/test_discord_presence_integration.py \
    tests/hermes_cli/test_gateway_restart_loop.py \
    tests/tools/test_web_backend_fail_closed.py \
    tests/tools/test_web_providers.py \
    tests/tools/test_web_tools_config.py \
    tests/agent/test_account_usage.py \
    tests/agent/test_codex_account_usage_pool_metadata.py
)

printf 'Verified Hermes customization against %s\n' "$expected_commit"
