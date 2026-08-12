# Dohwa Hermes Customizations

Version-pinned patches, overlays, and focused tests for local
[Hermes Agent](https://github.com/NousResearch/hermes-agent) customizations.

This repository contains source-level customization artifacts only. It does not
contain runtime configuration, credentials, sessions, databases, logs, backups,
or deployment-specific host paths.

## Current compatibility target

| Hermes version | Upstream tag | Upstream commit |
| --- | --- | --- |
| `0.20.0` | `v2026.8.3` | `3c27eb6234bf91b8ceee9e9071591b31e9b148cb` |

`UPSTREAM.toml` is the machine-readable source of truth for this identity.

## Discord dynamic presence

The current customization provides:

- `overlays/discord_presence.py` — fail-open Discord activity controller;
- `patches/discord-dynamic-presence.patch` — minimal gateway and Discord
  adapter lifecycle hooks;
- `tests/test_discord_presence.py` — controller behavior tests; and
- `tests/test_discord_presence_integration.py` — semantic hook integration
  tests for the supported upstream revision.

The patch preserves existing tool-progress and Discord voice-ack callbacks,
tracks generation-aware turn/tool activity, starts approval/response presence
only after successful sends, and cleans up presence tasks on disconnect.

## Lifecycle guard NUL safety

`patches/lifecycle-guard-nul-forward-fix.patch` preserves a known local binary
as a local result, rejects NUL-bearing remote script payloads, and catches
`ValueError` from bounded path reads. Its upstream regression additions verify
that a local binary never falls through to a remote read and that remote NUL
payloads fail safely without crashing the guard.

## Codex account Usage credential coupling

`patches/codex-account-usage-pool-metadata.patch` keeps the OAuth token and
ChatGPT account metadata used for an account Usage request coupled to the same
credential-pool entry. When the runtime resolver reports a pool source, the
patch reselects one currently usable entry instead of combining a stale token
with singleton metadata. Account IDs are derived from the selected OAuth JWT
when available and omitted for opaque tokens.

`tests/test_codex_account_usage_pool_metadata.py` verifies explicit-token,
runtime-pool, opaque-token, and direct-pool behavior. It intentionally does not
add credential rotation or retry behavior.

## Explicit web backend fail-closed behavior

`patches/explicit-web-backend-fail-closed.patch` preserves explicit
`web.search_backend` and `web.extract_backend` selections when unavailable,
preventing credential-driven provider walks. Search may use only the explicitly
configured `web.search_fallback_backend`, and only after a typed retryable
failure or a successful zero-result response. Tavily classifies HTTP auth,
rate-limit, server, network, and timeout failures without string matching.
`tests/test_web_backend_fail_closed.py` verifies the fail-closed paths, bounded
fallback edge, and unchanged auto-detection used when no backend is explicit.

## Verify against clean upstream

The verification script creates a temporary detached worktree, so it does not
modify the supplied upstream checkout.

```bash
scripts/verify.sh /path/to/hermes-agent /path/to/python
```

The checkout must be at the exact commit recorded in `UPSTREAM.toml`. The Python
environment must contain the matching upstream runtime and test dependencies.
The script checks patch applicability and whitespace, installs the overlay and
focused tests into the temporary worktree, compiles affected modules, and runs
the focused test set.

## Runtime dependency contract

The repository also carries a read-only contract for keeping Hermes core,
optional Python extensions, scheduled scripts, external CLIs, agent profiles,
and containers in separate ownership boundaries:

- `scripts/runtime_contract.py` — Python 3.11 preflight, extension attestation,
  and unified health-check;
- `examples/runtime-contract.toml` — deployment-neutral manifest schema;
- `requirements/ddgs-py311-linux-aarch64.lock` — hash-pinned DDGS thin
  extension packages;
- `requirements/parallel-web-py311-linux-aarch64.lock` — the Parallel SDK
  without core-owned shared dependencies;
- `requirements/exa-py311-linux-aarch64.lock` — the Exa SDK without
  core-owned shared dependencies;
- `requirements/edge-tts-py311-linux-aarch64.lock` — Edge TTS plus its
  core-absent `tabulate` dependency; and
- `tests/test_runtime_contract.py` — focused contract tests.

The `check` command is observation-only. `attest-extension` writes only its
explicit contract output; neither command installs packages, updates components,
creates schedules, repairs findings, or restarts the Gateway. See
[`docs/runtime-dependency-contract.md`](docs/runtime-dependency-contract.md).

## Upgrade policy

- `main` represents one currently supported upstream revision.
- Every upstream update is handled as a semantic rebase, even if the old patch
  still applies mechanically.
- Historical compatibility is retained through repository release tags such as
  `hermes-v2026.8.3-r1`, not by accumulating version directories.
- Supporting multiple upstream revisions at the same time is deferred until a
  real deployment requires it.

Applying these artifacts does not migrate or validate runtime databases. State
migration, backup, restore, and production cutover remain separate operational
gates.

## Licensing and attribution

Repository-authored files are available under the root [MIT License](LICENSE).
Patch files include context from Hermes Agent and remain subject to the upstream
license reproduced in [`LICENSES/Hermes-Agent-MIT.txt`](LICENSES/Hermes-Agent-MIT.txt).
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance details.

This is an independently maintained project. It is not affiliated with,
sponsored by, endorsed by, or maintained by Nous Research.
