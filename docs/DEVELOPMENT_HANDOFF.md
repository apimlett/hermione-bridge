# Development handoff: hermes-bridge (Hermione)

Agent rules are in [../AGENTS.md](../AGENTS.md). Deployment records for a specific host are kept privately, outside this repository.

## Setup and test commands

```bash
python3 -m unittest discover -s tests -v     # 49 tests: OK (Python 3.12)
```

There are no runtime dependencies and no install step is needed for tests: they put `src/` on `sys.path`. Pre-existing socket and HTTP `ResourceWarning`s may print. They aren't failures.

To exercise the Android client against this source, run `HERMES_BRIDGE_SRC=/path/to/hermione-bridge npm test` in the Android repository. All 149 tests pass.

Running locally (development only, never against production credentials):

```bash
PYTHONPATH=src python3 -m hermes_bridge init --key-file /tmp/hb/key --dashboard-token-file /tmp/hb/token
PYTHONPATH=src python3 -m hermes_bridge --config examples/hermes-bridge.toml check
PYTHONPATH=src python3 -m hermes_bridge --config <your.toml> serve
```

`check` exits 1 when the Hermes dashboard isn't compatible. A config error exits with 78. See [../README.md](../README.md) for Docker, uv and systemd variants, and [../examples/](../examples/) for the unit files and TOML.

### Recommended deployment shape

- A private, loopback-only Hermes backend (`hermes serve --isolated`) for the bridge to call. Never proxy it.
- The bridge on loopback, with mobile scope enabled (`allowed_profiles`, `new_profile_prefix`).
- One tailnet-only Tailscale Serve origin that maps the root to the Hermes API server and `/bridge` to the bridge (`base_path = "/bridge"` if the prefix isn't stripped). No Funnel.
- Bridge key and dashboard token in mode-0600 files outside the repository.

## Upstream provenance

| Item | Value |
|---|---|
| Upstream | https://github.com/suryavamsi6/hermes-bridge (MIT; `LICENSE.md` retained) |
| Remote name | `upstream` |
| Base | `76862fff2c3652aea9b6267b27fdc7cda29b4bab` (upstream 0.4.0) |
| Package version | `0.4.0` (unchanged) |

## Modifications from upstream

- `src/hermes_bridge/config.py`:
  - adds `allowed_profiles` and `new_profile_prefix`, configurable in TOML or environment;
  - validates them at startup (no `all`; the prefix must end in `-` or `_`);
  - adds a `mobile_scope` property;
  - an explicitly empty environment value counts as set.
- `src/hermes_bridge/app.py`:
  - the scope check runs before the compatibility probe and any upstream call;
  - `403 profile_denied` for out-of-scope paths, rosters filtered, `profile` overrides denied;
  - `clone_from` denied, and new profiles must use the prefix;
  - gateway restart denied (`restart_denied`);
  - slash commands limited to `/status`, `/help` and printable single-line `/title text`, with the catalog trimmed to match.
- `src/hermes_bridge/server.py`: in scoped mode, keeps blank query values and rejects duplicate query parameters (`400 invalid_query`).
- `examples/hermes-bridge.toml`: scope enabled by default (`["default"]`, `mobile-`).
- `README.md`: configuration rows and the "Mobile profile scope" section.
- New files: `tests/test_mobile_scope.py` (15 tests), `CLAUDE.md`, `AGENTS.md` and `docs/`.

## Verified versus unverified

### Verified

- 49 unit and integration tests against the fake dashboard and gateway (34 upstream and 15 scope tests).
- Against a real Hermes 0.22.5 deployment, over tailnet HTTPS: missing and wrong keys return 401; authenticated status and the scoped roster return 200; a protected profile returns 403 `profile_denied`; a restart attempt returns 403 `restart_denied` without executing; authenticated reads of jobs, skills, toolsets, MCP, plugins, memory, persona and usage succeed; `check` returns `ok=true`.

### Unverified

- Any **mutation** against live data (profile creation, key rotation, job actions, toggles, installs, memory and persona writes). Only fixtures have exercised these.
- Scoped slash-command execution through a live gateway socket.
- Behaviour on Hermes versions other than 0.22.5.
- The artefact service: proposed only. See [prd/native-artefacts.md](prd/native-artefacts.md).

## Release blockers

1. Live validation of each mutation the app exposes, or keep them disabled in the app.
2. An ownership registry for the `mobile-` namespace. Scope currently trusts the prefix: any existing profile matching it is accessible. That's acceptable only while the namespace is reserved by operator discipline.
3. This is an application-level boundary, not an OS sandbox. Chat tools keep the Hermes user's access to the host. Document that for any operator before wider use.
