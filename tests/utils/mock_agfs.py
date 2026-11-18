import asyncio
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from openviking.pyagfs import AGFSNotFoundError


class MockTreeDirectory:
    """Flatten the native tree response shape using a test double's ls method."""

    def tree_directory(
        self,
        path,
        show_hidden=False,
        node_limit=None,
        level_limit=None,
        *,
        offset=0,
        sort_by=None,
        sort_order="asc",
        directories_only=False,
        ctx=None,
    ):
        del ctx
        entries = []

        def walk(parent, depth):
            if level_limit is not None and depth > level_limit:
                return
            children = self.ls(parent)
            if sort_by is not None:

                def key(info):
                    name = str(info["name"])
                    value = name.casefold() if sort_by == "name" else info.get("mtime", 0)
                    return value, name

                reverse = sort_order == "desc"
                children = sorted(
                    (info for info in children if info.get("isDir")), key=key, reverse=reverse
                ) + sorted(
                    (info for info in children if not info.get("isDir")), key=key, reverse=reverse
                )
            for info in children:
                if not show_hidden and info["name"].startswith("."):
                    continue
                child_path = f"{parent.rstrip('/')}/{info['name']}"
                is_dir = info.get("isDir", False)
                if not directories_only or is_dir:
                    native_info = dict(info)
                    native_info.setdefault("mode", 0o755 if is_dir else 0o644)
                    native_info.setdefault(
                        "modTime",
                        datetime.fromtimestamp(info.get("mtime", 0), timezone.utc).isoformat(),
                    )
                    entries.append(
                        {
                            "path": child_path,
                            "rel_path": child_path[len(path.rstrip("/")) + 1 :],
                            "info": native_info,
                            "depth": depth,
                        }
                    )
                if is_dir:
                    walk(child_path, depth + 1)

        walk(path, 1)
        return entries[offset:] if node_limit is None else entries[offset : offset + node_limit]


class MockLocalAGFS(MockTreeDirectory):
    """
    A mock implementation of the AGFS binding client that operates on a local
    directory. Useful for tests where running the real RAGFS binding is not
    feasible or desired.
    """

    def __init__(self, config=None, root_path=None):
        self.config = config
        self.root = Path(root_path) if root_path else Path("/tmp/viking_data")
        self.root.mkdir(parents=True, exist_ok=True)
        self._pathlocks_guard = threading.Lock()
        self._pathlocks = {}
        self._pathlock_leases = {}
        self._leaders = {}

    def _resolve(self, path):
        if str(path).startswith("viking://"):
            path = str(path).replace("viking://", "")
        if str(path).startswith("/"):
            path = str(path)[1:]
        return self.root / path

    def exists(self, path, ctx=None):
        return self._resolve(path).exists()

    def mkdir(self, path, ctx=None, parents=True, exist_ok=True):
        self._resolve(path).mkdir(parents=parents, exist_ok=exist_ok)

    def ensure_parent_dirs(self, path, ctx=None):
        self._resolve(path).parent.mkdir(parents=True, exist_ok=True)

    def ls(self, path, ctx=None, **kwargs):
        """List test entries with the same sorting and pagination contract as RagFS."""
        p = self._resolve(path)
        if not p.exists():
            return []
        res = []
        for item in p.iterdir():
            res.append(
                {
                    "name": item.name,
                    "isDir": item.is_dir(),  # Note: JS style camelCase for some APIs
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                    "mtime": item.stat().st_mtime,
                    "uri": f"viking://{path}/{item.name}".replace("//", "/"),
                }
            )
        sort_by = kwargs.get("sort_by")
        reverse = kwargs.get("sort_order", "asc") == "desc"
        if sort_by:
            key = (
                (lambda entry: (str(entry["name"]).lower(), str(entry["name"])))
                if sort_by == "name"
                else (
                    lambda entry: (
                        entry["mtime"],
                        str(entry["name"]).lower(),
                        str(entry["name"]),
                    )
                )
            )
            directories = sorted(
                (entry for entry in res if entry["isDir"]), key=key, reverse=reverse
            )
            files = sorted((entry for entry in res if not entry["isDir"]), key=key, reverse=reverse)
            res = directories + files
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit")
        return res[offset:] if limit is None else res[offset : offset + limit]

    def glob_directory(
        self,
        path,
        pattern,
        show_hidden=False,
        page_size=None,
        level_limit=None,
        continuation_token=None,
        ctx=None,
    ):
        del ctx
        root = self._resolve(path)
        matches = []
        for item in sorted(root.glob(pattern)):
            relative = item.relative_to(root)
            if not show_hidden and any(part.startswith(".") for part in relative.parts):
                continue
            if level_limit is not None and len(relative.parts) > level_limit:
                continue
            matches.append(
                {
                    "path": f"{str(path).rstrip('/')}/{relative.as_posix()}",
                    "rel_path": relative.as_posix(),
                    "name": item.name,
                    "is_dir": item.is_dir(),
                }
            )

        start = int(continuation_token or 0)
        end = len(matches) if not page_size else start + page_size
        return {
            "entries": matches[start:end],
            "next_token": str(end) if end < len(matches) else None,
        }

    def writeto(self, path, content, ctx=None, **kwargs):
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_bytes(content)
        return str(p)

    def write(self, path, content, ctx=None, **kwargs):
        return self.writeto(path, content, ctx, **kwargs)

    def write_file(self, path, content, ctx=None, **kwargs):
        return self.writeto(path, content, ctx, **kwargs)

    def read_file(self, path, ctx=None, **kwargs):
        p = self._resolve(path)
        if not p.exists():
            raise AGFSNotFoundError(path)
        return p.read_bytes()

    def read(self, path, ctx=None, **kwargs):
        return self.read_file(path, ctx, **kwargs)

    def cat(self, path, ctx=None, **kwargs):
        return self.read_file(path, ctx, **kwargs)

    def rm(self, path, recursive=False, ctx=None):
        p = self._resolve(path)
        if p.exists():
            if p.is_dir():
                if recursive:
                    shutil.rmtree(p)
                else:
                    p.rmdir()
            else:
                p.unlink()

    def delete_temp(self, path, ctx=None):
        self.rm(path, recursive=True, ctx=ctx)

    def mv(self, src, dst, ctx=None):
        s = self._resolve(src)
        d = self._resolve(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))

    def stat(self, path, ctx=None):
        p = self._resolve(path)
        if not p.exists():
            raise AGFSNotFoundError(path)
        s = p.stat()
        return {
            "size": s.st_size,
            "mtime": s.st_mtime,
            "isDir": p.is_dir(),
            "is_dir": p.is_dir(),
        }

    def bind_request_context(self, ctx):
        return MagicMock(__enter__=lambda x: None, __exit__=lambda x, y, z: None)

    async def leader_validate_async(self):
        """The fixture provides one in-process, exclusively owned lease per key."""
        return None

    async def leader_try_acquire_async(self, key, holder_id, ttl_secs):
        with self._pathlocks_guard:
            now = time.monotonic()
            current = self._leaders.get(key)
            if current is not None and current["expires"] > now:
                return None
            lease_ref = str(uuid.uuid4())
            self._leaders[key] = {
                "lease_ref": lease_ref,
                "holder_id": holder_id,
                "expires": now + ttl_secs,
            }
            return lease_ref

    async def leader_renew_async(self, lease_ref, ttl_secs):
        with self._pathlocks_guard:
            now = time.monotonic()
            for key, lease in list(self._leaders.items()):
                if lease["lease_ref"] != lease_ref:
                    continue
                if lease["expires"] <= now:
                    del self._leaders[key]
                    return False
                lease["expires"] = now + ttl_secs
                return True
            return False

    async def leader_release_async(self, lease_ref):
        with self._pathlocks_guard:
            for key, lease in list(self._leaders.items()):
                if lease["lease_ref"] == lease_ref:
                    del self._leaders[key]
                    return

    async def pathlock_acquire_batch_async(
        self,
        ctx,
        requests,
        timeout_secs=0.0,
        owner_lease_ref=None,
    ):
        del ctx, owner_lease_ref
        acquired = []
        try:
            for path in sorted({request["path"] for request in requests}):
                with self._pathlocks_guard:
                    lock = self._pathlocks.setdefault(path, threading.Lock())
                if not await asyncio.to_thread(lock.acquire, timeout=timeout_secs):
                    raise TimeoutError(f"timed out acquiring test path lock: {path}")
                acquired.append(lock)
        except BaseException:
            for lock in reversed(acquired):
                lock.release()
            raise

        lease_ref = str(uuid.uuid4())
        lease = {
            "lease_ref": lease_ref,
            "ownership_ref": str(uuid.uuid4()),
            "owner_id": "mock-local-agfs",
            "owned": True,
        }
        with self._pathlocks_guard:
            self._pathlock_leases[lease_ref] = acquired
        return lease

    def pathlock_release(self, ctx, owned_lease_ref):
        del ctx
        lease_ref = owned_lease_ref["lease_ref"]
        with self._pathlocks_guard:
            locks = self._pathlock_leases.pop(lease_ref)
        for lock in reversed(locks):
            lock.release()
