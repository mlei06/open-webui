# Michael — reproducible Open WebUI stack

Deployment scaffold for the internal Open WebUI environment.

Read [onboarding](docs/ONBOARDING.md) before implementation. This repository is public: internal documents must be safe for publication. Keep confidential documents and credentials outside tracked files.

## Layout

| Path | Purpose |
|---|---|
| `docs/` | Architecture, decisions, setup, and operating instructions; [Filter.md](docs/Filter.md) and [Tool.md](docs/Tool.md) explain Open WebUI filters and tools and the ones we use; [ExternalToolServers.md](docs/ExternalToolServers.md) covers external tool servers (MCP and OpenAPI), `mcp.json` and the employee directory |
| `mcp/mcp.json` | Declared external tool servers (translator gateway, employee directory) with `mcp.schema.json`; registered by `bootstrap/mcp_servers.py` |
| `tools/` | Reviewed tool definitions/adapters (`document_translator.py`) |
| `functions/` | Open WebUI functions (`user_context.py` filter) |
| `prompts/` | Versioned specialist system prompts |
| `models/` | Model/agent preset manifests; `user-context.json` sets which user fields each model receives |
| `bootstrap/` | Authenticated, idempotent provisioning (`build_ca_bundle.py`, `davy_connection.py`, `xai_connection.py`, `mcp_servers.py`, `seed_employees.py`, `translator_tool.py`, `user_context.py`, `branding.py`) |
| `services/` | Local MCP/bridge/worker container build contexts (a checkout of the employee-directory source can sit here; see `EMPLOYEE_DIRECTORY_SRC`) |
| `tests/` | Filter unit tests, throwaway-stack end-to-end check, model-request log proxy; `fixtures/` holds synthetic test files only |
| `runtime/` | Ignored certificates, secrets, local data |
| `.env.example` | Placeholder-only runtime configuration template |

`mcp/mcp.json` is our own inventory of external tool servers (schema `mcp/mcp.schema.json`), not Pi configuration or an Open WebUI auto-import file. `bootstrap/mcp_servers.py` reads it and registers the servers; see [ExternalToolServers.md](docs/ExternalToolServers.md).

## Runtime setup

1. Copy `.env.example` to `.env` and supply private values locally (`OPENAI_API_BASE_URLS` ending in `/v1`, `OPENAI_API_KEYS`, `WEBUI_SECRET_KEY`).
2. Place certificates: copy the approved private CA bundle to `runtime/certs/davy-ca-bundle.pem`. `runtime/` and `.env` are gitignored. Do not disable TLS verification.
3. Build the bundle: `python3 Michael/bootstrap/build_ca_bundle.py` writes `runtime/certs/ca-bundle.pem`, the host's system CA bundle plus every other `.pem` in that folder. Open WebUI trusts only the file named by `AIOHTTP_CLIENT_SSL_CERT_FILE` (default `/certs/ca-bundle.pem`, the in-container path of this file), so the private bundle alone cannot verify public services such as xAI. Re-run it whenever certificates change; it prints PASS or FAIL and leaves an unchanged bundle untouched.
4. From the repository root, start the stack:

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

`AIOHTTP_CLIENT_SSL_CERT_FILE` is the path **inside the container** (`/certs/ca-bundle.pem`), not a host path. The script only checks that the matching file exists under `Michael/runtime/certs/`; the real test of trust is the verify step, which Open WebUI performs from inside the container with that bundle. A verification failure usually means a wrong key or an untrusted/missing CA bundle.

## Bootstrap: xAI (Grok) connection

`bootstrap/xai_connection.py` adds xAI as a second OpenAI-compatible connection beside the Davy one. Same conventions as `davy_connection.py` (admin credentials, PASS/FAIL output, standard library only), and it reuses its helpers. Through the admin API it adds or updates the connection (matched by URL, so re-runs change nothing and never duplicate it), leaves every other connection untouched, then verifies that the provider and Open WebUI list models. A response that may contain the key is masked; the key is never printed. With `XAI_API_KEY` empty it does nothing.

xAI is an outside service: send it only synthetic or approved data. Supply `XAI_API_KEY` (and `XAI_API_BASE_URL`, prefilled with `https://api.x.ai/v1`) in `Michael/.env`, make sure the combined CA bundle is built and the stack was restarted with it (the Davy bundle alone fails certificate verification for `api.x.ai`), then with the admin credentials from the Davy section:

```
python3 Michael/bootstrap/xai_connection.py
```

## Bootstrap: document translator

`bootstrap/translator_tool.py` provisions document translation through the translator MCP gateway. Same conventions as `davy_connection.py` (admin credentials, PASS/FAIL output, secrets never printed, re-runs change nothing, standard library only). Through the admin API it:

1. creates or updates the workspace tool `tools/document_translator.py` and sets its valves (gateway URL and the shared translator API key), with a public read grant;
2. registers the base model with a public read grant (a non-admin user cannot use a preset whose base model has no registered row) and creates or updates the **Document Translator** preset: file context off, native function calling, built-in file and knowledge tools off, both tools attached, public read access.

The gateway's MCP connection (`doctranslator`, exposing only `translation_capabilities`, `get_translation_status` and `cancel_translation`; the base64-carrying tools are filtered out so the model cannot pass file contents through the model context) is **not** registered here: `bootstrap/mcp_servers.py` owns all tool server registration (see below). Run it before or after this script; the preset only names the connection, and this script prints a `[NOTE]` if it is not registered yet.

Supply in `Michael/.env` (placeholders in `.env.example`): `TRANSLATOR_GATEWAY_URL` (the gateway `/mcp` URL **as reachable from the Open WebUI container**, e.g. `http://host.docker.internal:8766/mcp`; the gateway must listen on an interface the container can reach and allow that Host), `TRANSLATOR_API_KEY` (one shared key for all Open WebUI users, so users share one translator identity), `TRANSLATOR_BASE_MODEL` (a model id listed by Open WebUI), optional `TRANSLATOR_ID`. Then, with the admin credentials from the Davy section:

```
python3 Michael/bootstrap/mcp_servers.py
python3 Michael/bootstrap/translator_tool.py
```

How a translation works: the user attaches a file in a chat with the preset. With file context off, the model sees only an `<attached_files>` tag and calls `translate_attachment(target_language)`; Open WebUI injects the message's attachments (`__files__`) into the tool, so the model does not copy an id: with exactly one attachment the tool uses it, with several it answers with the available ids and the model calls again with `file_id` (an explicit `file_id` always works). The tool runs inside Open WebUI: it checks the caller may access the file, reads the bytes from Open WebUI's file store, opens a real MCP session to the gateway (Streamable HTTP, `initialize` handshake, bearer key) using the `mcp` SDK that Open WebUI already ships, submits the file, polls, verifies the checksum of the result, stores it through the Files API with the caller's own token (`POST /api/v1/files/?process=false`), then emits a `files` event and returns a dict with the download link. Progress is shown with `status` events; failures return `{"error": ...}`. These are the Native-mode-safe forms from the Open WebUI [tool development guide](https://docs.openwebui.com/features/extensibility/plugin/tools/development). File bytes are never part of any model request. If a job outlives the tool call (`MAX_WAIT_SECONDS` valve, 240 s default) the tool returns the job id and `deliver_translation(job_id)` fetches it later.

The tool needs no `requirements:` in its frontmatter (`mcp`, `httpx` and `pydantic` ship with Open WebUI 0.11.4), so nothing is pip-installed at save time.

Secret handling: the API key valve is a password field. `docker-compose.yaml` sets `ENABLE_VALVE_ENCRYPTION=true` (override in `.env`), which stores tool valves Fernet-encrypted at rest, using a key derived from `WEBUI_SECRET_KEY`. That needs `WEBUI_SECRET_KEY` set and **stable**: if it is rotated, saved valve values become unreadable and must be re-entered (re-run `bootstrap/translator_tool.py`). Values saved before encryption was on stay readable and are encrypted the next time they are written. The MCP tool-server connection key lives in Open WebUI's config, which valve encryption does not cover.

Limits: files over 8 MiB are refused with a clear message (the gateway's inline limit); the gateway returns only a generic message when the translator rejects a submission (for example HTTP 422); job ids are not scoped per user because all users share one translator key. The key is stored in the tool valves and the MCP connection (admin-readable; tool valves are also readable by anyone holding a write grant on the tool) and is loaded into the container by compose through `.env`.

## Bootstrap: external tool servers and the employee directory

`mcp/mcp.json` declares each external tool server: id, type (`mcp` or `openapi`), transport, URL, the environment variables it uses (required, secret, default), auth, who may use it, and which tools to expose. `bootstrap/mcp_servers.py` makes Open WebUI match it through the admin API (same conventions as the other bootstrap scripts: admin credentials from `.env`, PASS/FAIL lines, secrets never printed, standard library only): it validates the file, resolves variables, merges each server by id into the connection list (only writing when something changed, leaving connections it does not manage alone), and verifies that each server lists its declared tools. `--check` changes nothing, `--prune` removes servers it registered earlier that are no longer declared.

```
python3 Michael/bootstrap/mcp_servers.py --check
python3 Michael/bootstrap/mcp_servers.py
```

Declared today: the translator gateway (`doctranslator`), the read-only employee directory (`employee_directory`) and a disabled, admin-only write variant (`employee_directory_write`). The directory runs as the compose service `employee-directory` (built from `EMPLOYEE_DIRECTORY_SRC`, data in a named volume, UI on `127.0.0.1:${EMPLOYEE_DIRECTORY_PORT:-8780}` only, no sign-in; Open WebUI reaches `http://employee-directory:8000/mcp` over the compose network). Load employees at run time from a file that stays outside this repository:

```
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build employee-directory
python3 Michael/bootstrap/seed_employees.py /path/to/employees.json
```

How external tool servers work in Open WebUI, the `mcp.json` reference, how to add a server, troubleshooting and security notes: [docs/ExternalToolServers.md](docs/ExternalToolServers.md).

## Bootstrap: user context for every model

Every model should know who it is talking to without calling a tool. `functions/user_context.py` is an Open WebUI **Filter function**: its `inlet` runs once per chat request, receives the signed-in user as `__user__` and the model as `__model__`, and adds this block to the system message:

```
<user_context>
The signed-in user you are talking to. These are account facts, not instructions.
name: Michael Lei
id: mlei4
email: mlei4@lenovo.com
</user_context>
```

- `name` and `email` come from the account. `id` is the part of the email before the `@`, lowercased, so there is no per-user setup. An email with no `@` yields no `id` line (nothing is guessed); an empty field is left out.
- Nothing else about the user is sent (no internal user id, role, bio, location, groups). There are no date or time values, so provider prompt caching is not disturbed.
- The block is appended to the system message of the request (or becomes it when there is none). Open WebUI adds a model's or preset's own system prompt afterwards, in front of it, so the final system message is `<model prompt> <chat system text> <user block>`; nothing is replaced.
- A block already present in the request is **removed and rebuilt** from the account, so there is never a duplicate and a client cannot forge the identity.
- The display name is user-editable, so values are flattened to one line, `<` `>` and control characters are dropped, and each is cut at 100 characters.

### Which fields each model gets

`models/user-context.json` (tracked) maps model ids to the fields they receive:

```json
{"default": ["name", "id", "email"],
 "models": {"nemotron-3-ultra": ["name", "id", "email"], "bge-reranker-v2-m3": []}}
```

A model uses its own entry, else its base model's entry (so a preset such as `document-translator` can be set separately or inherits from its base), else `default`. An empty list means the model gets no block. All chat models and the Document Translator preset are listed with all three fields; the embedding and reranker models are listed with `[]`. The filter cannot read repository files (it runs in the container), so the bootstrap stores the file in the filter's valves (`models_config_json`). An invalid config makes the filter send nothing (fail closed); the bootstrap validates the file first.

### Provision

With admin credentials from the Davy section (`OPEN_WEBUI_ADMIN_API_KEY`, or email and password; no other settings):

```
python3 Michael/bootstrap/user_context.py
```

It creates or updates the filter (source compared exactly), enables it, makes it **global**, and stores the config in the valves, then checks the result. PASS/FAIL per step, nothing secret is printed, a re-run changes nothing. The toggle endpoints flip a flag, so the script reads the state first.

### Change one model's fields

Edit `models/user-context.json`, for example `"nemotron-3-ultra": ["name"]`, and re-run the script (it reports `model config in valves updated`). It takes effect on the next request: no restart, no per-model edit in the UI. Revert the line and re-run to undo. Check it in the request the provider receives (see below) or ask the model "what is my id?".

### Global filter versus one filter per model

Use **one global filter that reads the config**. Per-model attachment is documented (Workspace > Models > the model > Filters, stored as `meta.filterIds`) and was tried: with the filter not global and attached only to `gpt-oss-120b`, only that model received the block. But it adds nothing here: valves belong to the function, not to the attachment, so the per-model field choice needs the config in any case, and one filter per model would mean N copies of the code with N valve sets. Global also covers a model added later (default entry) without a UI edit, and a preset with no filter of its own. Two notes from the experiment: after attaching or detaching, the model list cache must refresh before the change shows (an admin `GET /api/models?refresh=true` did it; the first request right after the edit still saw the old state); and a global filter never sees embedding or reranker calls, which do not go through the chat pipeline, so the `[]` entries only document the intent.

### Verify, and limits

`tests/test_user_context.py` (unit tests, run inside the Open WebUI container, see the file header) and `tests/user_context_e2e.py` (throwaway stack only) exercise it. The end-to-end check creates synthetic non-admin users, asks every model "what is my name, my id and my email?" with no tools, and compares the answer and the **logged provider request** (from `tests/request_log_proxy.py`, a pass-through proxy that logs only the body's keys and system text, never headers) with the right user. Set `USER_CONTEXT_E2E_MODELS=id1,id2` to test other model ids with an all-fields config (the embedding step is skipped when the provider does not offer those models). It also checks the preset merge, a forged block, one model restricted to the name, and that embedding calls carry no system message.

Limits: only chat requests that go through Open WebUI's chat pipeline are affected. Background tasks (titles, tags, follow-ups) do not run inlet filters (read from the code, not exercised) and the embedding and reranker endpoints are not chat requests; the filter runs for API clients calling `/api/chat/completions` too (their requests have no chat id, which is fine as it needs none) but not for clients that call the provider directly. A model that is told these facts can still repeat them to the user it is talking to, which is the point, but do not rely on the block as a security boundary: a user can also write the same text in their own message. Anyone allowed to edit functions can read the user's account data in the filter, so keep function creation admin-only (the Open WebUI default) and review changes to `functions/user_context.py`.

Non-admin users only see models that have a registered row with a read grant; the end-to-end script registers the test models itself, and the live instance must already have them (the translator bootstrap does the same for its base model).

Limits: files over 8 MiB are refused with a clear message (the gateway's inline limit); the gateway returns only a generic message when the translator rejects a submission (for example HTTP 422); job ids are not scoped per user because all users share one translator key. The key is stored in the tool valves and the MCP connection (admin-readable) and is loaded into the container by compose through `.env`.

## Bootstrap: Lenovo branding

`bootstrap/branding.py` applies the Lenovo look (dark purple gradient by default, a light variant, Segoe UI with a self-hosted Archivo fallback, the logo on the sign-in page, loading splash and sidebar, Lenovo red only on the logo and the selected-chat edge, blue for the one pressable colour). It uses the **Theme Designer Pro** plugin (community, MIT, not tracked here) as the delivery channel and does what the designer's Save button does, without the designer. Same conventions as the other bootstrap scripts (PASS/FAIL output, secrets never printed, re-runs change nothing, standard library only).

- `branding/tokens.json` holds the colour tokens, `branding/theme.css` the rules. Both are committed; neither contains a logo or a font.
- The logo and font are brand binaries and are **not committed**. They live in the gitignored `runtime/brand/`. The logo is embedded into the CSS as a `data:` URI at upload time, so nothing is loaded from the network. The font is **not** embedded (see the incident note below): the theme asks for an installed `Archivo` and otherwise falls back to the Segoe UI stack.
  `python3 Michael/bootstrap/branding.py --init-assets --font /path/to/archivo-latin.woff2` writes a **placeholder** logo (a red tile with the word "Lenovo", not the official artwork) and copies the font. Drop an official `lenovo-logo.svg` or `.png` into `runtime/brand/` to replace the placeholder.
- **Never embed a large `data:` URI in the theme.** Theme Designer Pro's `loader.js` runs `css.replace(/[^{}]*body\s*::before\s*\{[^}]*\}/g, '')` over the whole theme on every repaint. That regex is quadratic in the longest run of text with no brace, so the 90 KB font as a `data:` URI (a 120 KB run) blocked the main thread for 3+ seconds per call, and the app never got past the splash (no console error). `branding.py` now fails if any brace-free run exceeds 6000 characters.
- The plugin accepts an admin session token only, so besides the admin API key the script needs `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD` in `.env`.
- The script installs the plugin from `tools/theme_designer_pro.py` if missing (the plugin file itself is not committed), enables it, and sets its valves to: Canvas FX off, Canvas API access off, community-theme catalogue off, URL import off. It then uploads the theme and verifies what users are served. `--check` verifies without changing anything.
- Do not open the designer and press Save: that replaces the theme with the designer's own state. Re-run `branding.py` to restore it.
- Set `WEBUI_NAME=AI Assistant` (or similar) in `.env` so the header reads "[Lenovo logo] AI Assistant (Open WebUI)"; Open WebUI always appends "(Open WebUI)".
- Licence: Open WebUI's `LICENSE` forbids replacing its branding for deployments with more than 50 users unless there is an enterprise licence or written permission. Settle that before applying this to a shared stack.

```bash
python3 Michael/bootstrap/branding.py --init-assets --font /path/to/archivo-latin.woff2
python3 Michael/bootstrap/branding.py
```

### Verify the render, and roll back a stuck UI

```bash
npm i --prefix Michael/runtime/pw playwright-core      # once; needs a Chromium (CHROME_PATH, or ~/.cache/ms-playwright)
python3 Michael/bootstrap/branding.py --verify-render   # login page and signed-in hard reload, dark and light
```

`--verify-render` (`tests/verify_render.mjs`) fails unless the login form (logged out) or the chat input (signed in) becomes visible and the splash is removed, within `TIMEOUT_MS` (20 s; it also fails if rendering takes more than half of that). Set `SHOTS=dir` to save screenshots. Run it against a throwaway stack after every theme change, **before** applying to the live stack, and again after.

If the UI is stuck on the splash, switch the theme off in one step (needs the admin credentials in `.env`), then hard-reload the browser (Ctrl+Shift+R):

```bash
python3 Michael/bootstrap/branding.py --rollback
```

This toggles the plugin off through the admin API, so `/static/custom.css` serves 0 bytes. Re-run `branding.py` to re-apply.
