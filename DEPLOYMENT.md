# OpenViking downstream deployment

## Source model

- `main` mirrors `https://github.com/volcengine/OpenViking.git`.
- `ome/deploy` is based on an upstream stable tag and carries reviewed fixes from
  [PATCHES.md](PATCHES.md). The namespace avoids Codex's marketplace checkout
  treating a bare `deploy` ref as ambiguous with this repository's `deploy/`
  directory.
- Fix branches target upstream `main`; only reviewed commits are cherry-picked
  into `ome/deploy`. Never merge `main` or an upstream feature branch into `ome/deploy`.

## Release build

Build from a clean archive of the release tag. OpenViking's SCM tag parser only
accepts numeric upstream tags, so set the fork package version explicitly:

```bash
SETUPTOOLS_SCM_PRETEND_VERSION=0.5.5+ome.1 uv build --wheel
```

Release only a commit that is on `origin/ome/deploy` and carries a pushed
`ome-<version>` tag. A wheel built from any other branch is a candidate for
testing, not a release; merge its commits into `ome/deploy` and tag before
deploying it as the live service. Record the tag in the release metadata.

Refresh the stable MCP proxy that agent configs launch from the same tag:
`git archive <tag> agent-plugins | tar -x --strip-components=1 -C ~/.local/share/openviking/agent-plugins`,
then write the tag and commit to `~/.local/share/openviking/agent-plugins/SOURCE`.
Never point agent configs at a worktree.

Store the wheel, an exact requirements export, checksums and deployment metadata
under:

```text
~/.local/share/openviking/releases/0.5.5+ome.1/
```

The package requires the `bot` extra and a Linux ARM64 native build. Run the
affected Python suites, the Codex and OpenCode plugin suites, and the plugin
version gate before promoting the artifact.

## Server update

Staged qualification must compare the old and new Gateway tool inventories.
If a profile previously relied on the built-in write exclusions, explicitly
preserve those exclusions before deploying v0.5.5. Preserve existing backend
choices, check for partial imports, and budget for first-use index backfill.

1. Confirm no pending or running ingestion tasks.
2. Back up the complete workspace, OpenViking config, systemd user unit and the
   current uv tool environment.
3. Stop `openviking.service`. Stop any other participants that write to the same
   workspace before installation. Keep isolated canaries on their own workspace.
4. Install the qualified wheel and exact dependency set into the versioned uv
   tool environment.
5. Start `openviking.service` and verify the running version, process identity,
   health, retrieval, a resource read, session capture and Kanban access.

The service remains:

```text
ExecStart=%h/.local/bin/openviking-server --with-bot
```

## Client plugins

- Claude Code and Codex use the `openviking` marketplace from `oliver-mee/OpenViking` ref
  `ome/deploy`, so the plugin source and server source share the release tag.
- OpenCode uses the documented source-install layout at
  `~/.config/opencode/plugins/openviking`. OpenCode v2 discovers that directory
  directly; omit the forwarding `openviking.js` wrapper and npm package entry
  to avoid duplicate plugin registration.
- Install the companion Toolkit core pack before updating these bundles. It owns
  `openviking-memory`, `openviking-skills`, `ov-experience-memory` and
  `openviking-rule-search`, plus the resource, troubleshooting and Kanban skills.
  Claude Code and Codex retain their own `ov-memory-doctor` and local scripts.
  Hooks, MCP tools and server-stored skill catalogues remain adapter-owned.
- Update through native marketplace commands. For OpenCode's source install,
  replace the plugin directory from a qualified complete artifact rather than
  copying over it, which would retain removed skills. Keep rollback outside the
  discovered plugins directory.
- Verify fresh skill discovery, plugin version and MCP registration. Existing
  sessions may retain loaded prose; do not interrupt them merely to refresh it.

## Rollback

Stop the service, restore the prior uv tool environment and client plugin
configuration. If migrations ran, restore the pre-upgrade workspace before starting the previous server. Do not run old and new lock participants together.
