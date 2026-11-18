"""Promote the qualified local MCP runtime, with private OAuth backup and rollback.

Only changes one systemd drop-in. Existing data, configuration, credentials,
OAuth issuer and public routing remain owned by the original deployment.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from verify_fastmcp_runtime import require, verify_qualified_runtime

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.5.5+ome.fastmcp42.1"
RELEASE = Path.home() / ".local/share/openviking/releases" / VERSION
UNIT = "openviking.service"
DROPIN = Path.home() / ".config/systemd/user/openviking.service.d/zz-fastmcp-runtime.conf"
CONF = Path.home() / ".openviking/ov.conf"
BASE = "http://127.0.0.1:1933"
HEADERS = {}


def load_headers():
    client = json.loads((Path.home() / ".openviking/ovcli.conf").read_text())
    headers = {"x-api-key": client["api_key"]}
    for key in ("account", "user"):
        if client.get(key):
            headers["x-openviking-" + key] = str(client[key])
    return headers


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def http(path, payload=None, authenticated=True, timeout=90):
    headers = dict(HEADERS) if authenticated else {}
    if payload is not None:
        headers["content-type"] = "application/json"
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def rpc(method, params=None):
    headers = HEADERS | {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": "2025-11-25",
    }
    identifier = uuid.uuid4().hex
    request = urllib.request.Request(
        BASE + "/mcp",
        data=json.dumps(
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}}
        ).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode()
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            messages = [
                json.loads(line[5:].strip())
                for line in raw.splitlines()
                if line.startswith("data:")
            ]
            data = next(item for item in messages if item.get("id") == identifier)
        else:
            data = json.loads(raw)
    if "error" in data:
        raise RuntimeError("MCP protocol error: " + str(data["error"].get("code")))
    return data["result"]


def call(name, arguments=None):
    result = rpc("tools/call", {"name": name, "arguments": arguments or {}})
    if result.get("isError"):
        # Tool output may contain user information; never put it in deployment logs.
        raise RuntimeError("MCP acceptance failed: " + name)
    return result


def wait_for_health(version, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            health = http("/health", authenticated=False, timeout=5)
            if health.get("healthy") and health.get("version") == version:
                return health
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            pass
        time.sleep(2)
    raise RuntimeError("Expected server version did not become healthy")


def atomic_write(path, data, mode=0o644, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            # A concurrent override must never be silently replaced.
            os.link(temporary, path)
            temporary.unlink()
        else:
            temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def candidate_path():
    pid = int(run("systemctl", "--user", "show", UNIT, "--property=MainPID", "--value").strip())
    require(pid > 0, "The original service has no running process")
    # Read only PATH from the process environment; never log the environment.
    environment = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
    paths = [item[5:].decode() for item in environment if item.startswith(b"PATH=")]
    require(len(paths) == 1, "The original process has no unambiguous PATH")
    original = str(Path.home() / ".local/share/uv/tools/openviking/bin")
    replacement = str(RELEASE / "venv/bin")
    parts = paths[0].split(":")
    require(parts.count(original) == 1, "Expected original environment is missing from PATH")
    require(replacement not in parts, "Candidate is already present in the original PATH")
    parts[parts.index(original)] = replacement
    result = ":".join(parts)
    require("\n" not in result and "\r" not in result, "Unsupported newline in service PATH")
    return result


def dropin_bytes(path):
    escaped_path = path.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return (
        "# Qualified OpenViking FastMCP runtime; remove this drop-in to roll back.\n"
        "[Service]\nExecStart=\n"
        f"ExecStart=%h/.local/share/openviking/releases/{VERSION}/venv/bin/openviking-server --with-bot\n"
        f'Environment="PATH={escaped_path}"\n'
    ).encode()


def cleanup_fixture(state, stage):
    fixture = state.get("fixture")
    if fixture is None:
        return
    require(
        re.fullmatch(r"viking://resources/fastmcp-migration-check-[0-9a-f]{32}", fixture),
        "Refusing cleanup outside this run's synthetic resource",
    )
    attempt = {"stage": stage, "state": "started"}
    state.setdefault("cleanup_attempts", []).append(attempt)
    try:
        try:
            call("forget", {"uri": fixture, "recursive": True})
        except Exception as exc:
            # A failed response can follow a successful delete, or the initial
            # write can have failed before creation. Confirm absence separately.
            attempt["delete_exception_type"] = type(exc).__name__
        try:
            http("/api/v1/fs/stat?" + urllib.parse.urlencode({"uri": fixture}), timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        else:
            raise RuntimeError("Synthetic fixture still exists after cleanup")
        attempt["state"] = "absent"
        state["cleanup_verified"] = True
    except BaseException as exc:
        attempt["state"] = "failed"
        attempt["exception_type"] = type(exc).__name__
        state["cleanup_verified"] = False
        raise


def smoke(config_hash, state):
    state["stage"] = "catalog"
    tools = rpc("tools/list")["tools"]
    baseline = json.loads(
        (ROOT / "test_scripts/fastmcp-migration/production-tools-before.json").read_text()
    )
    expected = {tool["name"] for tool in baseline["tools"]} | {"list_resources", "read_resource"}
    require(
        {tool["name"] for tool in tools} == expected and len(tools) == len(expected),
        "Candidate tool catalog differs from the qualified catalog",
    )
    call("health")
    state["stage"] = "guides"
    guides = rpc("resources/list")["resources"]
    guide_uris = [item["uri"] for item in guides if item["uri"].endswith("/SKILL.md")]
    require(len(guide_uris) == 5, "Candidate skill guide catalog is incomplete")
    call("list_resources")
    for uri in guide_uris:
        require(
            "description:" in json.dumps(call("read_resource", {"uri": uri})),
            "Candidate guide could not be read",
        )
    state["stage"] = "existing_knowledge_read"
    existing_uri = "viking://resources/oliversbrain/Knowledge/Guides/mcp-server-design.md"
    existing = call("read", {"uri": existing_uri})
    require(
        "Designing reliable MCP servers" in json.dumps(existing),
        "Existing knowledge read failed acceptance",
    )

    # Register the cleanup target BEFORE attempting a write. A timeout does not
    # prove the server failed to persist the resource.
    fixture = "viking://resources/fastmcp-migration-check-" + uuid.uuid4().hex
    state["fixture"] = fixture
    uri = fixture + "/probe.md"
    marker = "OpenViking FastMCP migration acceptance " + uuid.uuid4().hex
    primary_error = None
    try:
        state["stage"] = "write"
        call("write", {"uri": uri, "content": marker + "\nalpha\n", "wait": True})
        state["stage"] = "readback"
        require(marker in json.dumps(call("read", {"uri": uri})), "Fixture readback failed")
        state["stage"] = "edit"
        call("edit", {"uri": uri, "old_string": "alpha", "new_string": "beta", "wait": True})
        require("beta" in json.dumps(call("read", {"uri": uri})), "Fixture edit readback failed")
        state["stage"] = "semantic_retrieval"
        retrieved = call("find", {"query": marker, "target_uri": fixture, "limit": 3})
        require(uri in json.dumps(retrieved), "Fixture URI missing after awaited indexing")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            cleanup_fixture(state, "after_smoke")
        except BaseException:
            if primary_error is None:
                state["stage"] = "fixture_cleanup"
                raise
            # The cleanup attempt is recorded, but must not hide the original
            # write/read/indexing error. The rollback path retries after restart.
    state["stage"] = "configuration_unchanged"
    require(digest(CONF) == config_hash, "Configuration changed during acceptance")
    return {
        "tools": len(tools),
        "skills": len(guide_uris),
        "checks": [
            "catalog",
            "guides",
            "existing_knowledge_read",
            "write",
            "readback",
            "edit",
            "semantic_retrieval",
            "fixture_cleanup",
            "configuration_unchanged",
        ],
        "fixture_cleanup": state["cleanup_attempts"],
    }


def write_receipt(receipt):
    try:
        atomic_write(
            RELEASE / "production-receipt.json",
            (json.dumps(receipt, indent=2) + "\n").encode(),
            mode=0o600,
        )
    except Exception as exc:
        receipt["receipt_write_exception_type"] = type(exc).__name__
        print(json.dumps(receipt), flush=True)
        return False
    print(json.dumps(receipt), flush=True)
    return True


def rollback_runtime(data, version, result):
    result["stage"] = "remove_override"
    if DROPIN.exists():
        require(
            digest(DROPIN) == hashlib.sha256(data).hexdigest(),
            "Deployment override changed concurrently; refusing to remove it",
        )
        DROPIN.unlink()
    result["stage"] = "daemon_reload"
    run("systemctl", "--user", "daemon-reload")
    result["stage"] = "restart_original"
    run("systemctl", "--user", "restart", UNIT)
    result["stage"] = "original_health"
    wait_for_health(version)
    result["state"] = "restored"
    result["stage"] = "complete"


def promote(data, before, qualification, fingerprint, backup, config_hash):
    state = {"stage": "install_override", "fixture": None, "cleanup_attempts": []}
    mutated = False
    try:
        atomic_write(DROPIN, data, exclusive=True)
        mutated = True
        state["stage"] = "daemon_reload"
        run("systemctl", "--user", "daemon-reload")
        state["stage"] = "restart_candidate"
        run("systemctl", "--user", "restart", UNIT)
        print(json.dumps({"stage": "restarted", "target_version": VERSION}), flush=True)
        state["stage"] = "candidate_health"
        health = wait_for_health(VERSION)
        checks = smoke(config_hash, state)
        state["stage"] = "write_receipt"
        receipt = {
            "state": "verified",
            "version": VERSION,
            "before": before["version"],
            "source_commit": qualification["source_commit"],
            "wheel_sha256": qualification["wheel_sha256"],
            "runtime_fingerprint_sha256": fingerprint["fingerprint_sha256"],
            "backup": str(backup),
            "health": health,
            "acceptance": checks,
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        require(write_receipt(receipt), "Could not persist deployment acceptance receipt")
        return receipt
    except BaseException as exc:
        failure = {
            "state": "failed",
            "stage": state["stage"],
            "exception_type": type(exc).__name__,
            "before": before["version"],
            "version": VERSION,
            "backup": str(backup),
            "smoke": state,
            "rollback": {"state": "not_started"},
        }
        # An atomic install can finish before a later fsync raises. Detect our
        # exact bytes even when the successful-return flag was not reached.
        needs_rollback = mutated
        try:
            if not needs_rollback:
                failure["rollback"]["stage"] = "inspect_override"
                needs_rollback = (
                    DROPIN.exists() and digest(DROPIN) == hashlib.sha256(data).hexdigest()
                )
            if needs_rollback:
                rollback_runtime(data, before["version"], failure["rollback"])
        except BaseException as rollback_error:
            failure["state"] = "rollback_failed"
            failure["rollback"]["state"] = "failed"
            failure["rollback"]["exception_type"] = type(rollback_error).__name__
            write_receipt(failure)
            raise RuntimeError(
                "Candidate deployment failed and automatic rollback failed; inspect the receipt"
            ) from None
        if not needs_rollback:
            failure["state"] = "not_changed"
            write_receipt(failure)
            raise RuntimeError("Deployment failed before any service change") from None
        failure["state"] = "rolled_back"
        if state["fixture"] is not None:
            # The candidate process is now stopped, so a timed-out original
            # write cannot later recreate the resource after this retry.
            try:
                cleanup_fixture(state, "after_rollback")
            except BaseException:
                failure["state"] = "rolled_back_cleanup_failed"
        write_receipt(failure)
        if failure["state"] == "rolled_back_cleanup_failed":
            raise RuntimeError(
                "Candidate acceptance failed; original runtime restored, fixture cleanup unverified"
            ) from None
        raise RuntimeError("Candidate acceptance failed; original runtime restored") from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy", action="store_true", required=True)
    parser.parse_args()
    lock = open(RELEASE / "deployment.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    qualification = json.loads((RELEASE / "qualification.json").read_text())
    require(qualification.get("ready") is True, "Candidate has not been qualified")
    wheel = RELEASE / f"openviking-{VERSION}-cp310-abi3-linux_aarch64.whl"
    fingerprint = verify_qualified_runtime(qualification, RELEASE / "venv", wheel, VERSION)
    manifest = json.loads((RELEASE / "build-manifest.json").read_text())
    source_head = run("git", "-C", str(ROOT), "rev-parse", "HEAD").strip()
    require(
        qualification.get("source_commit") == source_head == manifest.get("source_head"),
        "Source commit, wheel build and qualification do not match",
    )
    require(
        manifest.get("wheel_sha256") == fingerprint["wheel_sha256"]
        and manifest.get("version") == VERSION,
        "Build manifest does not identify the qualified wheel",
    )
    global HEADERS
    HEADERS = load_headers()
    before = http("/health", authenticated=False)
    require(
        before.get("healthy") and before.get("version") == "0.5.5+ome.1",
        "Unexpected live runtime; reconcile before deploying",
    )
    require(
        not DROPIN.exists(), "A deployment override already exists; inspect before replacing it"
    )
    for status in ("pending", "running", "cancelling"):
        response = http(
            "/api/v1/tasks?"
            + urllib.parse.urlencode({"status": status, "include_internal": "true", "limit": 200})
        )
        require(
            response.get("status") == "ok" and isinstance(response.get("result"), list),
            "Active-task endpoint returned an unexpected response",
        )
        require(not response["result"], "Active user tasks remain; no deployment was performed")
    data = dropin_bytes(candidate_path())
    config_bytes = CONF.read_bytes()
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    backup = RELEASE / (
        "rollback-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    backup.mkdir(mode=0o700)
    config = json.loads(config_bytes)
    workspace = Path(config["storage"]["workspace"]).expanduser()
    if not workspace.is_absolute():
        workspace = CONF.parent / workspace
    oauth_path = workspace / config.get("oauth", {}).get("db_filename", "oauth.db")
    if config.get("oauth", {}).get("enabled"):
        require(oauth_path.is_file(), "Configured OAuth grant store is missing")
        destination = backup / "oauth.db"
        destination.touch(mode=0o600)
        with (
            sqlite3.connect(oauth_path.as_uri() + "?mode=ro", uri=True) as source,
            sqlite3.connect(destination) as target,
        ):
            source.backup(target)
    # Recovery copies stay private. Rollback changes only the runtime and never
    # overwrites current grants/configuration with an older snapshot.
    atomic_write(backup / "ov.conf", config_bytes, mode=0o600)
    unit_path = Path.home() / ".config/systemd/user/openviking.service"
    shutil.copy2(unit_path, backup / "openviking.service")
    metadata = {
        "before": before["version"],
        "configuration_sha256": config_hash,
        "override": str(DROPIN),
        "runtime_fingerprint_sha256": fingerprint["fingerprint_sha256"],
    }
    atomic_write(
        backup / "metadata.json", (json.dumps(metadata, indent=2) + "\n").encode(), mode=0o600
    )
    promote(data, before, qualification, fingerprint, backup, config_hash)


if __name__ == "__main__":
    main()
