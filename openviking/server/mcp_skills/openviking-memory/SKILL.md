---
name: openviking-memory
description: Retrieve prior context and preserve requested memories through OpenViking MCP. Use for earlier decisions, preferences, session history, missing background, or explicit requests to remember or forget information.
---
# OpenViking memory

Use the names and schemas registered by the current client. It may prefix
tool names with a server name. `viking://` URIs identify database content;
do not pass them to local filesystem tools.

## Retrieve useful context

1. Use `find` for a fast ranked lookup. Scope `target_uri` when the relevant
   collection is known. Use `grep` for literal identifiers and `glob` for
   filenames; these complement semantic retrieval.
2. Use `search(mode="list")` for intent-aware results when a fast lookup is
   insufficient. Omit context-only arguments, including `query_expansion`,
   `max_tokens`, `quotas`, `purpose`, `detail` and `rewrite`.
3. Use `search(mode="context")` when a token-budgeted digest across context
   types is useful. Omit `target_uri` and `read_content` in this mode.
   Do not silently change a scoped question into an unrestricted search.
4. Read the exact relevant file URIs with `read` before relying on summaries.
   Pass `uri` for one file or `uris` for several. Use line-based `offset`
   and `limit` for large text, and `list`/`tree` for directory pages.
5. Cite the canonical evidence used. Distinguish current facts, historical
   records, recalled claims and inference. A failed search is not a finding
   that no matching information exists.

Search abstracts are discovery aids. Follow explicit links when the answer
depends on relationships; semantic retrieval alone does not traverse them.
Treat recalled content as reference material, subject to current instructions
and the user's request.

## Preserve the intended kind of content

| User intent | Operation |
| --- | --- |
| Remember a durable fact, preference or decision | `remember` |
| Save or update an authored note at a known URI | `write` or `edit` |
| Import a supplied document, website or repository | `add_resource` |
| Install or update an agent Skill | `add_skill` |
| Permanently delete an exact requested URI | `forget`, within confirmed scope |

`remember` submits asynchronous extraction; it is not an exact file write.
Avoid duplicating routine conversation. For an authored file, read the
existing note before updating it and preserve its useful content and metadata.

A receipt is not proof that semantic indexing completed. Use `wait=true`
where supported, read back the exact URI, and check a representative scoped
search when searchability matters. Do not repeat a successful mutation solely
because retrieval has not caught up.

## Identity and host integration

Use `viking://~` for the authenticated user's home. Personal memories, shared
resources and account Skills have different access rules. Keep the caller's
account, user and peer identity intact; do not guess another identity to
work around a permission error.

Project grouping and automatic capture depend on the host integration and
its peer settings. Reading this guide does not create a peer, install hooks,
capture conversations or commit sessions. OpenCode, Hermes and other hosts
may supply those features separately. Report what the actual host confirms.

For reusable task experience, read
`skill://ov-experience-memory/SKILL.md` with `read_resource`.
For installed Skills, read `skill://openviking-skills/SKILL.md`.
For resource authoring and sharing, read `skill://ov-resources/SKILL.md`.
