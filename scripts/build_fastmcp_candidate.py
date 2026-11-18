"""Build a Python-only candidate from the exact deployed native wheel.

Keep the native libraries, bundled CLI, asset bytes and ZIP executable modes.
Regenerate package metadata and RECORD. Refuse a source/wheel baseline mismatch.
This is an operator release script; it never changes the running environment.
"""

import base64
import copy
import csv
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "5fab0ce701880e7e7053b307ca788fd36329ee24"
OLD_VERSION = "0.5.5+ome.1"
NEW_VERSION = "0.5.5+ome.fastmcp42.1"
RELEASES = Path.home() / ".local/share/openviking/releases"
SOURCE = RELEASES / OLD_VERSION / f"openviking-{OLD_VERSION}-cp310-abi3-linux_aarch64.whl"
DEST = RELEASES / NEW_VERSION
WHEEL = DEST / f"openviking-{NEW_VERSION}-cp310-abi3-linux_aarch64.whl"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    # Overlaying cannot remove a baseline wheel member. Refuse deletions (also
    # the deleted half of a rename) rather than shipping stale importable code.
    deleted = (
        git(
            "diff",
            "--name-only",
            "--diff-filter=D",
            "--no-renames",
            BASE_COMMIT,
            "--",
            "openviking/",
            "openviking_cli/",
            "openviking_gateway/",
            "bot/vikingbot/",
        )
        .decode()
        .splitlines()
    )
    if deleted:
        raise RuntimeError(
            "Package deletions require an explicit wheel rebuild: " + ", ".join(deleted)
        )
    DEST.mkdir(parents=True, exist_ok=True)
    changed = set(git("diff", "--name-only", BASE_COMMIT).decode().splitlines())
    changed.update(git("ls-files", "--others", "--exclude-standard").decode().splitlines())
    package_files = sorted(
        name
        for name in changed
        if name.startswith(
            ("openviking/", "openviking_cli/", "openviking_gateway/", "bot/vikingbot/")
        )
        and (ROOT / name).is_file()
        and (name.endswith(".py") or "/mcp_skills/" in name)
    )
    old_info = f"openviking-{OLD_VERSION}.dist-info/"
    new_info = f"openviking-{NEW_VERSION}.dist-info/"
    files = {}
    infos = {}
    overlays = {}
    with zipfile.ZipFile(SOURCE) as source:
        for original_info in source.infolist():
            name = original_info.filename
            if name.endswith("/RECORD"):
                continue
            data = source.read(name)
            newname = name.replace(old_info, new_info)
            info = copy.copy(original_info)
            info.filename = newname
            if name == old_info + "METADATA":
                metadata = data.decode()
                metadata = metadata.replace("Version: " + OLD_VERSION, "Version: " + NEW_VERSION)
                metadata = re.sub(
                    r"^Requires-Python: .*$", "Requires-Python: >=3.11", metadata, flags=re.M
                )
                metadata = re.sub(
                    r"^Classifier: Programming Language :: Python :: 3\.10\n",
                    "",
                    metadata,
                    flags=re.M,
                )
                metadata = re.sub(
                    r"^Requires-Dist: mcp[^\n]*$",
                    lambda match: (
                        "Requires-Dist: mcp>=2.3,<3"
                        + (" ;" + match[0].split(";", 1)[1] if ";" in match[0] else "")
                    ),
                    metadata,
                    flags=re.M,
                )
                metadata = metadata.replace("\n\n", "\nRequires-Dist: fastmcp==4.2.1\n\n", 1)
                data = metadata.encode()
            if name == "openviking/_version.py":
                data = data.replace(OLD_VERSION.encode(), NEW_VERSION.encode())
                data = data.replace(b"'ome.1'", b"'ome.fastmcp42.1'")
            files[newname] = data
            infos[newname] = info
        for rel in package_files:
            wheel_path = rel.removeprefix("bot/") if rel.startswith("bot/") else rel
            if wheel_path in source.namelist():
                baseline = git("show", BASE_COMMIT + ":" + rel)
                if baseline != source.read(wheel_path):
                    raise RuntimeError(f"Source/wheel baseline mismatch: {rel}")
            data = (ROOT / rel).read_bytes()
            files[wheel_path] = data
            overlays[wheel_path] = digest(data)
            if wheel_path not in infos:
                info = zipfile.ZipInfo(wheel_path)
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                infos[wheel_path] = info
    rows = []
    for name, data in sorted(files.items()):
        sha = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        rows.append([name, "sha256=" + sha, str(len(data))])
    record_name = new_info + "RECORD"
    rows.append([record_name, "", ""])
    record = io.StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    files[record_name] = record.getvalue().encode()
    record_info = zipfile.ZipInfo(record_name)
    record_info.external_attr = 0o100644 << 16
    record_info.compress_type = zipfile.ZIP_DEFLATED
    infos[record_name] = record_info
    temp = WHEEL.with_suffix(".whl.tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as output:
        for name in sorted(files):
            output.writestr(infos[name], files[name])
    temp.replace(WHEEL)
    with zipfile.ZipFile(SOURCE) as source, zipfile.ZipFile(WHEEL) as candidate:
        natives = [
            name for name in source.namelist() if name.endswith((".so", ".pyd", "/ov", ".exe"))
        ]
        for name in natives:
            assert source.read(name) == candidate.read(name), name
            assert source.getinfo(name).external_attr == candidate.getinfo(name).external_attr, name
    manifest = {
        "version": NEW_VERSION,
        "base_commit": BASE_COMMIT,
        "source_head": git("rev-parse", "HEAD").decode().strip(),
        "base_wheel_sha256": digest(SOURCE.read_bytes()),
        "wheel_sha256": digest(WHEEL.read_bytes()),
        "fastmcp": "4.2.1",
        "mcp": "2.3.0",
        "overlays": overlays,
        "native_artifacts_unchanged": natives,
    }
    (DEST / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "wheel": str(WHEEL),
                "overlays": len(overlays),
                "native_artifacts_unchanged": len(natives),
                "sha256": manifest["wheel_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()
