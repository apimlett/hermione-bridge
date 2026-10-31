# Agent context: hermes-bridge (Hermione)

This is the single source of agent context for this repository. [CLAUDE.md](CLAUDE.md) only points here.

## What this is

A small, standard-library-only Python (>= 3.11) service that exposes an allowlisted, bearer-key-protected slice of the Hermes dashboard API and gateway slash commands to the Hermione Android app. It's adapted from the MIT-licensed [suryavamsi6/hermes-bridge](https://github.com/suryavamsi6/hermes-bridge), based on `76862fff2c3652aea9b6267b27fdc7cda29b4bab` (upstream 0.4.0).

- `src/hermes_bridge/app.py`: route table, auth, mobile scope and handlers.
- `src/hermes_bridge/config.py`: TOML and environment configuration (environment wins).
- `src/hermes_bridge/server.py`: stdlib HTTP server. 64 KB body cap, 30 s socket timeout.
- `src/hermes_bridge/gateway.py` and `upstream.py`: gateway socket and dashboard clients.

The Hermione change is **mobile scope** (`allowed_profiles`, `new_profile_prefix`). When enabled, it restricts the roster and profile paths, blocks cloning and gateway restarts, limits slash commands to `/status`, `/help` and `/title text`, and rejects duplicate query parameters. See the README section "Mobile profile scope".

## Read next

1. [docs/DEVELOPMENT_HANDOFF.md](docs/DEVELOPMENT_HANDOFF.md): commands, provenance, modifications, verified and unverified functionality, release blockers.
2. [docs/prd/native-artefacts.md](docs/prd/native-artefacts.md): **canonical** cross-repository PRD for the proposed artefact service. Proposed, not implemented.
3. [README.md](README.md): upstream setup and API reference, plus the Hermione scope section.

## Hard rules

- **This repository is public.** Never commit hostnames, tailnet names, IP addresses, ports of a real deployment, personal names, home-directory paths or service names from a real host. Keep deployment notes outside the repository.
- Keep runtime dependencies at zero (stdlib only) unless an exception is approved.
- Mobile scope must fail closed. A new profile-addressed endpoint must pass `_profile_allowed` before any upstream call or disk access, and must be covered in `tests/test_mobile_scope.py`.
- Never expose the Hermes dashboard or private backend publicly. Tailnet-only Serve, no Funnel. Only the authenticated bridge may be proxied.
- Never print, log or return secrets (bridge key, dashboard token, profile API keys) except the one documented key-creation response.
- Don't restart or reconfigure a running bridge, backend or Hermes gateway, or change server profiles, credentials or Serve routes, without approval.
- No commits, pushes or PRs without approval.
- Base dashboard and gateway calls on hermes-agent source. Don't invent upstream endpoints.

## Before handing back work

```bash
python3 -m unittest discover -s tests -v
# app client against this bridge, from the Android repository:
HERMES_BRIDGE_SRC=/path/to/hermione-bridge npm test
```

Fake-dashboard and fake-gateway results aren't evidence of live server behaviour.
