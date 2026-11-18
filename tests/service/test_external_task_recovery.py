"""Recovery must distinguish submitted target owners from claim-only waiters."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_tracker import TaskRecord, TaskStatus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "stage", "external_task_id", "reserved"),
    [
        (TaskStatus.RUNNING, None, None, False),
        (TaskStatus.RUNNING, "queued", None, False),
        (TaskStatus.CANCELLING, None, None, False),
        (TaskStatus.CANCELLING, "queued", None, False),
        (TaskStatus.RUNNING, "submitting", None, True),
        (TaskStatus.CANCELLING, "submitting", None, True),
        (TaskStatus.RUNNING, None, "remote-1", True),
        (TaskStatus.RUNNING, "queued", "remote-1", True),
        (TaskStatus.CANCELLING, None, "remote-1", True),
        (TaskStatus.CANCELLING, "queued", "remote-1", True),
        (TaskStatus.RUNNING, "polling", "remote-1", True),
        (TaskStatus.CANCELLING, "polling", "remote-1", True),
        (TaskStatus.CANCELLED, "queued", None, True),
        (TaskStatus.CANCELLED, "submitting", None, True),
        (TaskStatus.CANCELLED, "queued", "remote-1", True),
        (TaskStatus.PENDING, "queued", None, False),
        (TaskStatus.COMPLETED, "completed", "remote-1", False),
        (TaskStatus.FAILED, "failed", "remote-1", False),
    ],
)
async def test_restore_reserves_only_submitted_or_ambiguous_target_work(
    monkeypatch, status, stage, external_task_id, reserved
):
    service = ExternalTaskService()
    service.register(
        SimpleNamespace(task_type="compile", serialization_key=lambda payload: payload["to"])
    )
    task = TaskRecord(
        task_id="compile-task",
        task_type="compile",
        status=status,
        account_id="account",
        user_id="alice",
        stage=stage,
        meta={"request": {"to": "viking://resources/wiki"}},
    )
    auth = {"external_task_id": external_task_id} if external_task_id else {}
    monkeypatch.setattr(service, "_task_auth", AsyncMock(return_value=auth))

    await service.restore_tasks([task])

    key = ("account", "compile", "viking://resources/wiki")
    assert service._owners.get(key, set()) == ({"compile-task"} if reserved else set())


@pytest.mark.asyncio
async def test_restore_does_not_grant_a_waiter_the_submitted_owners_target(monkeypatch):
    service = ExternalTaskService()
    service.register(
        SimpleNamespace(task_type="compile", serialization_key=lambda payload: payload["to"])
    )
    submitted = TaskRecord(
        task_id="submitted",
        task_type="compile",
        status=TaskStatus.RUNNING,
        account_id="account",
        user_id="alice",
        stage="queued",  # The remote runtime can also report its own queueing stage.
        meta={"request": {"to": "viking://resources/wiki"}},
    )
    waiter = TaskRecord(
        task_id="waiting",
        task_type="compile",
        status=TaskStatus.RUNNING,
        account_id="account",
        user_id="bob",
        stage="queued",
        meta={"request": {"to": "viking://resources/wiki"}},
    )

    async def auth(task_id, account_id, user_id):
        assert account_id == "account"
        assert user_id == ("alice" if task_id == "submitted" else "bob")
        return {"external_task_id": "remote-submitted"} if task_id == "submitted" else {}

    monkeypatch.setattr(service, "_task_auth", auth)

    # Recovery order must not let the queued waiter claim the existing owner's target.
    await service.restore_tasks([waiter, submitted])

    key = ("account", "compile", "viking://resources/wiki")
    assert service._owners[key] == {"submitted"}
