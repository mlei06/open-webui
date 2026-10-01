# Michael — reproducible Open WebUI stack

Deployment scaffold for the internal Open WebUI environment. No services are deployed yet.

Read [onboarding](docs/ONBOARDING.md) before implementation. This repository is public: internal documents must be safe for publication. Keep confidential documents and credentials outside tracked files.

## Layout

| Path | Purpose |
|---|---|
| `docs/` | Architecture, decisions, setup, and operating instructions |
| `mcp/mcp.json` | Declarative MCP inventory for future bootstrap |
| `tools/` | Reviewed tool definitions/adapters |
| `prompts/` | Versioned specialist system prompts |
| `models/` | Model/agent preset manifests |
| `bootstrap/` | Future authenticated, idempotent provisioning |
| `services/` | Local MCP/bridge/worker container build contexts |
| `tests/fixtures/` | Synthetic test files only |
| `runtime/` | Ignored certificates, secrets, local data |
| `.env.example` | Placeholder-only runtime configuration template |

`mcp/mcp.json` is a project-owned scaffold, not Pi configuration or an Open WebUI auto-import file. No loader or schema adapter exists yet. An empty inventory intentionally provisions no servers.

## Runtime setup

Copy `.env.example` to `.env` and supply private values locally. Store approved CA material under `runtime/certs/`. Do not disable TLS verification.

Compose configuration will be added after verifying service versions and bootstrap contracts. There is deliberately no pretend runnable Compose file in this scaffold.
