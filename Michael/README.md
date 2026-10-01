# Michael — reproducible Open WebUI stack

Deployment scaffold for the internal Open WebUI environment.

Read [onboarding](docs/ONBOARDING.md) before implementation. This repository is public: internal documents must be safe for publication. Keep confidential documents and credentials outside tracked files.

## Layout

| Path | Purpose |
|---|---|
| `docs/` | Architecture, decisions, setup, and operating instructions |
| `mcp/mcp.json` | Declarative MCP inventory for future bootstrap |
| `tools/` | Reviewed tool definitions/adapters (`document_translator.py`) |
| `prompts/` | Versioned specialist system prompts |
| `models/` | Model/agent preset manifests |
| `bootstrap/` | Authenticated, idempotent provisioning (`davy_connection.py`, `translator_tool.py`) |
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

## Bootstrap: Davy connection

`bootstrap/davy_connection.py` makes the running Open WebUI actually use `OPENAI_API_BASE_URLS`/`OPENAI_API_KEYS`, even on an existing data volume where saved settings override the environment. Through the admin API it adds or updates the connection (matched by URL, so re-runs never duplicate it; other connections are kept), disables the Ollama API in the persisted config, then verifies the connection and that models are listed. It prints a short PASS/FAIL per step and exits non-zero on failure. It never prints keys, passwords, tokens or API responses. Python standard library only.

1. Start the stack (above) and create the admin account in the UI if none exists.
2. Supply admin credentials in `Michael/.env` (placeholders in `.env.example`) or export them in your shell (shell values win, and keep them out of the container, since compose loads `.env` into it): either `OPEN_WEBUI_ADMIN_EMAIL` + `OPEN_WEBUI_ADMIN_PASSWORD`, or `OPEN_WEBUI_ADMIN_API_KEY` (requires `ENABLE_API_KEYS=true`). Set `OPEN_WEBUI_URL` if Open WebUI is not at `http://localhost:$OPEN_WEBUI_PORT`.
3. Run it from the repository root:

```
python3 Michael/bootstrap/davy_connection.py
```

`AIOHTTP_CLIENT_SSL_CERT_FILE` is the path **inside the container** (`/certs/davy-ca-bundle.pem`), not a host path. The script only checks that the matching file exists under `Michael/runtime/certs/`; the real test of trust is the verify step, which Open WebUI performs from inside the container with that bundle. A verification failure usually means a wrong key or an untrusted/missing CA bundle.

## Bootstrap: document translator

`bootstrap/translator_tool.py` provisions document translation through the translator MCP gateway. Same conventions as `davy_connection.py` (admin credentials, PASS/FAIL output, secrets never printed, re-runs change nothing, standard library only). Through the admin API it:

1. creates or updates the workspace tool `tools/document_translator.py` and sets its valves (gateway URL and the shared translator API key), with a public read grant;
2. registers the gateway as a native MCP tool server (`type: mcp`, bearer auth, shared key) exposing only `translation_capabilities`, `get_translation_status` and `cancel_translation` (the base64-carrying tools are filtered out so the model cannot pass file contents through the model context), then verifies it lists them;
3. registers the base model with a public read grant (a non-admin user cannot use a preset whose base model has no registered row) and creates or updates the **Document Translator** preset: file context off, native function calling, built-in file and knowledge tools off, both tools attached, public read access.

Supply in `Michael/.env` (placeholders in `.env.example`): `TRANSLATOR_GATEWAY_URL` (the gateway `/mcp` URL **as reachable from the Open WebUI container**, e.g. `http://host.docker.internal:8766/mcp`; the gateway must listen on an interface the container can reach and allow that Host), `TRANSLATOR_API_KEY` (one shared key for all Open WebUI users, so users share one translator identity), `TRANSLATOR_BASE_MODEL` (a model id listed by Open WebUI), optional `TRANSLATOR_ID`. Then, with the admin credentials from the Davy section:

```
python3 Michael/bootstrap/translator_tool.py
```

How a translation works: the user attaches a file in a chat with the preset. With file context off, the model sees only an `<attached_files>` tag with the attachment id, and calls `translate_attachment(file_id, target_language)`. The tool runs inside Open WebUI: it checks the caller may access the file, reads the bytes from Open WebUI's file store, submits them to the gateway, polls, verifies the checksum of the result, stores it through the Files API with the caller's own token (`POST /api/v1/files/?process=false`), then emits a `files` event and returns a markdown download link. File bytes are never part of any model request. If a job outlives the tool call (`MAX_WAIT_SECONDS` valve, 240 s default) the tool returns the job id and `deliver_translation(job_id)` fetches it later.

Limits: files over 8 MiB are refused with a clear message (the gateway's inline limit); the gateway returns only a generic message when the translator rejects a submission (for example HTTP 422); job ids are not scoped per user because all users share one translator key. The key is stored in the tool valves and the MCP connection (admin-readable) and is loaded into the container by compose through `.env`.
