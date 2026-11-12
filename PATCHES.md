# Patches carried on `ome/deploy`

`ome/deploy` is the latest upstream stable release plus the fixes below, one commit
per fix. It is the source for the self-hosted server and downstream agent plugin
bundles. `main` remains an unmodified mirror of `volcengine/OpenViking`.

Base: `v0.5.4` (`9dfa9017a8c61abefcd239fe98fdba094a2de7f3`). Fork release:
`ome-0.5.4-1`, package version `0.5.4+ome.1`.

| Commit subject | Why we carry it | Upstream | State |
| --- | --- | --- | --- |
| `fix(compile): publish declared resource retrieval tags` | Published resource pages retain valid explicit retrieval tags and an unchanged body still reconciles metadata. | [#6867](https://github.com/volcengine/OpenViking/pull/6867) | open |
| `fix(codex-plugin): keep pre-upgrade sessions' hooks working` | Active Codex threads no longer lose every OpenViking hook when an upgrade removes their versioned plugin cache directory. | [#5986](https://github.com/volcengine/OpenViking/pull/5986) | open |
| `fix(opencode-plugin): only commit new captures during v2 cleanup` | OpenCode cleanup no longer creates empty server commits for sessions with nothing new since their last commit. | [#5497](https://github.com/volcengine/OpenViking/pull/5497) | open |
| `fix(opencode-plugin): prefetch recall without blocking prompt admission` | Recall begins during the prompt hook but waits and injects at request context, keeping user-message admission fast and replay stable. | [#5496](https://github.com/volcengine/OpenViking/pull/5496) | open |
| `chore(plugins): delegate shared skills to companion pack` | The three downstream host bundles retain integration code and local doctors while the companion pack owns shared guidance. The sync target filter prevents duplicate skills returning on rebuild. | downstream packaging policy | carried |

Remove each row when its equivalent lands in a stable upstream release and the
corresponding tests pass on the upstream implementation.

The server wheel excludes `examples/`, so the Codex and OpenCode fixes are
deployed as separate plugin artifacts from this same tag. Hermes already has a
native OpenViking provider and is not changed by this deployment.
