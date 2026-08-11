"""Tests for the read-only scheduled-workload runtime contract."""

import json
import platform
import sysconfig
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "runtime_contract.py"


def _run(manifest: Path):
    return subprocess.run(
        [sys.executable, str(CHECKER), "check", str(manifest), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )


def test_valid_stdlib_python_script_passes(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("import json\nprint(json.dumps({'ok': True}))\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[python_scripts]]
name = "job"
path = "{script}"
runtime = "host"
stdlib_only = true
'''
    )

    result = _run(manifest)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["checks"][-1]["code"] == "python_script_ok"


def test_stdlib_contract_rejects_third_party_import(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("import definitely_not_stdlib\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[python_scripts]]
name = "job"
path = "{script}"
runtime = "host"
stdlib_only = true
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["checks"][-1] == {
        "ok": False,
        "code": "non_stdlib_import",
        "name": "job",
        "detail": "definitely_not_stdlib",
    }


def test_shell_contract_rejects_invalid_syntax(tmp_path):
    script = tmp_path / "job.sh"
    script.write_text("if true; then\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[shell_scripts]]
name = "job"
path = "{script}"
shell = "/usr/bin/bash"
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["checks"][-1]["code"] == "shell_syntax_error"


def test_missing_boundary_path_fails(tmp_path):
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[boundaries]]
name = "agent-profile"
kind = "directory"
path = "{tmp_path / "missing"}"
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "boundary_missing"


def test_missing_cli_executable_fails(tmp_path):
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[commands]]
name = "tool"
executable = "{tmp_path / "missing"}"
boundary = "host-cli"
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "command_missing"


def test_container_image_drift_fails(tmp_path):
    engine = tmp_path / "docker"
    engine.write_text(
        '#!/usr/bin/env python3\n'
        'import json\n'
        'print(json.dumps([{"Image": "sha256:actual", "State": {"Status": "running"}}]))\n'
    )
    engine.chmod(0o755)
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[containers]]
name = "service"
engine = "{engine}"
expected_image = "sha256:expected"
expected_state = "running"
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "container_image_mismatch"


def test_attested_extension_passes_contract_check(tmp_path):
    extension = tmp_path / "extension"
    (extension / "ddgs").mkdir(parents=True)
    (extension / "ddgs" / "__init__.py").write_text("VALUE = 1\n")
    metadata = extension / "ddgs-1.0.dist-info" / "METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Name: ddgs\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"ddgs==1.0 --hash=sha256:{'a' * 64}\n")
    contract = extension / ".contract.json"

    attest = subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 40],
        text=True, capture_output=True, check=False,
    )

    assert attest.returncode == 0, attest.stderr
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[extensions]]
name = "search"
path = "{extension}"
contract = "{contract}"
contract_sha256 = "{__import__('hashlib').sha256(contract.read_bytes()).hexdigest()}"
source_commit = "{'a' * 40}"
lockfile = "{lock}"
runtime = "host"
'''
    )

    result = _run(manifest)

    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_ok"

    (extension / "ddgs" / "__init__.py").write_text("VALUE = 2\n")
    drift = _run(manifest)
    assert drift.returncode == 1
    assert json.loads(drift.stdout)["checks"][-1]["code"] == "extension_file_drift"


def test_extension_import_failure_is_reported(tmp_path):
    extension = tmp_path / "extension"
    (extension / "broken").mkdir(parents=True)
    (extension / "broken" / "__init__.py").write_text("raise RuntimeError('boom')\n")
    metadata = extension / "broken-1.0.dist-info" / "METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Name: broken\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"broken==1.0 --hash=sha256:{'b' * 64}\n")
    contract = extension / ".contract.json"
    subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 40],
        check=True,
    )
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[extensions]]
name = "broken"
path = "{extension}"
contract = "{contract}"
contract_sha256 = "{__import__('hashlib').sha256(contract.read_bytes()).hexdigest()}"
source_commit = "{'a' * 40}"
lockfile = "{lock}"
runtime = "host"
probe_imports = ["broken"]
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_import_error"


def test_local_source_tree_is_parsed_by_target_python(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("import local_job\n")
    source = tmp_path / "local_job"
    source.mkdir()
    (source / "broken.py").write_text("if True print('broken')\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[python_scripts]]
name = "job"
path = "{script}"
runtime = "host"
stdlib_only = true
allow_imports = ["local_job"]
source_paths = ["{source}"]
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "python_source_error"


def test_extension_cannot_overlap_frozen_core_distribution(tmp_path):
    import importlib.metadata

    version = importlib.metadata.version("pytest")
    extension = tmp_path / "extension"
    (extension / "pytest").mkdir(parents=True)
    (extension / "pytest" / "__init__.py").write_text("VALUE = 1\n")
    metadata = extension / f"pytest-{version}.dist-info" / "METADATA"
    metadata.parent.mkdir()
    metadata.write_text(f"Name: pytest\nVersion: {version}\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"pytest=={version} --hash=sha256:{'c' * 64}\n")
    contract = extension / ".contract.json"
    subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 40],
        check=True,
    )
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[extensions]]
name = "overlap"
path = "{extension}"
contract = "{contract}"
contract_sha256 = "{__import__('hashlib').sha256(contract.read_bytes()).hexdigest()}"
source_commit = "{'a' * 40}"
lockfile = "{lock}"
runtime = "host"
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_core_overlap"


def test_relative_imports_in_declared_local_sources_are_allowed(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from .helper import VALUE\n")
    (package / "helper.py").write_text("VALUE = 1\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[python_scripts]]
name = "pkg"
path = "{package / '__init__.py'}"
runtime = "host"
stdlib_only = true
allow_imports = ["pkg"]
source_paths = ["{package}"]
'''
    )

    result = _run(manifest)

    assert result.returncode == 0


def test_python_preflight_rejects_source_that_does_not_compile(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("return\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[python_scripts]]
name = "job"
path = "{script}"
runtime = "host"
stdlib_only = true
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "python_script_error"


def test_missing_shell_is_reported_as_json(tmp_path):
    script = tmp_path / "job.sh"
    script.write_text("true\n")
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[shell_scripts]]
name = "job"
path = "{script}"
shell = "{tmp_path / 'missing-bash'}"
'''
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "shell_unavailable"


def test_empty_manifest_is_not_healthy(tmp_path):
    manifest = tmp_path / "runtime.toml"
    manifest.write_text("schema_version = 1\n")

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "empty_manifest"


def _new_extension(tmp_path):
    extension = tmp_path / "extension"
    (extension / "demo").mkdir(parents=True)
    (extension / "demo" / "__init__.py").write_text("VALUE = 1\n")
    metadata = extension / "demo-1.0.dist-info" / "METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Name: demo\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"demo==1.0 --hash=sha256:{'d' * 64}\n")
    contract = extension / ".contract.json"
    subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 40],
        check=True,
    )
    return extension, lock, contract


def _extension_manifest(tmp_path, extension, lock, contract, **extra):
    values = {
        "contract_sha256": __import__("hashlib").sha256(contract.read_bytes()).hexdigest(),
        "source_commit": "a" * 40,
        "read_only": "false",
        **extra,
    }
    manifest = tmp_path / "runtime.toml"
    manifest.write_text(
        f'''schema_version = 1

[[python_runtimes]]
name = "host"
executable = "{sys.executable}"
expected = "{sys.version_info.major}.{sys.version_info.minor}"

[[extensions]]
name = "demo"
path = "{extension}"
contract = "{contract}"
contract_sha256 = "{values['contract_sha256']}"
source_commit = "{values['source_commit']}"
lockfile = "{lock}"
runtime = "host"
probe_imports = ["demo"]
read_only = {values['read_only']}
'''
    )
    return manifest


def test_extension_contract_requires_external_hash_anchor(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    manifest = _extension_manifest(
        tmp_path, extension, lock, contract, contract_sha256="0" * 64
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_contract_drift"


def test_extension_rejects_unattested_bytecode(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    cache = extension / "demo" / "__pycache__"
    cache.mkdir()
    (cache / "demo.cpython-311.pyc").write_bytes(b"unattested")
    manifest = _extension_manifest(tmp_path, extension, lock, contract)

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_unattested_bytecode"


def test_read_only_extension_rejects_writable_entries(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    manifest = _extension_manifest(
        tmp_path, extension, lock, contract, read_only="true"
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_writable"


def test_extension_drift_is_rejected_before_import_probe_runs(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    marker = tmp_path / "imported"
    (extension / "demo" / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    )
    for path in (extension, *extension.rglob("*")):
        path.chmod(path.stat().st_mode & ~0o222)
    manifest = _extension_manifest(
        tmp_path, extension, lock, contract, read_only="true"
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_file_drift"
    assert not marker.exists()


def test_attestation_records_full_python_abi(tmp_path):
    extension = tmp_path / "extension"
    (extension / "demo-1.0.dist-info").mkdir(parents=True)
    (extension / "demo-1.0.dist-info" / "METADATA").write_text("Name: demo\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"demo==1.0 --hash=sha256:{'d' * 64}\n")
    contract = extension / ".contract.json"

    result = subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 40],
        text=True, capture_output=True,
    )

    assert result.returncode == 0
    assert json.loads(contract.read_text())["python_abi"] == {
        "version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "implementation": sys.implementation.name,
        "soabi": sysconfig.get_config_var("SOABI"),
        "cache_tag": sys.implementation.cache_tag,
        "system": platform.system(),
        "machine": platform.machine(),
    }


def test_extension_rejects_unknown_contract_schema(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    data = json.loads(contract.read_text())
    data["schema_version"] = 999
    contract.write_text(json.dumps(data))
    manifest = _extension_manifest(tmp_path, extension, lock, contract)

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_contract_schema"


def test_attestation_records_required_source_commit(tmp_path):
    extension = tmp_path / "extension"
    (extension / "demo-1.0.dist-info").mkdir(parents=True)
    (extension / "demo-1.0.dist-info" / "METADATA").write_text("Name: demo\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"demo==1.0 --hash=sha256:{'d' * 64}\n")
    contract = extension / ".contract.json"
    commit = "a" * 40

    result = subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", commit],
        text=True, capture_output=True,
    )

    assert result.returncode == 0
    assert json.loads(contract.read_text())["source_commit"] == commit


def test_extension_rejects_source_commit_mismatch(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    manifest = _extension_manifest(
        tmp_path, extension, lock, contract, source_commit="b" * 40
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_source_mismatch"


def test_extension_rejects_python_abi_mismatch(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    data = json.loads(contract.read_text())
    data["python_abi"]["soabi"] = "different"
    contract.write_text(json.dumps(data))
    manifest = _extension_manifest(tmp_path, extension, lock, contract)

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_abi_mismatch"


def test_extension_rejects_missing_source_commit_on_both_sides(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    data = json.loads(contract.read_text())
    del data["source_commit"]
    contract.write_text(json.dumps(data))
    manifest = _extension_manifest(tmp_path, extension, lock, contract)
    manifest.write_text(
        manifest.read_text().replace(f'source_commit = "{"a" * 40}"\n', "")
    )

    result = _run(manifest)

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"][-1]["code"] == "extension_source_mismatch"


def test_unsupported_manifest_schema_stops_before_extension_import(tmp_path):
    extension, lock, contract = _new_extension(tmp_path)
    sentinel = tmp_path / "schema-imported"
    (extension / "demo" / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n"
    )
    subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 40],
        check=True,
    )
    manifest = _extension_manifest(tmp_path, extension, lock, contract)
    manifest.write_text(manifest.read_text().replace("schema_version = 1", "schema_version = 999", 1))

    result = _run(manifest)

    assert result.returncode == 1
    assert [item["code"] for item in json.loads(result.stdout)["checks"]] == ["unsupported_schema"]
    assert not sentinel.exists()


def test_attestation_rejects_nonstandard_git_object_id_length(tmp_path):
    extension = tmp_path / "extension"
    (extension / "demo-1.0.dist-info").mkdir(parents=True)
    (extension / "demo-1.0.dist-info" / "METADATA").write_text("Name: demo\nVersion: 1.0\n")
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"demo==1.0 --hash=sha256:{'d' * 64}\n")
    contract = extension / ".contract.json"

    result = subprocess.run(
        [sys.executable, str(CHECKER), "attest-extension", str(extension),
         "--lock", str(lock), "--python", sys.executable, "--output", str(contract),
         "--source-commit", "a" * 41],
        text=True, capture_output=True,
    )

    assert result.returncode != 0
    assert not contract.exists()
