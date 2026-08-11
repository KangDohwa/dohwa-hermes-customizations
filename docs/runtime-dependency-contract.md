# Runtime dependency contract

Keep runtime ownership separate and unify only declarations and read-only checks.

| Boundary | Policy |
| --- | --- |
| Hermes core | Frozen and immutable; never install optional or cron packages into it. |
| Optional capability | One hash-locked thin extension per Python ABI and platform. |
| Deterministic cron | Keep stdlib/local-source jobs on the declared Python ABI. |
| Third-party cron | Create a compatibility-group venv only when a real dependency appears. |
| CLI, agent profile, container | Keep the native ownership boundary; inventory and health-check only. |

`runtime_contract.py` never installs, updates, restarts, schedules, or repairs
anything. `check` is observation-only and runs target-Python AST/import probes,
`bash -n`, extension attestation checks, executable/path checks, and read-only
container inspection. `attest-extension` writes only the explicitly requested
contract file in an unreferenced staging tree.
A failed check is evidence for a separate change, not permission to mutate the
runtime.

## Thin-extension staging

1. Download the exact wheels for the lock's Python/platform into a temporary
   wheelhouse.
2. Install into a new, unreferenced release directory with
   `pip install --no-index --no-deps --require-hashes --target ...`.
3. Remove generated `__pycache__`, `.pyc`, and `.pyo` files, then create
   `.contract.json` with `attest-extension`, pinning the reviewed customization
   commit that contains the checker and lock.
4. Pin the contract's SHA-256 in the private manifest and make the complete
   staged tree read-only.
5. Run `check` and a real provider smoke in a fresh process with bytecode writes
   disabled.
6. Request approval before adding the extension to the Gateway process and
   restarting it.

The DDGS lock intentionally excludes `click`, `httpx`, and `socksio`; those are
shared from the frozen Hermes core. The Parallel lock owns only `parallel-web`;
`anyio`, `distro`, `httpx`, `pydantic`, `sniffio`, and `typing-extensions` stay
core-owned. The Edge TTS lock owns `edge-tts` and `tabulate`; `aiohttp`,
`certifi`, and `typing-extensions` stay core-owned. Keep all three extensions
separate so a capability can be removed without changing another capability's
attestation.

The checker rejects any extension package whose normalized distribution name
overlaps with the core. It also rejects unanchored contracts, generated
bytecode, and writable entries when `read_only = true`. A disabled lazy
installer is not a package-boundary control: the Gateway environment must also
unset or replace any mutable `HERMES_LAZY_INSTALL_TARGET` that bootstrap code
would activate with `site.addsitedir()`.

## Commands

```bash
python scripts/runtime_contract.py check /private/path/runtime-contract.toml
python scripts/runtime_contract.py attest-extension /staged/extension \
  --lock requirements/ddgs-py311-linux-aarch64.lock \
  --python /path/to/frozen/python \
  --output /staged/extension/.contract.json \
  --source-commit "$SOURCE_COMMIT"
```

Do not add a recurring health-check job unless separately approved. Run it
manually or as a deployment preflight.
