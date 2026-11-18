---
name: ov-resources
description: Browse, retrieve, author, import and share OpenViking resources through MCP. Use for notes, documents, websites, repositories, indexing verification, access grants or watch management.
---
# OpenViking resources through MCP

Use the registered tools. A source URL or client-local path supplied for
ingestion differs from a stored `viking://` resource URI.

## Choose the operation

- Discover with `find`/`search`, match exact text with `grep`, and match
  filenames with `glob`. Read the full relevant evidence with `read`.
- Browse known directories with `list`/`tree`. Follow returned pagination;
  one bounded page is not the entire collection.
- Use `write` for authored material and `edit` for exact replacements.
  Read current content before editing. Preserve URI, useful metadata,
  source attribution and the collection's policy.
- Use `add_resource` for supplied files, websites and repositories. Search
  for an existing import before creating an expensive duplicate.
- Use `add_skill` for installed Skill packages.
- Use `download` for original bytes when text/media reads are insufficient.
  Treat the returned signed URL as temporary access to that file.
- Use `forget` only for the exact authorized deletion scope.

Do not directly modify the server's backing filesystem. Do not substitute
another user's identity or an administrator token after a permission denial.

## Author and verify notes

Read first, then use the smallest suitable change. `edit` requires an exact,
unique old string unless `replace_all=true` is intentional. Re-read after
a conflict; do not overwrite another editor's changes with a stale full copy.

Use `wait=true` for sequential edits when supported. Read back the exact
URI and check a representative scoped search when indexing matters.
Multiple writes are not one transaction. Report partial completion and
resource-busy errors accurately.

`write`/`edit` do not parse supplied PDFs, refresh repositories or install
Skills. A write acknowledgement and completed indexing are different states.

## Import and watch

Bound large imports to the requested source, revision and useful exclusions.
`add_resource(to=...)` can refresh an existing source and remove entries no
longer present in it. Establish that replacement scope before using it.

Remote URL imports run asynchronously. A local path produces upload
instructions: the server cannot read the client's filesystem. Uploading
requires a separate authorized file/HTTP capability. Preserve returned task
identifiers, resource URIs and partial-failure information.

Create recurring watches only when intended; they can repeat processing
costs. Inspect `list_watches` and cancel an exact intended target with
`cancel_watch(to_uri=...)`. Cancelling a watch does not itself delete the
imported content. Do not invent generic job-status or job-cancel tools when
the current MCP catalog does not provide them.

## Share without dropping existing grants

Use `list_users`/`list_groups` to resolve recipients and `get_acl` to read
the resource's current direct, inherited and effective grants. Respect its
manage-permission requirement.

`set_acl(entries=...)` replaces direct grants. Include grants that must
remain, and do not change inheritance unless intended. A normal content edit
should preserve sharing. Read back the ACL after an authorized change.

Credentials belong in the dedicated privacy tools when the user explicitly
asks to save or use them, never in a note or Skill. Do not reveal stored
secret values in unrelated results or diagnostics.

## Failure handling

A retrieval error is not an empty result. A successful mutation followed by
an indexing timeout is not a reason to replay the mutation automatically.
Keep exact public error codes and opaque error reference IDs when reporting
a failure. Read successful parts of a batch and identify failed parts.
