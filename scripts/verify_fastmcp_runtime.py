"""Verify the installed candidate without importing or executing its application.

The fingerprint binds a qualification to the wheel payload, dependency version
closure, console scripts and Python interpreter that were actually tested.
Only the installer's rewritten RECORD is exempt from wheel byte comparison.
"""

import argparse
import configparser
import datetime
import email.parser
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

EXPECTED_FRAMEWORKS = {"fastmcp": "4.2.1", "mcp": "2.3.0"}
CANDIDATE_VERSION = "0.5.5+ome.fastmcp42.1"
CANDIDATE_RELEASE = Path.home() / ".local/share/openviking/releases" / CANDIDATE_VERSION
REPAIRABLE_NATIVE_FILES = {
    "openviking/bin/ov",
    "openviking/lib/ragfs_python.abi3.so",
    "openviking/storage/vectordb/engine/_native.abi3.so",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stream_digest(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def file_digest(path):
    with path.open("rb") as stream:
        return stream_digest(stream)


def object_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def regular_file(root, relative):
    parts = PurePosixPath(relative)
    require(
        not parts.is_absolute() and ".." not in parts.parts and str(parts) == relative,
        "Unsafe package path",
    )
    target = root / relative
    cursor = root
    for part in parts.parts:
        cursor /= part
        require(not cursor.is_symlink(), "Installed payload contains a symlink: " + relative)
    require(target.is_file(), "Installed payload is missing: " + relative)
    require(stat.S_ISREG(target.stat().st_mode), "Installed payload is not a regular file")
    return target


def native_artifact(name):
    return re.search(r"\.so(?:\.\d+)*$", name) is not None or name.endswith(
        (".pyd", ".dylib", ".dll", ".exe", "/ov")
    )


def console_script(python, module, function):
    # uv's standard wheel console-script template, checked against the wheel's
    # entry_points.txt instead of accepting an arbitrary executable by name.
    return (
        f"#!{python}\n"
        "# -*- coding: utf-8 -*-\n"
        "import sys\n"
        f"from {module} import {function}\n"
        'if __name__ == "__main__":\n'
        '    if sys.argv[0].endswith("-script.pyw"):\n'
        "        sys.argv[0] = sys.argv[0][:-11]\n"
        '    elif sys.argv[0].endswith(".exe"):\n'
        "        sys.argv[0] = sys.argv[0][:-4]\n"
        f"    sys.exit({function}())\n"
    ).encode()


def _inspect_runtime(venv, wheel, version, *, allow_native_mode_drift=False):
    venv = Path(venv).resolve(strict=True)
    wheel = Path(wheel).resolve(strict=True)
    sites = sorted(venv.glob("lib/python*/site-packages"))
    require(len(sites) == 1, "Expected exactly one candidate site-packages directory")
    site = sites[0]
    require(not site.is_symlink(), "Candidate site-packages must not be a symlink")
    config = regular_file(venv, "pyvenv.cfg")
    settings = dict(line.split("=", 1) for line in config.read_text().splitlines() if "=" in line)
    settings = {key.strip(): value.strip() for key, value in settings.items()}
    require(
        settings.get("include-system-site-packages") == "false",
        "Candidate must not inherit system site-packages",
    )
    python = venv / "bin/python"
    interpreter = python.resolve(strict=True)
    require(interpreter.is_file(), "Candidate Python interpreter is missing")

    payload = []
    native_files = []
    with zipfile.ZipFile(wheel) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        require(len(names) == len(set(names)), "Wheel contains duplicate paths")
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        require(len(metadata_paths) == 1, "Expected exactly one wheel distribution")
        info_prefix = metadata_paths[0].removesuffix("METADATA")
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_paths[0]))
        require(metadata["Name"].lower() == "openviking", "Unexpected wheel package")
        require(metadata["Version"] == version, "Wheel version does not match qualification")
        for info in sorted(infos, key=lambda entry: entry.filename):
            name = info.filename
            require(
                ".data/" not in name, "Wheel .data relocation is not supported by this verifier"
            )
            if info.is_dir():
                relative = name.rstrip("/")
                parts = PurePosixPath(relative)
                require(
                    not parts.is_absolute() and ".." not in parts.parts,
                    "Unsafe wheel directory",
                )
                require((site / relative).is_dir(), "Installed wheel directory is missing")
                continue
            if name == info_prefix + "RECORD":
                continue
            installed = regular_file(site, name)
            mode = stat.S_IMODE(installed.stat().st_mode)
            with archive.open(info) as stream:
                expected_digest = stream_digest(stream)
            require(
                file_digest(installed) == expected_digest,
                "Installed payload differs from qualified wheel: " + name,
            )
            if native_artifact(name):
                wheel_mode = stat.S_IMODE(info.external_attr >> 16)
                require(
                    allow_native_mode_drift or mode == wheel_mode,
                    "Installed native mode differs from wheel: " + name,
                )
                native_files.append(name)
            payload.append({"path": name, "sha256": expected_digest, "mode": mode})
        entry_points = configparser.ConfigParser(interpolation=None)
        entry_points.read_string(archive.read(info_prefix + "entry_points.txt").decode())
        require(entry_points.has_section("console_scripts"), "Wheel has no console scripts")
        require(
            "openviking-server" in entry_points["console_scripts"],
            "Wheel does not define the server launcher",
        )
        launchers = {}
        for name, target in sorted(entry_points["console_scripts"].items()):
            require(re.fullmatch(r"[A-Za-z0-9_-]+", name), "Unsafe console-script name")
            match = re.fullmatch(r"([A-Za-z_][\w.]*)\s*:\s*([A-Za-z_]\w*)", target)
            require(match is not None, "Unsupported console-script entry point")
            launcher = regular_file(venv, "bin/" + name)
            require(
                launcher.read_bytes() == console_script(python, *match.groups()),
                "Installed launcher does not match wheel entry point: " + name,
            )
            mode = stat.S_IMODE(launcher.stat().st_mode)
            require(mode & 0o111 != 0, "Installed launcher is not executable: " + name)
            launchers[name] = {"sha256": file_digest(launcher), "mode": mode}

    closure = {}
    for directory in sorted(site.glob("*.dist-info")):
        metadata_path = regular_file(site, str(directory.relative_to(site) / "METADATA"))
        metadata = email.parser.BytesParser().parsebytes(metadata_path.read_bytes())
        name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
        installed_version = metadata.get("Version")
        require(bool(name and installed_version), "Installed dependency metadata is incomplete")
        require(name not in closure, "Duplicate installed distribution: " + name)
        item = {"version": installed_version, "metadata_sha256": file_digest(metadata_path)}
        for filename in ("RECORD", "entry_points.txt", "direct_url.json"):
            path = directory / filename
            if path.exists():
                regular_file(site, str(path.relative_to(site)))
                item[filename + "_sha256"] = file_digest(path)
        closure[name] = item
    versions = {name: item["version"] for name, item in closure.items()}
    for name, expected in (EXPECTED_FRAMEWORKS | {"openviking": version}).items():
        require(versions.get(name) == expected, "Unexpected installed version of " + name)
    require(not list(site.glob("*.egg-info")), "Legacy egg distributions are not qualified")
    import_hooks = {}
    for path in sorted(
        set(site.glob("*.pth")) | {site / "sitecustomize.py", site / "usercustomize.py"}
    ):
        if path.exists():
            regular_file(site, str(path.relative_to(site)))
            import_hooks[path.name] = file_digest(path)
    python_version = settings.get("version_info", settings.get("version", ""))
    match = re.match(r"(\d+)\.(\d+)", python_version)
    require(
        match is not None and tuple(map(int, match.groups())) >= (3, 11),
        "Candidate Python must be at least 3.11",
    )
    fingerprint = {
        "schema_version": 1,
        "version": version,
        "wheel_name": wheel.name,
        "wheel_sha256": file_digest(wheel),
        "venv": str(venv),
        "site_packages": str(site.relative_to(venv)),
        "payload_sha256": object_digest(payload),
        "payload_files": len(payload),
        "native_artifacts": native_files,
        "dependency_closure_sha256": object_digest(closure),
        "dependency_versions": versions,
        "launchers": launchers,
        "python": {
            "resolved_path": str(interpreter),
            "sha256": file_digest(interpreter),
            "mode": stat.S_IMODE(interpreter.stat().st_mode),
            "link_target": str(python.readlink()) if python.is_symlink() else None,
            "version": python_version,
        },
        "venv_configuration_sha256": file_digest(config),
        "import_hooks": import_hooks,
    }
    fingerprint["fingerprint_sha256"] = object_digest(fingerprint)
    return fingerprint


def fingerprint_runtime(venv, wheel, version):
    return _inspect_runtime(venv, wheel, version)


def restore_native_modes(release, wheel, version):
    """Repair only this candidate's verified native files, never shared inodes."""
    release = Path(release).absolute()
    wheel = Path(wheel).absolute()
    require(version == CANDIDATE_VERSION, "Native repair is limited to the candidate version")
    require(
        release == CANDIDATE_RELEASE.absolute()
        and not release.is_symlink()
        and release.resolve(strict=True) == CANDIDATE_RELEASE.absolute(),
        "Native repair is limited to the fixed candidate release",
    )
    venv = release / "venv"
    require(not venv.is_symlink(), "Native repair refuses a symlinked environment")
    require(
        wheel == release / f"openviking-{version}-cp310-abi3-linux_aarch64.whl"
        and not wheel.is_symlink(),
        "Native repair requires the exact candidate wheel path",
    )
    # Validate every payload byte, entry point and dependency before any change.
    # Mode drift is the sole exception, and this result is never exported as a
    # qualified fingerprint. The ordinary verifier remains strict.
    inspected = _inspect_runtime(venv, wheel, version, allow_native_mode_drift=True)
    require(
        set(inspected["native_artifacts"]) == REPAIRABLE_NATIVE_FILES,
        "Candidate native artifact set differs from the explicit repair allowlist",
    )
    site = venv / inspected["site_packages"]
    receipt = {
        "version": version,
        "wheel_sha256": inspected["wheel_sha256"],
        "state": "started",
        "repairs": [],
    }
    primary_error = None
    try:
        with zipfile.ZipFile(wheel) as archive:
            for name in sorted(REPAIRABLE_NATIVE_FILES):
                info = archive.getinfo(name)
                expected_mode = stat.S_IMODE(info.external_attr >> 16)
                require(expected_mode in (0o700, 0o755), "Unexpected native repair permissions")
                installed = regular_file(site, name)
                original = installed.stat()
                before_mode = stat.S_IMODE(original.st_mode)
                if before_mode == expected_mode:
                    continue
                temporary = installed.with_name(installed.name + "." + uuid.uuid4().hex + ".tmp")
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(fd, "wb") as output, archive.open(info) as source:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                        output.flush()
                        os.fchmod(output.fileno(), expected_mode)
                        os.fsync(output.fileno())
                    with archive.open(info) as source:
                        expected_sha256 = stream_digest(source)
                    require(
                        file_digest(temporary) == expected_sha256
                        and file_digest(installed) == expected_sha256,
                        "Native bytes changed during repair",
                    )
                    current = installed.stat()
                    require(
                        (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino),
                        "Native path changed concurrently during repair",
                    )
                    temporary.replace(installed)
                    after = installed.stat()
                    receipt["repairs"].append(
                        {
                            "path": name,
                            "before_mode": oct(before_mode),
                            "after_mode": oct(stat.S_IMODE(after.st_mode)),
                            "before_nlink": original.st_nlink,
                            "after_nlink": after.st_nlink,
                            "sha256": expected_sha256,
                        }
                    )
                    directory = os.open(installed.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    temporary.unlink(missing_ok=True)
        fingerprint_runtime(venv, wheel, version)
        receipt["state"] = "verified"
    except BaseException as exc:
        primary_error = exc
        receipt["state"] = "failed"
        receipt["exception_type"] = type(exc).__name__
        raise
    finally:
        receipt["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        destination = release / "native-mode-repairs.json"
        temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as output:
                    json.dump(receipt, output, indent=2)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        except BaseException as receipt_error:
            receipt["receipt_write_exception_type"] = type(receipt_error).__name__
            print(json.dumps(receipt), flush=True)
            if primary_error is None:
                raise RuntimeError("Could not persist native-mode repair receipt") from None
    return receipt


def verify_qualified_runtime(qualification, venv, wheel, version):
    expected = qualification.get("runtime_fingerprint")
    require(isinstance(expected, dict), "Qualification has no runtime fingerprint")
    expected_body = {key: value for key, value in expected.items() if key != "fingerprint_sha256"}
    require(
        expected.get("fingerprint_sha256") == object_digest(expected_body),
        "Qualification runtime fingerprint is inconsistent",
    )
    current = fingerprint_runtime(venv, wheel, version)
    require(current == expected, "Installed runtime has changed since qualification")
    require(
        qualification.get("wheel_sha256") == current["wheel_sha256"],
        "Qualification wheel hash does not match installed runtime",
    )
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restore-native-modes", action="store_true")
    args = parser.parse_args()
    wheel = args.release / f"openviking-{args.version}-cp310-abi3-linux_aarch64.whl"
    if args.restore_native_modes:
        restore_native_modes(args.release, wheel, args.version)
    fingerprint = fingerprint_runtime(args.release / "venv", wheel, args.version)
    args.output.write_text(json.dumps(fingerprint, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "fingerprint_sha256": fingerprint["fingerprint_sha256"],
                "payload_files": fingerprint["payload_files"],
                "dependencies": len(fingerprint["dependency_versions"]),
                "native_artifacts": len(fingerprint["native_artifacts"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
