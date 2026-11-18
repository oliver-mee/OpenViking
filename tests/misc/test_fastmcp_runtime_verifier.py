"""Release gates use isolated files and mocked services, never the live runtime."""

import hashlib
import importlib.util
import json
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
VERSION = "0.5.5+ome.fastmcp42.1"


def load_script(name, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def verifier(monkeypatch):
    return load_script("verify_fastmcp_runtime", monkeypatch)


@pytest.fixture
def installed_runtime(tmp_path):
    venv = tmp_path / "venv"
    site = venv / "lib/python3.14/site-packages"
    site.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin/python").symlink_to(sys.executable)
    (venv / "pyvenv.cfg").write_text(
        "version_info = 3.14.6\ninclude-system-site-packages = false\n"
    )
    info = f"openviking-{VERSION}.dist-info"
    payload = {
        "openviking/__init__.py": b"VERSION = 'qualified'\n",
        "openviking/lib/test.so": b"\x7fELF-test-native-payload",
        info
        + "/METADATA": f"Metadata-Version: 2.4\nName: openviking\nVersion: {VERSION}\n".encode(),
        info + "/entry_points.txt": (
            b"[console_scripts]\nopenviking-server = openviking_cli.server_bootstrap:main\n"
        ),
        info + "/RECORD": b"original-wheel-record\n",
    }
    wheel = tmp_path / f"openviking-{VERSION}-cp310-abi3-linux_aarch64.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in payload.items():
            mode = 0o755 if name.endswith(".so") else 0o644
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (0o100000 | mode) << 16
            archive.writestr(entry, data)
            installed = site / name
            installed.parent.mkdir(parents=True, exist_ok=True)
            installed.write_bytes(data)
            installed.chmod(mode)
    # Installers legitimately regenerate RECORD and add console-script entries.
    (site / info / "RECORD").write_text("rewritten-installed-record\n")
    for name, version in (("fastmcp", "4.2.1"), ("mcp", "2.3.0"), ("some_dependency", "1.0.0")):
        dist = site / f"{name}-{version}.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    launcher = venv / "bin/openviking-server"
    launcher.write_text(
        f"#!{venv}/bin/python\n"
        "# -*- coding: utf-8 -*-\n"
        "import sys\n"
        "from openviking_cli.server_bootstrap import main\n"
        'if __name__ == "__main__":\n'
        '    if sys.argv[0].endswith("-script.pyw"):\n'
        "        sys.argv[0] = sys.argv[0][:-11]\n"
        '    elif sys.argv[0].endswith(".exe"):\n'
        "        sys.argv[0] = sys.argv[0][:-4]\n"
        "    sys.exit(main())\n"
    )
    launcher.chmod(0o755)
    return venv, site, wheel


def test_exact_installed_payload_and_rewritten_record_are_qualified(verifier, installed_runtime):
    venv, site, wheel = installed_runtime
    first = verifier.fingerprint_runtime(venv, wheel, VERSION)
    assert first == verifier.fingerprint_runtime(venv, wheel, VERSION)
    assert first["payload_files"] == 4
    assert first["native_artifacts"] == ["openviking/lib/test.so"]
    assert first["dependency_versions"]["some-dependency"] == "1.0.0"
    qualification = {"runtime_fingerprint": first, "wheel_sha256": first["wheel_sha256"]}
    assert verifier.verify_qualified_runtime(qualification, venv, wheel, VERSION) == first
    (site / f"openviking-{VERSION}.dist-info/RECORD").write_text("another installer record\n")
    # It is exempt from wheel equality, but still changes the qualified closure.
    assert (
        verifier.fingerprint_runtime(venv, wheel, VERSION)["payload_sha256"]
        == first["payload_sha256"]
    )
    with pytest.raises(RuntimeError, match="changed since qualification"):
        verifier.verify_qualified_runtime(qualification, venv, wheel, VERSION)


@pytest.mark.parametrize("path", ["openviking/__init__.py", "openviking/lib/test.so"])
def test_same_version_stale_or_tampered_payload_is_rejected(verifier, installed_runtime, path):
    venv, site, wheel = installed_runtime
    (site / path).write_bytes(b"stale or modified installed bytes")
    with pytest.raises(RuntimeError, match="differs from qualified wheel"):
        verifier.fingerprint_runtime(venv, wheel, VERSION)


def test_native_execution_modes_are_part_of_the_gate(verifier, installed_runtime):
    venv, site, wheel = installed_runtime
    (site / "openviking/lib/test.so").chmod(0o644)
    with pytest.raises(RuntimeError, match="native mode differs"):
        verifier.fingerprint_runtime(venv, wheel, VERSION)


def test_dependency_drift_invalidates_qualification(verifier, installed_runtime):
    venv, site, wheel = installed_runtime
    fingerprint = verifier.fingerprint_runtime(venv, wheel, VERSION)
    qualification = {
        "runtime_fingerprint": fingerprint,
        "wheel_sha256": fingerprint["wheel_sha256"],
    }
    (site / "some_dependency-1.0.0.dist-info/METADATA").write_text(
        "Name: some_dependency\nVersion: 1.0.1\n"
    )
    with pytest.raises(RuntimeError, match="changed since qualification"):
        verifier.verify_qualified_runtime(qualification, venv, wheel, VERSION)


@pytest.mark.parametrize("change", ["wrong_interpreter", "not_executable"])
def test_launcher_identity_and_permissions_are_checked(verifier, installed_runtime, change):
    venv, _, wheel = installed_runtime
    launcher = venv / "bin/openviking-server"
    if change == "wrong_interpreter":
        launcher.write_text(launcher.read_text().replace(str(venv / "bin/python"), "/other/python"))
    else:
        launcher.chmod(0o644)
    with pytest.raises(RuntimeError, match="launcher"):
        verifier.fingerprint_runtime(venv, wheel, VERSION)


def test_payload_symlinks_cannot_escape_the_candidate(verifier, installed_runtime, tmp_path):
    venv, site, wheel = installed_runtime
    installed = site / "openviking/__init__.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(installed.read_bytes())
    installed.unlink()
    installed.symlink_to(outside)
    with pytest.raises(RuntimeError, match="symlink"):
        verifier.fingerprint_runtime(venv, wheel, VERSION)


def test_fingerprint_cannot_be_edited_without_updating_its_digest(verifier, installed_runtime):
    venv, _, wheel = installed_runtime
    fingerprint = verifier.fingerprint_runtime(venv, wheel, VERSION)
    fingerprint["dependency_versions"]["some-dependency"] = "0.0.0"
    qualification = {
        "runtime_fingerprint": fingerprint,
        "wheel_sha256": fingerprint["wheel_sha256"],
    }
    with pytest.raises(RuntimeError, match="fingerprint is inconsistent"):
        verifier.verify_qualified_runtime(qualification, venv, wheel, VERSION)


def test_write_timeout_still_cleans_up_and_preserves_primary_error(tmp_path, monkeypatch):
    deploy = load_script("deploy_fastmcp_candidate", monkeypatch)
    baseline = tmp_path / "test_scripts/fastmcp-migration/production-tools-before.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text('{"tools":[{"name":"health"}]}')
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    guides = [f"guide://{number}/SKILL.md" for number in range(5)]
    state = {"cleanup_attempts": []}
    calls = []
    primary = TimeoutError("private write details")

    def rpc(method, params=None):
        if method == "tools/list":
            return {
                "tools": [{"name": name} for name in ("health", "list_resources", "read_resource")]
            }
        return {"resources": [{"uri": uri} for uri in guides]}

    def call(name, arguments=None):
        calls.append((name, arguments))
        if name == "write":
            raise primary
        if name == "forget":
            raise OSError("private cleanup details")
        if name == "read":
            return {"content": "Designing reliable MCP servers"}
        return {"content": "description: guide"}

    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError("http://localhost/", 503, "unavailable", None, None)

    monkeypatch.setattr(deploy, "rpc", rpc)
    monkeypatch.setattr(deploy, "call", call)
    monkeypatch.setattr(deploy, "http", unavailable)
    with pytest.raises(TimeoutError) as caught:
        deploy.smoke("unused-before-write-failure", state)
    assert caught.value is primary
    assert state["stage"] == "write"
    assert any(name == "forget" and args["uri"] == state["fixture"] for name, args in calls)
    assert state["cleanup_attempts"][0]["state"] == "failed"
    assert state["cleanup_attempts"][0]["exception_type"] == "HTTPError"
    assert "private" not in json.dumps(state)


def test_failed_rollback_records_both_failures_without_secret_messages(tmp_path, monkeypatch):
    deploy = load_script("deploy_fastmcp_candidate", monkeypatch)
    monkeypatch.setattr(deploy, "DROPIN", tmp_path / "override.conf")
    receipts = []
    restarts = 0

    def run(*args):
        nonlocal restarts
        if "restart" in args:
            restarts += 1
            if restarts == 1:
                raise ValueError("private deployment failure")
            raise OSError("private rollback failure")
        return ""

    monkeypatch.setattr(deploy, "run", run)
    monkeypatch.setattr(deploy, "write_receipt", lambda receipt: receipts.append(receipt) or True)
    with pytest.raises(RuntimeError, match="automatic rollback failed"):
        deploy.promote(b"[Service]\n", {"version": "old"}, {}, {}, tmp_path, "configuration-hash")
    assert len(receipts) == 1
    assert receipts[0]["state"] == "rollback_failed"
    assert receipts[0]["exception_type"] == "ValueError"
    assert receipts[0]["rollback"]["exception_type"] == "OSError"
    assert receipts[0]["rollback"]["stage"] == "restart_original"
    assert "private" not in json.dumps(receipts)


def test_write_timeout_cleanup_is_retried_after_original_runtime_returns(tmp_path, monkeypatch):
    deploy = load_script("deploy_fastmcp_candidate", monkeypatch)
    monkeypatch.setattr(deploy, "DROPIN", tmp_path / "override.conf")
    receipts = []
    health_checks = []
    cleanup = []

    def smoke(config_hash, state):
        state["stage"] = "write"
        state["fixture"] = "viking://resources/fastmcp-migration-check-" + "a" * 32
        state["cleanup_attempts"].append({"stage": "after_smoke", "state": "failed"})
        raise TimeoutError("private write failure")

    def restored(version):
        health_checks.append(version)
        return {"healthy": True, "version": version}

    def clean(state, stage):
        cleanup.append((state["fixture"], stage))
        state["cleanup_attempts"].append({"stage": stage, "state": "absent"})
        state["cleanup_verified"] = True

    monkeypatch.setattr(deploy, "run", lambda *args: "")
    monkeypatch.setattr(deploy, "smoke", smoke)
    monkeypatch.setattr(deploy, "wait_for_health", restored)
    monkeypatch.setattr(deploy, "cleanup_fixture", clean)
    monkeypatch.setattr(deploy, "write_receipt", lambda receipt: receipts.append(receipt) or True)
    with pytest.raises(RuntimeError, match="original runtime restored"):
        deploy.promote(b"[Service]\n", {"version": "old"}, {}, {}, tmp_path, "configuration-hash")
    assert health_checks == [deploy.VERSION, "old"]
    assert cleanup == [("viking://resources/fastmcp-migration-check-" + "a" * 32, "after_rollback")]
    assert receipts[0]["state"] == "rolled_back"
    assert receipts[0]["exception_type"] == "TimeoutError"
    assert receipts[0]["smoke"]["cleanup_verified"]
    assert not deploy.DROPIN.exists()


def test_missing_runtime_qualification_cannot_touch_the_service(tmp_path, monkeypatch):
    deploy = load_script("deploy_fastmcp_candidate", monkeypatch)
    (tmp_path / "qualification.json").write_text('{"ready":true}')
    monkeypatch.setattr(deploy, "RELEASE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["deploy_fastmcp_candidate.py", "--deploy"])

    def forbidden(*args, **kwargs):
        pytest.fail("A failed runtime qualification must not access the live service")

    monkeypatch.setattr(deploy, "http", forbidden)
    monkeypatch.setattr(deploy, "run", forbidden)
    with pytest.raises(RuntimeError, match="no runtime fingerprint"):
        deploy.main()


def test_deletion_guard_refuses_stale_wheel_overlay(tmp_path, monkeypatch):
    build = load_script("build_fastmcp_candidate", monkeypatch)
    monkeypatch.setattr(build, "DEST", tmp_path / "must-not-be-created")
    monkeypatch.setattr(build, "git", lambda *args: b"openviking/removed.py\n")
    with pytest.raises(RuntimeError, match="Package deletions require an explicit wheel rebuild"):
        build.main()
    assert not build.DEST.exists()


def test_explicit_native_repair_breaks_shared_inodes_safely(
    verifier, installed_runtime, tmp_path, monkeypatch
):
    venv, site, wheel = installed_runtime
    monkeypatch.setattr(verifier, "CANDIDATE_RELEASE", tmp_path)
    monkeypatch.setattr(verifier, "REPAIRABLE_NATIVE_FILES", {"openviking/lib/test.so"})
    installed = site / "openviking/lib/test.so"
    installed.chmod(0o711)
    cached = tmp_path / "shared-cache-file"
    cached.hardlink_to(installed)
    original_digest = hashlib.sha256(cached.read_bytes()).hexdigest()
    assert installed.stat().st_nlink == 2

    receipt = verifier.restore_native_modes(tmp_path, wheel, VERSION)
    assert receipt["state"] == "verified"
    assert receipt["repairs"] == [
        {
            "path": "openviking/lib/test.so",
            "before_mode": "0o711",
            "after_mode": "0o755",
            "before_nlink": 2,
            "after_nlink": 1,
            "sha256": original_digest,
        }
    ]
    assert installed.stat().st_mode & 0o777 == 0o755
    assert cached.stat().st_mode & 0o777 == 0o711
    assert installed.stat().st_ino != cached.stat().st_ino
    assert hashlib.sha256(cached.read_bytes()).hexdigest() == original_digest
    assert json.loads((tmp_path / "native-mode-repairs.json").read_text()) == receipt
    verifier.fingerprint_runtime(venv, wheel, VERSION)


def test_native_repair_cannot_target_another_release(
    verifier, installed_runtime, tmp_path, monkeypatch
):
    _, site, wheel = installed_runtime
    monkeypatch.setattr(verifier, "CANDIDATE_RELEASE", tmp_path / "different-release")
    installed = site / "openviking/lib/test.so"
    installed.chmod(0o711)
    with pytest.raises(RuntimeError, match="fixed candidate release"):
        verifier.restore_native_modes(tmp_path, wheel, VERSION)
    assert installed.stat().st_mode & 0o777 == 0o711


def test_native_repair_refuses_modified_application_bytes(
    verifier, installed_runtime, tmp_path, monkeypatch
):
    _, site, wheel = installed_runtime
    monkeypatch.setattr(verifier, "CANDIDATE_RELEASE", tmp_path)
    monkeypatch.setattr(verifier, "REPAIRABLE_NATIVE_FILES", {"openviking/lib/test.so"})
    installed = site / "openviking/lib/test.so"
    installed.chmod(0o711)
    (site / "openviking/__init__.py").write_text("unexpected installed modification\n")
    with pytest.raises(RuntimeError, match="differs from qualified wheel"):
        verifier.restore_native_modes(tmp_path, wheel, VERSION)
    assert installed.stat().st_mode & 0o777 == 0o711
    assert not (tmp_path / "native-mode-repairs.json").exists()
