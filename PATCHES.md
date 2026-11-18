# Patches carried on `ome/deploy`

`ome/deploy` is the latest upstream stable release plus the fixes below, one commit
per fix. It is the source for the self-hosted server and downstream agent plugin
bundles. `main` remains an unmodified mirror of `volcengine/OpenViking`.

Base: `v0.5.5` (`4f4b09c8b81cc52cc0361ec7d9f189ce0ccda35b`). Fork release:
`ome-0.5.5-1`, package version `0.5.5+ome.1`.

| Commit subject | Why we carry it | Upstream | State |
| --- | --- | --- | --- |
| `fix(compile): publish declared resource retrieval tags` | Published resource pages retain valid explicit retrieval tags and an unchanged body still reconciles metadata. | [#6867](https://github.com/volcengine/OpenViking/pull/6867) | open |
| `fix(codex-plugin): keep pre-upgrade sessions' hooks working` | Active Codex threads no longer lose every OpenViking hook when an upgrade removes their versioned plugin cache directory. | [#5986](https://github.com/volcengine/OpenViking/pull/5986) | open |
| `fix(opencode-plugin): only commit new captures during v2 cleanup` | OpenCode cleanup no longer creates empty server commits for sessions with nothing new since their last commit. | [#5497](https://github.com/volcengine/OpenViking/pull/5497) | open |
| `fix(opencode-plugin): prefetch recall without blocking prompt admission` | Recall begins during the prompt hook but waits and injects at request context, keeping user-message admission fast and replay stable. | [#5496](https://github.com/volcengine/OpenViking/pull/5496) | open |
| Backport OpenCode skill registration and parallel prompt preparation | Restores native bundled registration and correct project precedence. The deployment uses the companion pack for its skill inventory. | [#7582](https://github.com/volcengine/OpenViking/pull/7582) | merged after v0.5.5 |
| Preserve session rows across concurrent saves | Concurrent OpenCode locations keep each other's pending capture and local mappings. | [#7908](https://github.com/volcengine/OpenViking/pull/7908) | draft |
| OpenCode V2 Doctor and terminal controls | Read status and diagnostics, inspect recall, and explicitly commit the current session. | [#7910](https://github.com/volcengine/OpenViking/pull/7910), [#7911](https://github.com/volcengine/OpenViking/pull/7911) | drafts |
| OpenCode V2 experience hints and context assistance | Optional skill experience hints, citation-preserving recall compression with raw fallback, and archive references during native compaction. | [#7909](https://github.com/volcengine/OpenViking/pull/7909), [#7912](https://github.com/volcengine/OpenViking/pull/7912), [#7913](https://github.com/volcengine/OpenViking/pull/7913), [#5727](https://github.com/volcengine/OpenViking/pull/5727) | drafts and citation prerequisite |
| Forward OpenCode recall exclusions | Carry the configured exclusions into the context-search request. | [#6850](https://github.com/volcengine/OpenViking/pull/6850) | open |
| `chore(plugins): delegate shared skills to companion pack` | The three downstream host bundles retain integration code and local doctors while the companion pack owns shared guidance. The sync target filter prevents duplicate skills returning on rebuild. | downstream packaging policy | carried |

Remove each row when its equivalent lands in a stable upstream release and the
corresponding tests pass on the upstream implementation.

The server wheel excludes `examples/`, so the Codex and OpenCode fixes are
deployed as separate plugin artifacts from this same tag. Hermes already has a
native OpenViking provider and is not changed by this deployment.
