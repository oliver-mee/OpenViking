"""Check that service test doubles preserve the native tree and lease contracts."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.utils import mock_agfs
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.mark.asyncio
async def test_leadership_has_one_owner_and_rejects_unknown_leases(tmp_path):
    client = MockLocalAGFS(root_path=tmp_path)
    holders = [f"worker-{index}" for index in range(12)]
    leases = await asyncio.gather(
        *(client.leader_try_acquire_async("watch", holder, 60) for holder in holders)
    )
    winners = [
        (holder, lease) for holder, lease in zip(holders, leases, strict=True) if lease is not None
    ]
    assert len(winners) == 1
    holder, lease = winners[0]
    assert client._leaders["watch"]["holder_id"] == holder

    assert await client.leader_renew_async("unowned", 60) is False
    await client.leader_release_async("unowned")
    assert await client.leader_try_acquire_async("watch", "competitor", 60) is None
    assert await client.leader_try_acquire_async("different-key", "competitor", 60) is not None

    assert await client.leader_renew_async(lease, 60) is True
    await client.leader_release_async(lease)
    successor = await client.leader_try_acquire_async("watch", "successor", 60)
    assert successor is not None and successor != lease
    assert client._leaders["watch"]["holder_id"] == "successor"


@pytest.mark.asyncio
async def test_expired_leases_cannot_renew_or_release_a_successor(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(mock_agfs, "time", SimpleNamespace(monotonic=lambda: now[0]))
    client = MockLocalAGFS(root_path=tmp_path)
    expired = await client.leader_try_acquire_async("watch", "original", 5)

    now[0] = 105.0
    successor = await client.leader_try_acquire_async("watch", "successor", 5)
    assert successor is not None and successor != expired
    assert await client.leader_renew_async(expired, 60) is False
    await client.leader_release_async(expired)
    assert await client.leader_try_acquire_async("watch", "competitor", 5) is None

    now[0] = 109.0
    assert await client.leader_renew_async(successor, 10) is True
    now[0] = 111.0
    assert await client.leader_try_acquire_async("watch", "competitor", 5) is None
    now[0] = 119.0
    assert await client.leader_renew_async(successor, 10) is False
    assert await client.leader_try_acquire_async("watch", "competitor", 5) is not None


def test_tree_preserves_paths_hidden_filter_depth_and_pagination(tmp_path):
    client = MockLocalAGFS(root_path=tmp_path)
    client.write("/root/nested/a.txt", "nested")
    client.write("/root/z.txt", "visible")
    client.write("/root/.hidden/secret.txt", "hidden")
    client.write("/root/.hidden.txt", "hidden file")

    entries = client.tree_directory("/root", sort_by="name")
    assert [entry["path"] for entry in entries] == [
        "/root/nested",
        "/root/nested/a.txt",
        "/root/z.txt",
    ]
    assert [entry["depth"] for entry in entries] == [1, 2, 1]
    assert entries[0]["info"]["isDir"] is True
    assert entries[1]["info"]["isDir"] is False
    assert entries[1]["info"]["name"] == "a.txt"
    assert entries[1]["rel_path"] == "nested/a.txt"
    assert entries[0]["info"]["mode"] == 0o755
    assert entries[1]["info"]["mode"] == 0o644
    assert entries[1]["info"]["modTime"]
    assert client.tree_directory("/root", sort_by="name", offset=1, node_limit=1) == entries[1:2]
    assert [
        entry["path"] for entry in client.tree_directory("/root", sort_by="name", level_limit=1)
    ] == ["/root/nested", "/root/z.txt"]
    assert [
        entry["path"]
        for entry in client.tree_directory("/root", sort_by="name", directories_only=True)
    ] == ["/root/nested"]

    hidden = client.tree_directory("/root", sort_by="name", show_hidden=True)
    assert {entry["path"] for entry in hidden} == {
        "/root/.hidden",
        "/root/.hidden/secret.txt",
        "/root/.hidden.txt",
        "/root/nested",
        "/root/nested/a.txt",
        "/root/z.txt",
    }
