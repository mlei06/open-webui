# Michael — reproducible Open WebUI stack

Deployment scaffold for the internal Open WebUI environment.

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

1. Copy `.env.example` to `.env` and supply private values locally (`OPENAI_API_BASE_URLS` ending in `/v1`, `OPENAI_API_KEYS`, `WEBUI_SECRET_KEY`).
2. Copy the approved CA bundle to `runtime/certs/davy-ca-bundle.pem`. `runtime/` and `.env` are gitignored. `AIOHTTP_CLIENT_SSL_CERT_FILE` in `.env.example` already points at the in-container mount path. Do not disable TLS verification.
3. From the repository root, start the stack:

```
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up --build -d
```

`docker-compose.yaml` runs Open WebUI only (no Ollama; `ENABLE_OLLAMA_API=false`), loads `Michael/.env`, mounts `runtime/certs` read-only at `/certs`, and configures the model connection through `OPENAI_API_BASE_URLS`/`OPENAI_API_KEYS`. It reuses the external Docker volume `open-webui_open-webui` (override with `OPEN_WEBUI_VOLUME`), so create it first on a fresh host (`docker volume create open-webui_open-webui`).

Open WebUI persists connection settings in its database after first start; on an existing volume those saved values win over these environment variables, so change them in Admin Settings (or set `ENABLE_PERSISTENT_CONFIG=false`).
