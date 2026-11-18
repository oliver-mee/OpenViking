# Baseline fixtures and recovery audit for the FastMCP migration

This audit separates three findings: the original six temporary-scope failures
and 52 session setup errors were stale fixtures; the compile tests also had
outdated Task-versus-Work status assertions; and the corrected assertions
exposed a real, pre-existing external-task recovery bug. The recovery bug was
fixed in production source. Its complete focused validation is **108 passing
cases** on the final recovery-fix wheel. Historical runs and their exact
candidate identities are preserved below.

## Evidence before changing assertions or fixtures

The first combined candidate run of nine server test files reported **322 passed,
6 failed, 52 errors**. Every MCP, identity authentication and content-write test in
that run passed. The six failed cases were in `test_temp_scope_acl.py`. The 52
errors were session-test setup failures from the same missing native leadership
method.

To distinguish a migration regression from a stale test fixture, we created a
detached worktree at **5fab0ce70** and repeated all temporary-scope tests plus
`test_api_sessions.py::test_create_session` using the original production Python
and installed packages. The interpreter confirmed OpenViking **0.5.5+ome.1**,
MCP **1.30.0**, and FastAPI **0.163.2**, with OpenViking and MCP imported from the
original production environment. That baseline run reproduced **6 failed,
4 passed, 1 setup error** in 2.22 seconds, with the same error types and paths.

The original production environment does not include pytest. We appended the
candidate site-packages directory after the production paths only to make
pytest and its asyncio plugin available. Application packages were explicitly
imported before adding source test paths. No packages were installed into the
production environment. This proves these exact failures precede the migration;
it is not a claim that every dependency in the baseline process was an original
production dependency.

Host Commander evidence:

- Combined candidate job: `3d5fd6a0-a738-4ff4-be7b-881ffec5f4d6`.
- Pristine baseline result: `1a53f9c2-fa1a-4d04-ad2c-7fc80d8d1c4e`.
- Baseline source checkout: `/tmp/openviking-fastmcp-baseline-5fab`.

## Repairs and unchanged security meaning

The product already raises its typed `PermissionDeniedError` when a caller
tries to read, overwrite, or delete another user's temporary scope. Four tests
still expected built-in `PermissionError`. We corrected the expected type at
all six relevant assertion sites. The tests still require a denial and still
verify that another user's data survives prohibited operations. No production
ACL logic was changed.

Two listing tests used an in-memory AGFS double that predates the native
`tree_directory` method. A small shared test helper now returns the native
flat tree shape, including full paths and file metadata. Actual visibility and
user-isolation filtering remain in the real VikingFS implementation. The
helper handles hidden entries, depth limits, directory filtering and paging.

Session setup now starts the existing WatchRuntime, whose native AGFS client
requires a leadership interface. The local AGFS test double now implements
validation, acquisition, renewal and release. This is an exclusive, expiring
in-process lease per key with a holder ID and an opaque lease reference.
Unknown or expired references cannot renew or release a successor's lease.
It is deliberately a test implementation, not a distributed coordinator.

After the setup errors were removed, the focused group reached 67 passing
cases and exposed two more stale VLM-fixture failures. The fixture accepted only
a positional prompt, whereas the unchanged production VLM interface now accepts
message lists, tool schemas and token-budget arguments. It now accepts that
explicit interface and returns the supported empty extraction program for the
ordinary session-test memory path. The actual memory extraction implementation
still runs; tests for specific extraction behavior use their own dedicated
fakes. The archive-context test also now waits for and requires successful
completion of the seed archive job before adding a deliberately incomplete
archive. Its expected messages remain unchanged.

The fixture contract tests verify competition between holders, ownership,
expiration, stale references, and tree filtering/pagination. No runtime module,
deployed authentication setting, or persistent user data was changed for these
repairs.

## Re-running

Use the candidate interpreter and the repository launcher, which imports the
installed wheel before adding source tests to `sys.path`:

```sh
CANDIDATE=/home/ubuntu/.local/share/openviking/releases/0.5.5+ome.fastmcp42.1/venv/bin/python
SOURCE=/home/ubuntu/magbrain/01-Projects/00-personal_coding/20261119-openviking-fastmcp
"$CANDIDATE" "$SOURCE/test_scripts/fastmcp-migration/run_tests.py" \
  "$SOURCE/tests/utils/test_mock_agfs_contract.py" \
  "$SOURCE/tests/server/test_temp_scope_acl.py" \
  "$SOURCE/tests/server/test_api_sessions.py" -q --tb=short
```

The affected group and the original combined group must both be rerun after
these fixture repairs. Record the observed results alongside migration evidence;
the initial failed run remains useful evidence and must not be represented as
a passing run.

## Observed candidate validation

The corrected fixture group completed successfully on the installed candidate:
**71 passed, 5 deprecation warnings, 72.52 seconds**, exit code 0. This includes
all temporary-scope and session API tests plus the three new fixture contract
tests. Host Commander job: `3c462405-c8c3-479a-9140-161a069c6806`.

The tested candidate wheel SHA-256 reported by the build owner is
`c54bbb7f86e1e080b73c6a9522494bed15c7e08cdd34178f5c2a5ef44007aed9`.
The test launcher verifies that application modules come from that candidate's
installed environment before it exposes source tests.

The warnings concern existing Pydantic configuration/serialization deprecations
and the third-party Lark protobuf package's UTC datetime API. They are not
failures or evidence of complete coverage of every OpenViking subsystem.

The complete original nine-file group plus the three fixture contract tests
then passed together: **383 passed, 13 warnings, 458.07 seconds**, exit code 0,
with complete stdout capture and no stderr. Host Commander job:
`83d04da3-9db8-4e5a-8516-5cb0072d1105`. The combined files were:

- `tests/server/test_mcp_endpoint.py`
- `tests/server/test_mcp_fastmcp4.py`
- `tests/server/test_mcp_search_modes.py`
- `tests/server/test_mcp_search_bounds.py`
- `tests/server/test_mcp_parse_mode.py`
- `tests/server/test_identity_auth.py`
- `tests/server/test_temp_scope_acl.py`
- `tests/server/test_api_content_write.py`
- `tests/server/test_api_sessions.py`
- `tests/utils/test_mock_agfs_contract.py`

Besides the five warnings above, the combined run emitted eight deprecation
warnings for existing test assertions that still read SDK1 camelCase field
aliases through FastMCP's compatibility layer. They do not represent application
runtime errors or relaxed assertions. Those test-only aliases were subsequently updated to canonical SDK2
snake_case attributes without changing any string constants or wire dictionary
keys; the targeted follow-up result is recorded below.

This final combined result closes the original six failed temporary-scope
cases and 52 session setup errors under the migrated candidate. It covers this
explicit regression selection, not the entire upstream OpenViking test tree or
the independent protocol, OAuth, and external-agent compatibility groups.

### Canonical SDK2 assertion follow-up

After the final candidate refresh, the 19 affected endpoint-test functions
expanded to **32 passing parameterized cases**, with five existing deprecation
warnings and **no SDK2 alias warnings**, in **46.89 seconds**, exit code 0.
Host Commander job: `65bd29fe-8193-40e6-987e-a204ef66f2b6`. Ruff formatting and
lint checks also passed. The only stderr output was uvx's package-cache notice.

The final wheel SHA-256 reported by the build owner was
`685fdfd31f37b36bdfb529765872403bf395db975c736caa271be2204a1fa1ab`.
The full 383-case result above remains the historical combined run on the
preceding wheel. The subsequent runtime delta is the independent MCP service
registry query-redaction/address-sync fix, whose specific tests are owned by
the integration workstream. The canonical test-field cleanup itself changes
no runtime code.

The edit changed 36 SDK attribute reads across 19 functions. AST comparison
confirmed that only those attributes changed, and token comparison confirmed
that every string constant, including camelCase MCP wire dictionary keys,
remained intact.

## External compile task recovery audit

An additional security/authentication selection exposed 50 failures in the
parameterized compile protocol test in `test_bot_proxy_auth.py`. All initially
stopped at the same assertion: a requeued task reported `RUNNING`, while the
old test expected `PENDING`. A representative case reproduced this exact failure
on pristine **5fab0ce70** and the original production runtime in **0.84 seconds**
(Host Commander result `554d1686-34f4-4d21-a9bf-b58b03c0a5d2`). The same
pytest-only borrowing qualification described above applies.

The existing durable-work contract distinguishes the parent Task from each
queue delivery's Work. `TaskStore.start_work` marks the parent Task running;
`requeue_work` resets the delivery to pending without demoting the parent Task.
The existing queue middleware tests explicitly cover a running Task with
pending Work. The two outdated assertions were replaced with stricter checks:

- The parent Task is `RUNNING`, consistent with having already been admitted.
- Exactly one delivery for that task remains in the queue.
- The delivery's actual stored Work state is `PENDING`.
- The existing assertion that no remote submission has occurred remains intact.

With those checks in place, the ordinary complete/cancel/poll-failure cases
passed, but all **20 restart/restart-cancel variants** exposed a real recovery
defect. On recovery, `ExternalTaskService.restore_tasks` treated every running
task as a target owner, including an unsubmitted waiter. It then restored both
the already-submitted task and its waiter as owners of the same target. The
subsequent ownership check admitted the waiter, causing an actual second remote
submission before the first owner's work had resolved. The pre-fix full-file
run reported **40 passed, 20 failed** (job
`15848f9c-c6c3-4087-bffb-378531bc1a38`). Those no-submission assertions were kept.

The related external-task, queue, and task-store source was unchanged from the
pre-migration baseline before this fix. The original-runtime reproduction above
confirms the first status assertion; the newly reachable restart defect was
observed on the pre-fix candidate and traced to the unchanged recovery code.

The production correction is deliberately narrow: the existing check excluding
`CANCELLING` tasks with no external ID at stage `None` or `queued` now also
applies to `RUNNING` tasks. A claim-only waiter therefore remains a waiter after
restart. A task at stage `submitting` still reserves its target even without an
external ID because its remote outcome may be unknown. A task with a persisted
external ID remains an owner, including when the remote runtime reports a
queued stage. Existing `CANCELLED` cleanup behavior is preserved. Target keys
still include account, task type, and destination; same-account users cannot
bypass target serialization, and separate accounts remain independent.

The four ownership cases are preserved explicitly:

| Saved state | External ID | Recovery behavior |
| --- | --- | --- |
| Running or cancelling, stage absent/queued | Absent | Retain as a waiter; do not grant target ownership. |
| Running or cancelling, stage submitting | Absent | Reserve the target because the remote outcome may be unknown. |
| Already submitted, including a queued remote stage | Present | Restore the existing target owner. |
| Cancelled cleanup/recovery | Either | Preserve the existing conservative cleanup ownership behavior. |

An independent reviewer confirmed the predicate, the await-free check/claim
sequence, and preservation of ambiguous and submitted ownership. The new
`tests/service/test_external_task_recovery.py` matrix covers claim-only waiters,
ambiguous submissions, submitted owners, cancellation/terminal states, and
restoring a waiter before its existing owner.

### Observed recovery-fix validation

On the rebuilt installed candidate, **108 tests passed**, with five existing
deprecation warnings, in **9.08 seconds**, exit code 0 and no stderr:

- All **60** cases in `tests/server/test_bot_proxy_auth.py`, including all 20
  restart/restart-cancel cases.
- All **19** focused restore-state/mixed-owner cases.
- All **29** existing queue middleware cases.

Host Commander job: `dff56cad-e59e-43ab-a346-af02cc2a9697`. The installed wheel
SHA-256 reported by the build owner was
`4e7f7d7e3131d5bf9499a899ff1941ada913d5165aee3c4eabb0a3aec22c8cf7`.
Ruff checks and the scoped diff check passed. The original remote non-submission,
exactly-once submission, cancellation, restart recovery, ACK retention, and
rejection-release assertions were preserved. The build owner is separately
rerunning the combined 383-case core selection on this final recovery-fix wheel;
the earlier combined result above is retained as historical evidence.

The complete raw stdout for the 108-case recovery run is also retained at
`/home/ubuntu/.local/share/openviking/releases/0.5.5+ome.fastmcp42.1/evidence/release-recovery-final.log`,
so this evidence does not depend on Host Commander result retention.
