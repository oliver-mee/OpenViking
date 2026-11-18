# FastMCP migration and client compatibility

The MCP endpoint uses independent Python FastMCP in the OpenViking process at
`/mcp`, beside the REST API. Existing handlers continue to call OpenViking domain
services under the same account, user, role and actor-peer context. There is no
extra REST proxy hop or second knowledge store.

The deployment baseline is OpenViking `0.5.5+ome.1`, source commit
`5fab0ce701880e7e7053b307ca788fd36329ee24`. The earlier five-tool REST proxy is a
prototype, not the migrated implementation.

## Preserved contracts

Keep the 27 original tool names, arguments, meaningful failures, media content,
structured results and upstream-tool invocation behavior. Preserve API-key and
OAuth authentication, the existing OAuth store and grants, public issuer and
protected-resource metadata. Resolve identity before dispatch and reset it after
each request; concurrent callers must not share an implicit operator credential.

Keep REST, native clients, memory/session processing and accepted background work
independent of MCP request lifetimes. Both legacy `initialize` clients and the
2026-07-28 discovery protocol must work. A modern protocol header must not be sent
with a legacy initialize request.

SDK 2 uses `httpx2` for its HTTP transport and OAuth client. OpenViking's REST
clients can continue using `httpx`. The packages coexist, but objects passed into
the SDK must come from the HTTP library the SDK actually uses.

For registered downstream servers, prefer credentials in HTTP headers. Public
server summaries and generated tool documents replace the entire URL query with
`?__query_redacted__`; names and values of arbitrary query parameters are private.
The original URL remains in the private registry and is used unchanged for
connection and invocation. Query-free endpoint URLs are unchanged.

Preexisting generated documents may still contain an earlier full address. A
normal, explicitly requested catalog sync now republishes an address-only change
even when tool/schema revisions are unchanged, so normal document processing can
refresh derived/indexed copies. Deploying the code alone does not rewrite or
reindex historical documents. Assess affected registry scope before scheduling
that regeneration; do not scan or rewrite other accounts implicitly.

## Features used deliberately

FastMCP serves five packaged operational Skills: `openviking-memory`,
`openviking-skills`, `openviking-rule-search`, `ov-experience-memory` and
`ov-resources`. One packaged source is available through native Skills for modern protocol
clients, native MCP resources and the ordinary `list_resources` / `read_resource`
tools. Legacy clients use the resource and tool paths.
Tool-only clients discover an exact `skill://` URI and read it without installing
a local Skill.

The bridge must preserve modern native Skill discovery. Reading the same guide
through native Skills and resource/tool paths is a release contract. User-installed OpenViking Skills remain
authorized `viking://` knowledge, managed with `find`, `read` and `add_skill`.

Supported framework registration, lifecycle, context and middleware APIs replace
private SDK 1 internals. Portable schemas, omitted-argument semantics, original
content blocks and `isError` results remain client contracts. HTTP success does
not mean a tool succeeded.

Timing diagnostics record MCP methods, elapsed time and outcomes. They must not
record arguments, credentials, private URIs, content or exception strings that
may contain them. Outbound SDK/HTTP diagnostics use task-local redaction because
parsing errors can otherwise log private response bodies before the public error
boundary. Preserve severity, HTTP status and exception class while clearing raw
messages and traceback text; other callers keep their own logging behavior.

Modern HTTP cancellation reaches and stops request-owned handler work. For the
legacy stateless transport, acknowledging a separate cancellation notification
POST does not cancel the original request. Neither path can undo a completed
write or revoke an independently accepted ingestion task. Progress is currently
limited to token-bound start/end milestones (0 and 1); it is not granular progress
inside an embedding, ingestion or retrieval operation. Requests without a
progress token receive no progress notifications.

Do not enable shared result caches or automatic mutation retries merely because
the framework offers them. Results depend on identity, ACLs, mutable content,
live context and external side effects. MCP Tasks, sampling, Apps and elicitation
are optional features that require implemented behavior and a specific use case.

## Client paths

| Path | Compatibility contract |
|------|------------------------|
| ChatGPT | Authenticated HTTP and all original tools, plus guides through ordinary tools. New tool names may require a connector refresh. |
| OpenCode 2 direct HTTP | Legacy by default; `protocol: "auto"` probes modern discovery and falls back. Check actual schema conversion and execution. |
| OpenCode OpenViking plugin | Local stdio proxy forwards legacy JSON-RPC and preserves credential selection and actor-peer headers. |
| Hermes direct MCP | Installed transport negotiates protocol and converts schemas. Check actual calls and native resource utility behavior. |
| Hermes native OpenViking plugin | REST capture, recall and `viking_*` tools continue using the same endpoints and identity. |
| OpenViking outbound MCP | SDK client discovery and invocation retain credentials, annotations, media, structured results and upstream errors. |

Versions inspected for this deployment: OpenCode `2.3.2`, deployed OpenViking
OpenCode plugin `0.5.30-ome.1`, Hermes `0.23.2` (source `ae2a81ccc00e`), its
MCP SDK `2.3.0` with `httpx2 2.15.1`, and native Hermes OpenViking plugin
`2.1.2`. Inspection is not evidence of a passing integration test.

OpenCode's existing shared proxy buffers an HTTP/SSE response until completion.
Do not describe that path as displaying live progress without a separate proxy
change and test. The inspected Hermes registration code keeps a server-native
tool when an optional generated resource utility has the same normalized name. This preserves the
server bridge tools. Existing include filters can still hide new tools.

## Qualification

Use isolated profiles, storage fixtures and ordinary fixture identities. Never
start a second full server on the live data directory. Qualification must not
require inference or change a real conversation, credential profile or knowledge
item.

1. Start the candidate with its pinned dependency closure. Verify the built
   package contains the guides, not only the source checkout.
2. Exercise legacy HTTP and modern discovery. Check all original tools, guide
   tools, native resources and native Skills where supported.
3. Check successful authorized fixture reads/writes, invalid input, invalid or
   missing credentials, cross-user denial and concurrent identity isolation.
4. Exercise OAuth discovery, DCR, PKCE, code exchange, refresh and revocation
   with an isolated store. Preserve the actual store and issuer during rollout.
5. Run relevant MCP/domain/auth and outbound-client tests. Validate media and
   structured errors through the protocol.
6. Run installed OpenCode and Hermes clients without an LLM. Discover tools,
   execute `health`, read a guide and an allowed fixture, and verify errors.
   Also check the native Hermes REST client.
7. After promotion, verify health, authenticated public discovery, an allowed
   data operation and guide retrieval. Record versions, source and rollback.

Generate source-owned client fixtures before their tests:

```bash
node examples/memory-plugin-shared/sync.mjs
node --test \
  examples/memory-plugin-shared/mcp-proxy-core.test.mjs \
  examples/memory-plugin-shared/mcp-proxy-config.test.mjs \
  examples/memory-plugin-shared/mcp-hook-parity.test.mjs \
  examples/memory-plugin-shared/one-header-builder.test.mjs \
  examples/memory-plugin-shared/wire-headers.test.mjs \
  examples/opencode-plugin/tests/mcp-config.test.mjs
```

The seven HTTP protocol tests in `tests/server/test_mcp_protocols.py` pass
against the candidate. They exercise modern/legacy discovery, guide access,
parent-app state and identity, token-bound progress, no-token silence and the
different cancellation behavior described above. The legacy cancellation case
explicitly verifies that the original work remains running until released.

The final installed candidate passes all **16** outbound SDK tests in
`bot/tests/test_mcp_sdk2.py`: real stdio, SSE and HTTP discovery/calls,
pagination, structured results and errors, cancellation, timeout recovery,
registry invocation, transport log redaction, public URL metadata redaction and
address-only catalog sync. A real downstream fixture verifies that original
private endpoint queries still arrive unchanged. Credential sentinels are tested
in URL queries and reflected malformed response bodies. The tests verify that
HTTP status/error class remain useful, exception and stack text are cleared,
and unrelated concurrent callers and connection owners keep their logs.

The source Hermes native provider suite (plugin **2.1.4**) passed **432/432**
against the installed Hermes host `ae2a81ccc00e`, using isolated profiles and fake
services. Two test helpers now support both the reviewed older host and current
host: the action schema can live inside `operations[]`, and prefetch may require
`previous_message`. No behavioral assertions were removed. This is separate from
the installed native plugin **2.1.2**, whose real REST health/read path was also
exercised against the candidate.

The selected Node client contracts passed **76/76** after generation. The initial
run lacked generated DSH packaging fixtures; that was a fixture preparation
failure, not a passing run. The isolated candidate also passed the actual
OpenCode shared proxy, installed OpenCode 2.3.2 tool executors, Hermes
legacy/modern MCP transport and native Hermes REST read checks. These covered the 29-tool catalog, hosted guide retrieval
and an authorized disposable fixture. Production outcomes belong in the deployment
receipt.

Temporary client probes under `test_scripts/fastmcp-migration/` accept a
mode-0600 JSON file through `OV_MCP_PROBE_CONFIG_FILE`: candidate URL, headers,
and optionally a dedicated fixture URI/marker. They print versions, counts and
check outcomes, not credentials or user content. Non-loopback targets require
explicit `OV_MCP_PROBE_ALLOW_REMOTE=1`.

- `proxy-roundtrip.mjs`: actual shared OpenCode proxy.
- `hermes-roundtrip.py`: installed Hermes MCP transport in both protocol modes
  and its native OpenViking REST client.
- `opencode-roundtrip.py`: installed OpenCode binary, temporary XDG/profile
  directories, real MCP tool executors, no model prompt or user config changes.

Use Hermes's published runtime selector rather than an assumed `.venv` path.
Its managed install can use a prepared package store and another Python
interpreter. Leave the installed dependency generation unchanged.

These probes establish connection, discovery, schema conversion, authentication
and tool execution. They do not establish recall quality, embedding throughput,
long-term memory accuracy or every desktop UI behavior.

## Primary references

- [FastMCP SDK migration](https://gofastmcp.com/docs/getting-started/upgrading/from-mcp-sdk-v1)
- [FastMCP ASGI](https://gofastmcp.com/docs/integrations/fastapi)
- [FastMCP Skills](https://gofastmcp.com/servers/skills)
- [Resources as Tools](https://gofastmcp.com/docs/servers/transforms/resources-as-tools)
- [OpenCode MCP](https://opencode.ai/docs/mcp-servers/)
- [OpenCode tool API](https://opencode.ai/docs/build/plugins/server)
- [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
