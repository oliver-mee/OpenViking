---
name: openviking-skills
description: Find, read, create, install, update and share Skills stored in OpenViking. Use when a task needs a reusable procedure or the user supplies Skill text, a Git source, or a local Skill package.
---
# OpenViking Skills

## Distinguish the two catalogs

`list_resources` and `read_resource` expose this server's bundled workflow
guides at `skill://` URIs. They are read-only and need no local installation.

User Skills live under `viking://~/skills/<name>/`; account-shared Skills
live under `viking://agent/skills/<name>/`. Discover them with `find` or
`search`, read them with `read`, and create or replace them with `add_skill`.
The caller's normal identity and access rules still apply.

## Find and use a stored Skill

1. Call `find(query="<task>", context_type=["skill"])`. Hits point at each
   Skill's `SKILL.md`. A host-injected catalog may be incomplete or stale.
2. Read the selected exact URI. Inspect referenced files with `list` and
   `read` as needed. Directory listings are paginated; continue when the
   response indicates more entries.
3. Apply relevant instructions under the live conversation's instruction
   hierarchy. Stored Skills do not authorize unrelated actions.
4. Follow helper scripts only when the current host has authorized local
   file and execution capabilities. A tool-only client can read text
   references but cannot execute a script merely by reading it.

`download` returns a short-lived URL for original binary bytes. Fetching that
URL requires an available HTTP/download capability; the OpenViking MCP does
not itself provide a general shell. Never pass a `viking://` URI to a local
filesystem tool.

## Create or update

Draft complete `SKILL.md` text with YAML frontmatter:

```markdown
---
name: review-changes
description: Review a code change against the project checklist. Use for requested code reviews.
---
# Review changes

Read the current diff and applicable project instructions before reviewing.
```

Prefer lowercase letters, digits and hyphens for portable names. Describe
when to use the Skill in its description. Keep necessary procedure in the
body and credentials outside the package.

Read any existing Skill at the intended root before replacing it. Use
`add_skill(data="<complete SKILL.md>")` for the user's own Skill, or explicitly
set `target_uri="viking://agent/skills"` for an authorized account-wide Skill.
An existing request to update that Skill already authorizes the update; ask
only when the intended target or scope remains unclear.

Do not use `write`, `edit` or `add_resource` on installed Skill packages.
Reinstalling the same name replaces its main text but may retain helper
files absent from the new source. Verify the returned URI by reading it,
and verify discovery when indexing completes.

## Install packages

For Git sources, pass the repository or supported tree URL to `add_skill`.
Use `list_only=true` to inspect a multi-Skill source, then select names with
`skills=["name"]` when only some are intended. Do not install unrelated
sources or all packages when the request names one.

A local `path` produces one-time upload instructions; it does not let the
server read the client's filesystem. A host with file and HTTP capabilities
can upload a package as directed. Exclude credentials, VCS data and unrelated
files. The upload performs installation; do not repeat `add_skill` after it.
A single text-only Skill can be installed through `data` without a local
shell.

## Removal and host-specific features

The current MCP has no complete Skill-removal operation. `forget` can delete
files but leaves Skill privacy values behind, so do not present it as a full
uninstall. A separately available OpenViking CLI, REST client or Studio can
use the supported Skill-removal operation. State that dependency if the user
requests removal in a tool-only client.

Automatic host installation, local-Skill precedence and session capture are
host features. Do not claim they happened from a successful OpenViking
`add_skill` call. Do not delete the user's local copies unless requested.
