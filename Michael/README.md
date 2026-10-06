# Michael — reproducible Open WebUI stack

Deployment scaffold for the internal Open WebUI environment.

Read [onboarding](docs/ONBOARDING.md) before implementation. This repository is public: internal documents must be safe for publication. Keep confidential documents and credentials outside tracked files.

## Layout

| Path | Purpose |
|---|---|
| `docs/` | Architecture, decisions, setup, and operating instructions; [Filter.md](docs/Filter.md), [Tool.md](docs/Tool.md) and [Events.md](docs/Events.md) explain Open WebUI filters, tools and event functions and the ones we use; [ExternalToolServers.md](docs/ExternalToolServers.md) covers external tool servers (MCP and OpenAPI), `mcp.json` and the employee directory |
| `mcp/mcp.json` | Declared external tool servers (translator gateway, employee directory, mail, QDTS cases) with `mcp.schema.json`; registered by `bootstrap/mcp_servers.py` |
| `tools/` | Reviewed tool definitions/adapters (`document_translator.py`, `knowledge_base_manager.json` tool export; `generate_slides.py` and `generate_documents.py`, the Lenovo-styled PowerPoint and Word generators) |
| `knowledge/` | Seed knowledge bases: `manifest.json` names each knowledge base, its description and files; the Markdown files (`sops/`) are the committed copy |
| `functions/` | Open WebUI functions (`user_context.py` filter, `audit_log.py` event function) |
| `prompts/` | Versioned system prompts, one file per preset |
| `models/` | `presets.json` declares the model presets; `user-context.json` sets which user fields each model receives |
| `bootstrap/` | Authenticated, idempotent provisioning: `provision.py` is the one entry point and runs the others in order (`init_env.py`, `accounts.py`, `access.py`, `smtp_check.py`, `build_ca_bundle.py`, `davy_connection.py`, `xai_connection.py`, `mcp_servers.py`, `presets.py`, `knowledge_bases.py`, `kb_manager_tool.py`, `pptx_to_markdown.py`, `seed_employees.py`, `translator_tool.py`, `office_tools.py`, `user_context.py`, `audit_log.py`, `branding.py`) |
| `services/` | Local MCP/bridge/worker container build contexts (a checkout of the employee-directory source can sit here; see `EMPLOYEE_DIRECTORY_SRC`) |
| `tests/` | Unit tests, throwaway-stack end-to-end checks (`stack_e2e.py`, `user_context_e2e.py`, `knowledge_e2e.py`), model-request log proxy; `fixtures/` holds synthetic test files only |
| `runtime/` | Ignored certificates, secrets, local data |
| `.env.example` | Placeholder-only runtime configuration template |

`mcp/mcp.json` is our own inventory of external tool servers (schema `mcp/mcp.schema.json`), not Pi configuration or an Open WebUI auto-import file. `bootstrap/mcp_servers.py` reads it and registers the servers; see [ExternalToolServers.md](docs/ExternalToolServers.md).

## Internal cases: rollout approval first

[QDTS cases](docs/QDTS_CASES.md) describes the new compose service/indexer, shared-key access to **all private notes**, Davy-only plain-customer-name guard, synthetic validation and the targeted no-downtime rollout. Both **Lenny** and the new **Case Assistant** receive its three read-only tools. Configure the approved source checkout/data folder and build the index before starting the cases service. Provisioning now refuses an xAI key, outside saved connection or non-Gemma preset base; do not apply this deployment to a mixed-model environment. No runtime DLP is implied. Existing live env/settings must be reviewed privately by the operator first.

## Restart the stack (the one path)

From the repository root, with the private `Michael/.env` in place (copy `.env.example`; the owner's file already has the Davy, xAI, Perplexity, translator, SMTP and admin values):

```
python3 Michael/bootstrap/provision.py --init-env
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build
python3 Michael/bootstrap/provision.py
```

On a host that has never run the stack, create the data volume first: `docker volume create open-webui_open-webui` (compose expects it to exist; `OPEN_WEBUI_VOLUME` renames it).

1. **`--init-env`** (`bootstrap/init_env.py`) only ever **adds** what is missing to `Michael/.env` and never overwrites or prints a value (names only; the file is written mode 600). It adds `MAIL_SERVICE_SRC` (the mail-service clone, found at `~/github/firstmate/projects/mail-service`, `$FIRSTMATE_HOME/projects/mail-service`, next to this repository or in `~/projects`; or pass `--mail-src PATH`), `EMPLOYEE_DIRECTORY_SRC` the same way (`--employee-src`), `MAIL_PROVIDER` (`smtp` when `SMTP_HOST` is set, otherwise `mock`), `MAIL_SMTP_TLS_VERIFY=false` for the smtp provider (**risk below**) a generated random `WEBUI_SECRET_KEY` (48 random bytes, urlsafe; see Session key below) and a generated random `MAIL_MCP_API_KEY`. It also adds `QDTS_CASES_SRC` (devqdts checkout, `--cases-src PATH`), `QDTS_MCP_API_KEY` and `QDTS_CUSTOMER_NAMES=plain`, but never discovers the approved data folder. That one mail variable is what compose passes to the mail service **and** what `mcp/mcp.json` sends when it registers the mail connection, so the two always match. The base model is not written: the default is Gemma, see Model presets.
2. **`docker compose ... up -d --build`** starts Open WebUI, the employee directory, the mail service and QDTS cases (which needs its derived index initialized first; see the case rollout). The mail service runs with the provider from `.env`; with `smtp` it uses `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USERNAME` and `SMTP_PASSWORD`, and its MCP endpoint refuses everything unless the request carries `MAIL_MCP_API_KEY`.
3. **`provision.py`** runs the bootstrap scripts below in dependency order against the running stack and ends with a read-only access audit. It is idempotent (a re-run changes nothing), prints PASS / FAIL / NOTE per step, never prints a secret, and `--check` changes nothing and exits 1 if anything differs. Steps: wait for `/health`; **session key** (a NOTE when the running container has no `WEBUI_SECRET_KEY`); **first admin** (on a fresh volume it creates the account from `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`, which Open WebUI makes the admin even though sign-up is closed; otherwise it signs in with them); `accounts.py`; Davy and xAI model connections; the tool servers (translator gateway, employee directory, mail, cases, PATH); the user context filter; the audit function; the translator, Knowledge Base Manager and office generator tools; the knowledge bases; the presets, the Review and send email action and web search; the Lenovo branding; the mail relay check; the access audit. An **unreachable Davy host is a NOTE**, not a failure, and provisioning continues. Branding is a NOTE when the private plugin file `tools/theme_designer_pro.py` or `runtime/brand/lenovo-logo.svg` is not in the checkout. `--only a,b` / `--skip a,b` select steps, `--employees FILE` also loads the employee directory, `--update-knowledge` overwrites seed files edited in the app, `--env-file FILE` uses another env file.

**Mail relay check.** `bootstrap/smtp_check.py` (a provision step when `MAIL_PROVIDER=smtp`, or run it alone) resolves the relay, connects, says EHLO, upgrades with STARTTLS, authenticates and quits. It never issues MAIL FROM, so **no message is ever sent**. A relay it cannot reach (not on the company network or VPN) is a NOTE, an environment limit and not a wiring fault; a rejected login or a failed certificate check is a FAIL. The first real test send is done by hand, to a recipient you choose, through the Review and send email button.

**MAIL_SMTP_TLS_VERIFY=false, the risk.** The relay's certificate cannot be verified (an internal authority and no DNS name in it), so certificate checking is turned off for it by decision of the owner. STARTTLS is still required and there is no plaintext fallback, so the connection is encrypted, but anyone who can intercept the connection to the relay can pose as it and read the SMTP credentials and every message. Use it only on a network you trust. The cleaner fix is `MAIL_SMTP_CA_FILE` with the issuing authority's certificate in `runtime/certs/` (then delete the false line). The mail service logs a warning at start while verification is off.

**Sender.** The From address is the signed-in user's own company address (`MAIL_ALLOWED_DOMAINS`, `lenovo.com`); anyone else, such as an account without a company address, sends as `MAIL_SHARED_SENDER` (`lenny@lenovo.com`). The relay accepts a per-user From. There is no redirect switch (`MAIL_REDIRECT_ALL` is `off`).

**Session key.** `WEBUI_SECRET_KEY` signs Open WebUI's sign-in sessions and is the source of the tool-key encryption (`ENABLE_VALVE_ENCRYPTION`). `--init-env` adds a generated one to `.env` when it is missing or empty and never overwrites or prints one; compose passes it to the container with the rest of `.env`. Without it, every container restart can make a new key: browsers keep a sign-in the server no longer accepts (the page hangs on its loading screen) and saved tool keys, such as the translator key, become unreadable. `provision.py` and `provision.py --check` print a NOTE (not a failure) when the running container has none (found through docker by the published port). **After a key change** (a first `--init-env` on an existing stack, or any edit of the value) recreate the container (`docker compose ... up -d`); everyone is signed out once, and a browser that hangs on the loading screen needs a one-time sign-out: open the site's cookies/storage and clear it (or use a private window). Re-run `bootstrap/translator_tool.py` to re-enter the translator key. Keep the value stable after that.

**Secrets.** SMTP and API secrets stay in the private `Michael/.env` (gitignored, mode 600). Compose loads that whole file into the Open WebUI container (`env_file`), so everything in it is readable inside that container by anyone with admin or shell access to it; there is no per-service split.

**Tests.** Unit tests (standard library): `python3 Michael/tests/test_provision.py` (init-env, the SMTP check, provision), `test_presets.py`, `test_icons.py`, `test_mcp_servers.py`, `test_knowledge_bases.py`, `test_office_tools.py` (see its header for the packages). `Michael/tests/stack_e2e.py` is the end-to-end proof for a **throwaway** stack (own compose project name, port and volume; mock mail provider): it creates ordinary users through the admin path and checks sign-up is refused, every preset and tool is visible, chats through every preset (through the web UI's real tool loop, `socket_chat.py`), privacy between users, the Knowledge Base Manager confirmation dialog and mail drafting, attaching and sending with the right sender.

### Accounts

Sign-up is closed (`ENABLE_SIGNUP=false`, saved in Open WebUI by `accounts.py` too, because saved settings override the environment) and the default role is `user`. Only an admin creates accounts: **Admin Panel > Users > Add User** (name, email, password, role `user`), or **CSV Import** on the same dialog with a file whose first row is a header and whose other rows are `Name,Email,Password,Role` (role `admin`, `user` or `pending`; Open WebUI's template is at `/static/user-import.csv`). There is no script in this repository for a users file, so a private `runtime/users.csv` in that format is imported through that dialog; keep it out of git. The first account on a fresh volume is the one `provision.py` creates from `OPEN_WEBUI_ADMIN_EMAIL`: Open WebUI lets the first sign-up through whatever `ENABLE_SIGNUP` says, and every later sign-up gets HTTP 403.

### Visible to every user

Provisioning grants, and `access.py` audits (read-only, last step of `provision.py`), access for **all users** to: every model preset and the base model (public read, so they are in everyone's model selector), every workspace tool (public read), every tool server connection (public read in the connection config), the SOPs knowledge base (public read and write, so users can add and edit files; nothing here ever deletes a user's addition), and the Review and send email action (active, not global, attached to the models that list it, so it shows under Lenny and Office Agent answers for everyone). The user context filter is global. No object type is limited to admins: Open WebUI can grant all-user access to each of them. What stays private is what users create: their own knowledge bases and files, which the tools reach only with the signed-in user's own token. `tests/stack_e2e.py` proves it on a throwaway stack with a second, ordinary user.

## Runtime setup

1. Copy `.env.example` to `.env` and supply private values locally (`OPENAI_API_BASE_URLS` ending in `/v1`, `OPENAI_API_KEYS`; `WEBUI_SECRET_KEY` is generated by `--init-env`).
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

**Not compatible with the installed plain-name case integration:** first remove case tool access/presets under an approved configuration change. The case provisioning guard refuses `XAI_API_KEY` and saved outside connections. xAI is an outside service: send it only synthetic or approved data. Supply `XAI_API_KEY` (and `XAI_API_BASE_URL`, prefilled with `https://api.x.ai/v1`) in `Michael/.env`, make sure the combined CA bundle is built and the stack was restarted with it (the Davy bundle alone fails certificate verification for `api.x.ai`), then with the admin credentials from the Davy section:

```
python3 Michael/bootstrap/xai_connection.py
```

## Bootstrap: document translator

`bootstrap/translator_tool.py` provisions document translation through the translator MCP gateway. Same conventions as `davy_connection.py` (admin credentials, PASS/FAIL output, secrets never printed, re-runs change nothing, standard library only). Through the admin API it:

1. creates or updates the workspace tool `tools/document_translator.py` and sets its valves (gateway URL and the shared translator API key), with a public read grant;
2. registers the base model with a public read grant (a non-admin user cannot use a preset whose base model has no registered row). The **Document Translator** preset that attaches this tool is created by `bootstrap/presets.py` (see Model presets).

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

Declared today: the translator gateway (`doctranslator`), mail (`mail`), QDTS cases (`qdts`), the read-only employee directory (`employee_directory`) and a disabled, admin-only write variant (`employee_directory_write`). The directory runs as the compose service `employee-directory` (built from `EMPLOYEE_DIRECTORY_SRC`, data in a named volume, UI on `127.0.0.1:${EMPLOYEE_DIRECTORY_PORT:-8780}` only, no sign-in; Open WebUI reaches `http://employee-directory:8000/mcp` over the compose network). Load employees at run time from a file that stays outside this repository:

```
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build employee-directory
python3 Michael/bootstrap/seed_employees.py /path/to/employees.json
```

How external tool servers work in Open WebUI, the `mcp.json` reference, how to add a server, troubleshooting and security notes: [docs/ExternalToolServers.md](docs/ExternalToolServers.md).

## Bootstrap: model presets

`models/presets.json` declares ready-made models; `bootstrap/presets.py` creates or updates them through the admin API (same conventions as the other scripts: admin credentials from `.env`, PASS/FAIL lines, secrets never printed, standard library only). Each preset has its system prompt (`prompts/<name>.md`), native function calling, its tool list, capabilities, built-in tools, default features and the `user_context` filter attached.

| Preset (id) | Tools | Web search | Notes |
|---|---|---|---|
| PATH assistant (`path`) | Nine read-only PATH mailroom tools (`path`); built-in time/user input only | off | package/mail custody, exact itcode or fuzzy receiver-name lookup; no knowledge bases; see [PATH rollout](docs/PATH_MCP.md) |
| Case Assistant (`case-assistant`) | QDTS cases (`qdts`); built-in time/user input only | off | case-only, name/id context, no SOPs or employee-directory dependency; see [case guide](docs/QDTS_CASES.md) |
| Lenny (`lenny`) | everything: QDTS cases (`qdts`), translation stack (`doctranslator` and Document Translator tool), employee directory, mail (with the Review and send email button), Lenovo-styled slides and Word generators, Knowledge Base Manager tool, built-in web search, time, files, knowledge | on by default | the all-in-one assistant; its prompt routes to each tool |
| Document Translator (`document-translator`) | `doctranslator` MCP connection and the Document Translator tool only | off | translation only; file context off: the model never sees document text |
| Web Searcher (`web-searcher`) | built-in web search and time only | on by default | web search only; sees only the user's name; answers with sources |
| Office Agent (`office-agent`) | employee directory and mail | off | directory and mail only; drafts mail for the user to review and send; can attach chat files through the mail tool |
| Knowledge Base Manager (`knowledge-base-manager`) | Knowledge Base Manager tool, built-in files, time | off | its tool only; turns an attached file into a knowledge base entry; deleting needs the user's own confirmation, see [Bootstrap: knowledge bases](#bootstrap-knowledge-bases) |
| Office Documents (`office-documents`) | the two generator tools of the next section | off | the two generators only; Lenovo-styled decks and Word documents; asks what is missing, then returns a download link; chat files are source material (file context on) |

**Existing presets by tool matrix** (x = attached; these presets also get SOPs and the user context filter). Additionally, `path` is attached to PATH assistant, Lenny and Office Agent; PATH assistant has no SOPs/built-in knowledge, web, files or mail actions. See [PATH rollout](docs/PATH_MCP.md). `qdts` is attached to Lenny and Case Assistant only; Case Assistant has no SOPs/built-in knowledge, and only time/user input plus the user context filter.

| Tool | Lenny | Document Translator | Web Searcher | Office Agent | Knowledge Base Manager | Office Documents |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Translator MCP (`doctranslator`) + Document Translator tool | x | x | | | | |
| Employee directory (`employee_directory`) | x | | | x | | |
| Mail drafts (`mail`) + Review and send email action | x | | | x | | |
| Web search (built in, Perplexity) | x | | x | | | |
| Slides generator (`generate_slide_pptx`) | x | | | | | x |
| Word generator (`generate_docx_documents`) | x | | | | | x |
| Knowledge Base Manager tool | x | | | | x | |
| Time / user input / files (built in) | time, user input, files | none | time | time, user input | time, user input, files | time, user input |

Except Case Assistant and PATH assistant (explicit empty overrides), every preset also gets the knowledge bases listed in the top-level `knowledge_bases` of `presets.json` (today `sops`, an id from `knowledge/manifest.json`; a preset can override the list with its own `knowledge_bases`) and the built-in knowledge tool, which is how a model with native function calling searches them. Knowledge a user attached to a preset in the app is kept. A base that is not created yet is a `[NOTE]`; run `knowledge_bases.py`, then re-run `presets.py`.

**Mail.** The `mail` connection (declared in `mcp/mcp.json`, registered by `mcp_servers.py`) is the draft-only MCP channel of the mail service: create, update, get, list and discard a draft, no send tool. It carries the key `MAIL_MCP_API_KEY` as a bearer token and the identity headers `X-User-Email`, `X-Chat-Id` and `X-Message-Id`, which Open WebUI fills per call (the `headers` entry of `mcp.json`); the service derives the From address from the user, never from the model: the user's own company address, otherwise `MAIL_SHARED_SENDER` (`lenny@lenovo.com`). There is no redirect switch (compose sets `MAIL_REDIRECT_ALL=off`). Sending is the **Review and send email** Action (`functions/mail_review.py`, declared under `actions` in `presets.json`, installed and activated by `presets.py`, attached only to Lenny and the Office Agent): the envelope button under an assistant message opens the chat's newest unsent draft in an editable form (From read-only, To, Cc, Subject, Message, and a tick list of the chat files the model suggested through `suggested_attachment_ids`). Pressing Send makes the Action upload the ticked files from Open WebUI's file store to the mail service and send, using the clicking user's own session token, which the model never sees. Attachment bytes never pass through the model; the service enforces its limits (5 files, 10 MB each, 20 MB total, executables and macro documents refused).

The compose service `mail-service` is built from a checkout of the mail-service repository (`MAIL_SERVICE_SRC` in `.env`), has no published port, keeps its data in the volume `mail-service-data`, and validates user sessions against `http://open-webui:8080`. `MAIL_PROVIDER=mock` (the default) records messages in `/data/mock_emails.jsonl` and sends nothing; set `MAIL_PROVIDER=smtp` in `.env` for real mail through the relay given by `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_USE_TLS` (read from `.env`, never printed or committed; `MAIL_SMTP_CA_FILE=/certs/ca-bundle.pem` makes the service verify the relay with the mounted bundle). Run `mcp_servers.py` before `presets.py`; otherwise the presets print a `[NOTE]` for the unregistered connection. Other unregistered tools or a missing filter are noted the same way.

**Web search** is Perplexity, through the Search API (`WEB_SEARCH_ENGINE=perplexity_search`; the older `perplexity` engine calls a chat endpoint Perplexity has retired and returns nothing). Compose passes `ENABLE_WEB_SEARCH=true`, the engine and `PERPLEXITY_API_KEY` from `.env` to Open WebUI, so a fresh volume starts with search working. Open WebUI keeps saved settings in its database, which override the environment, so `presets.py` also saves the same three settings through the admin API when they differ (and does nothing when they already match). Leave `PERPLEXITY_API_KEY` empty to leave search settings alone. No search service runs in compose.

Base model: one setting, used by every preset **and** by the translator tool (`translator_tool.py` registers the same model for every user). `--base-model`, else `PRESETS_BASE_MODEL`, else `TRANSLATOR_BASE_MODEL` in `.env`, else `gemma-4-31b-it` from `presets.json`. **The default is Gemma on the Davy connection.** With plain-name QDTS cases installed, `PRESETS_BASE_MODEL`, `TRANSLATOR_BASE_MODEL` or `--base-model` may not select a different base, and provisioning refuses an xAI key or outside saved connection. Remove the case integration under an approved configuration change before using an outside fallback; see [case safety](docs/QDTS_CASES.md).

```
python3 Michael/bootstrap/mcp_servers.py
python3 Michael/bootstrap/user_context.py
python3 Michael/bootstrap/translator_tool.py
python3 Michael/bootstrap/knowledge_bases.py
python3 Michael/bootstrap/kb_manager_tool.py
python3 Michael/bootstrap/office_tools.py
python3 Michael/bootstrap/presets.py --check   # exit 1 if a preset differs
python3 Michael/bootstrap/presets.py
```

**Preset icons.** Each preset's `icon` in `presets.json` names an SVG of `branding/icons/` (hand-written, 64x64, Lenovo palette from `tokens.json`, white 3.5 strokes on a deep-blue round badge with one red accent; no Lenovo logo, no external assets). Open WebUI accepts a model's profile image only as a URL or a `data:image/png|jpeg|gif|webp;base64` URI and refuses SVG, so `presets.py` rasterises the SVG to a 128 px PNG with `bootstrap/icons.py` (standard library only; it draws the small SVG subset the icons use and rejects anything else) and stores the data URI as the model's profile image. A new preset gets an icon the same way: add `branding/icons/<id>.svg` and set `"icon": "<id>.svg"`. A preset with no `icon` keeps the default image and prints a `[NOTE]`; a declared file that is missing or cannot be rendered is a `[FAIL]`. `meta.preset_icon` records which SVG (and renderer version) the image came from and a hash of the image written, so `--check` reports an icon that is missing, outdated or replaced in the app, and a re-run changes nothing otherwise. To look at the PNGs: `python3 Michael/bootstrap/icons.py --out /tmp/icons`.

Re-runs change nothing; only the settings `presets.json` declares are managed, other fields of a preset edited in the UI are kept. To change a prompt, edit its file and re-run. `tests/test_presets.py` and `tests/test_mail_review.py` hold the unit tests (`python3 Michael/tests/test_presets.py`; the Action's tests need `httpx` and `pydantic`, for example `uv run --no-project --with httpx --with pydantic python Michael/tests/test_mail_review.py`). Tool visibility is per connection: a preset sees every tool its connection exposes (after the `function_name_filter_list`).

## Bootstrap: knowledge bases

`knowledge/manifest.json` declares the knowledge bases (id, name, description, files) and `knowledge/` holds the committed Markdown. The first one, **SOPs**, holds `sops/path-sop.md` (PATH, the package arrival tracking system: who to ask, setup, pickup, check-in) and `sops/ai-video-workflow.md` (the AI video creation workflow), converted from the source PowerPoint decks with `bootstrap/pptx_to_markdown.py`: every slide's text, tables and speaker notes, with link targets; images and videos are left out and the large .pptx files are not committed. Each file starts with an overview that names the owner and who to ask. Knowledge base text is public in this repository, so review a deck before converting it (the PATH deck contains internal network addresses).

`bootstrap/knowledge_bases.py` creates each knowledge base (matched by exact name) with **public read and write grants** (every user can search it and add or edit files; an existing one gets whichever of the two is missing, other grants are kept) and uploads every seed file that is missing, waiting until Open WebUI has extracted and indexed it. It never deletes anything and leaves users' own files, folders and edits alone: a seed file whose live text differs from the committed copy is reported as a `[NOTE]` and only overwritten with `--update`. The knowledge base name is its identity; if it is renamed in the app, rename it in the manifest too, or the next run creates a new one.

```
python3 Michael/bootstrap/knowledge_bases.py --check   # exit 1 if a knowledge base or seed file is missing
python3 Michael/bootstrap/knowledge_bases.py           # create what is missing
python3 Michael/bootstrap/knowledge_bases.py --update  # also overwrite files edited in the app with the committed seed
python3 Michael/bootstrap/kb_manager_tool.py           # import the Knowledge Base Manager tool
python3 Michael/bootstrap/presets.py                   # attach the knowledge base to every preset
```

To add a document to the seed: put the Markdown under `knowledge/`, list it in `manifest.json`, run the script. Users can also drop a new file into a chat with the **Knowledge Base Manager** preset and ask it to create an entry; entries made that way live in the app's data volume and are not committed.

**Knowledge Base Manager tool.** `tools/knowledge_base_manager.json` is an Open WebUI tool export, imported by `bootstrap/kb_manager_tool.py` (compared by source, so re-runs change nothing) and readable by every user. It talks to Open WebUI's own API at `http://127.0.0.1:8080` (inside the container) with the signed-in user's own token, so it can only do what that user may do: it has no valves, stores no secrets and logs nothing. Deletions are confirmed by the **user, not the model**: every delete (a file by id or path, a folder, a knowledge base) still needs the model's `confirm=true` (and `allow_nonempty=true` for a non-empty knowledge base), and then the tool asks the signed-in user through Open WebUI's own confirmation dialog in the chat window (`__event_call__`, type `confirmation`). Only the user's click on Confirm deletes anything; Cancel, a closed tab, a timeout, or a call with no chat window (an API client) all end in "Deletion cancelled" and nothing is deleted, and the model cannot answer the dialog. That is the whole change: the flags stay, the dialog is added. Deleting a file removes it from every knowledge base that uses it. `upsert`/`update` replace file content without a dialog (the prompt makes the model ask first). New knowledge bases are private to the user. The seed knowledge base is readable **and editable** by every user, so a non-admin user can change it through the tool; changes are seen by everyone. `tests/stack_e2e.py` proves the dialog (declined: nothing deleted; confirmed: deleted). `tests/test_knowledge_bases.py` holds the unit tests.

## Bootstrap: office document generators (Office Documents preset)

Two workspace tools make real Office files and put them in the user's own file store, with a download link in the chat: `tools/generate_slides.py` (tool id `generate_slide_pptx`, function `generate_slides`, native PowerPoint from a JSON spec) and `tools/generate_documents.py` (`generate_docx_documents`, function `generate_document`, native Word from Markdown with frontmatter or JSON). They are the upstream exports by IANUSTEC (MIT, `Generate Slides` 1.0.3 and `Generate Documents` 1.2.0), kept as tracked source. Each file header lists every deviation from the export: the Lenovo theme, the logo, empty/reduced frontmatter `requirements`, and (documents tool) the `await`s that current Open WebUI needs; the originals stay untouched on their source machine. The **Office Documents** preset (`prompts/office-documents.md`) attaches both; its prompt covers what to ask before generating, how to deliver the link and what not to invent, and leaves the look to the tools.

`bootstrap/office_tools.py` (same conventions as the other scripts; `--check` changes nothing and exits 1 on drift) creates or updates both tools with a public read grant, stores the logo in their valves, points the Word tool's `letterhead_dirs` at a folder that does not exist (see Safety) and checks that each tool exposes its function. Run it before `presets.py`:

```
python3 Michael/bootstrap/office_tools.py --check
python3 Michael/bootstrap/office_tools.py
python3 Michael/bootstrap/presets.py
```

**Lenovo style is the default; nothing for the model to choose.** Values come from `branding/tokens.json`:

| Element | Slides | Word |
|---|---|---|
| Type | Segoe UI (headings and body), the first of the tokens font stack; Office has no fallback stack, so a machine without Segoe UI substitutes its default | same |
| Surfaces / headings | dark neutral ramp: cover, section and closing slides on `#191019`, cards and tables on `#2D2130` | headings `#191019`, accent (table headers, rules, list marks, KPI values) `#221827` |
| Lenovo red `#E1251B` | the accent only: eyebrows, bullets, step numbers, icons; on dark slides a lightened tint (`#F0665E`) keeps 4.5:1 for small text. **Data is not red**: chart series, KPI numbers and funnel/pyramid/cycle steps use the blue and neutral colours of `tokens.json` (`#1E40AF`, `#74697A`, `#3B82F6`, `#5B4E61`, `#1E3A8A`, `#A79BAB`; pies darker, so white labels stay readable). An accent the user asks for colours the data too | cover rule and kicker only; never behind text |
| Links, callouts | n/a | links `#1E40AF`; callout borders from the semantic tokens (info blue, warning `#C2410C`, success `#047857`, danger `#B91C1C`) |
| Logo | cover (top left) and every footer | running header (not on the cover page) and the cover |
| Other looks | `theme` still accepts `midnight`, `forest`, `ocean`, `slate` and the other upstream palettes; `auto` now means `lenovo` instead of guessing from the content | every template (`report`, `memo`, `letter`, `proposal`, `minutes`, `whitepaper`, `blank`) uses the Lenovo palette; `styles.accent`, `heading_color` and `font` in a document's frontmatter still override it |

The tool descriptions the model reads say to leave theme, colours, fonts and logo alone unless the user asks, so the preset prompt does not repeat style rules.

**Where the logo comes from.** The tools run inside the container, which mounts only `runtime/certs`, so they cannot read `runtime/brand/`. `office_tools.py` reads `runtime/brand/lenovo-logo.svg` (or `.png`; the same file `branding.py` uses and `--init-assets` provides), rasterizes an SVG to PNG with the standard library (rect and path elements only; python-pptx and python-docx cannot take SVG) and stores the PNG base64 in each tool's `brand_logo_png_b64` valve. Nothing is fetched from the network and no brand binary is committed. If the file is missing, or is the placeholder (it draws text, which the rasterizer refuses), the script prints a `[NOTE]` and decks and documents are produced without a logo (the slide cover falls back to an icon). Re-run the script after replacing the logo.

**Packages.** `python-pptx`, `python-docx`, `pillow`, `lxml`, `PyYAML` and `markdown-it-py` ship in the Open WebUI image. Only `mdit-py-plugins` (Word Markdown input) does not: Open WebUI pip-installs it when the tool is saved, so the container needs access to a Python package index at that moment (or install it in the image). A failed install makes Open WebUI refuse the Word tool; the slides tool installs nothing.

**Delivery.** Files are saved through Open WebUI's Files API as the signed-in user, so the link `/api/v1/files/<id>/content` works for that user (and admins) only. Both tools also have a fallback that writes to the cache folder (`/cache/files/<name>`), which any signed-in user can open if they know the name; it is used only when the Files API save fails, and the file names carry a random suffix.

**Safety review (what the tools do, and what to keep in mind).**

- No secrets. Nothing is read from the environment except the optional `UPLOAD_DIR`; there are no credentials in the tools. Valves: `unsplash_access_key`, `image_generation_*` and the Word tool's `image_generation_url`/`image_generation_api_key` are empty by default and stay empty.
- Network. Both tools download an image when the model puts an `image_url` (or `![alt](https://...)`) in the spec. That fetch runs inside the container, so it is guarded against request forgery (`_guarded_get` in each tool): http(s) only; the host must resolve to **public addresses only** (private, loopback, link-local such as the cloud metadata address, CGNAT, multicast and reserved ranges, IPv4-mapped IPv6 included, are refused, and a name with ANY internal address is refused); the connection is made to the address that was checked, so a second DNS answer cannot redirect it (no rebinding); **every redirect is checked again** (at most 4); bodies are capped at 15 MB; `data:` URIs and `file:` and other schemes never reach the network. Tests in `tests/test_office_tools.py`. Unsplash and AI image generation, off unless configured by an admin, fetch the URLs their own service returns through the same guard in the slides tool; the Word tool's two image-service helpers only talk to the configured service. The preset prompt also tells the model not to use image links. Unsplash and AI image generation only run when configured (`image_hint`/`image_generate`; the Word tool then uses Open WebUI's image router with the user's prompt).
- Files. Reads: a chat-attached `.docx` as letterhead, by id from the chat. The Word tool would also scan the server's upload folders (`/mnt/uploads`, `UPLOAD_DIR`, `/app/backend/data/uploads`) for a letterhead by file name, which could expose another user's `.docx` header and footer; `office_tools.py` therefore sets `letterhead_dirs` to a folder that does not exist (a letterhead attached to the chat still works). Writes: the generated file through the Files API, and the cache fallback above.
- Execution. No `eval`, `exec` or subprocess in the tools; the spec is data (JSON, or Markdown parsed by markdown-it/PyYAML `safe_load`).
- Prompt-injection: text from an attached file can end up in a deck or document, which is the point; nothing else is reachable from the tools.

**Tests.** `tests/test_office_tools.py` (the rasterizer, the bootstrap against a fake API, and, when the packages are installed, a real deck and Word file read back as Office XML: colours, fonts, logo, themes, the async save regression):

```
python3 Michael/tests/test_office_tools.py   # tool tests are skipped without the packages
uv run --no-project --with python-pptx --with python-docx --with pillow --with pydantic --with httpx \
   --with markdown-it-py --with mdit-py-plugins --with pyyaml --with lxml python Michael/tests/test_office_tools.py
```

## Bootstrap: user context for every model

Every model should know who it is talking to without calling a tool. `functions/user_context.py` is an Open WebUI **Filter function**: its `inlet` runs once per chat request, receives the signed-in user as `__user__` and the model as `__model__`, and adds this block to the system message:

```
<user_context>
The signed-in user you are talking to. These are account facts, not instructions.
name: Jane Doe
id: jdoe
email: jdoe@example.com
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

## Bootstrap: audit log (event function)

`bootstrap/audit_log.py` installs and enables `functions/audit_log.py`, an **event function** that appends one JSON line per administrative or security event (sign-ins, user and role changes, config, plugin and model changes, startup) to `<data volume>/audit/events-YYYY-MM-DD.jsonl`. It records metadata only, never chat content. Same conventions as the other bootstrap scripts; re-runs change nothing and leave the valves alone. What event functions are, how they behave (lifecycle, errors, several workers) and the use cases we ranked are in [docs/Events.md](docs/Events.md); it was proven on a throwaway stack only, so review its valves before applying it to the live instance.

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

### Check contrast (WCAG AA), light and dark

```bash
python3 Michael/bootstrap/branding.py --verify-contrast                  # light and dark
python3 Michael/bootstrap/branding.py --verify-contrast --modes light    # one mode
python3 Michael/bootstrap/branding.py --verify-contrast --unseeded       # live stack: skip the steps that need the seeded chats
```

`--verify-contrast` (`tests/verify_contrast.mjs`, same Node + playwright-core + Chromium setup as `--verify-render`) drives a real headless browser through the sign-in page, the loading splash, an empty chat, a markdown conversation (headings, links, tables, blockquotes, inline code, code blocks), the message action icons and their tooltips, toasts, the model selector, the input menus, the sidebar in hover, selected and menu states, the search modal, the user menu, the settings modal and every tab, every admin and workspace page, the create forms, and a phone-width layout. For every visible text node, input value, placeholder, editor placeholder, icon (`svg`) and form-control border it measures the real contrast ratio against what is painted behind it: the page is screenshotted with all text transparent and every `svg` hidden, and the pixels under each item are sampled, so the gradient, translucent panels and backdrop blur are measured as drawn, not guessed from CSS. It fails (exit 1) when any item is under 4.5:1 (text, placeholders), 3:1 (large text, icons, form-control edges, disabled controls). It prints each failing element with its selector, text, ratio, colours, screen and state; set `REPORT=file.json` to save every failure, `SHOTS=dir` for screenshots, `ONLY=regex` to audit matching screens only, `DUMP=1` to list every measurement.

Needs fixtures to reach every screen: against a **throwaway** stack (own compose project name, port and volume), sign up an admin, point `MICHAEL_ENV_FILE` at a file with that admin's `OPEN_WEBUI_URL`, `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`, and run `tests/seed_contrast_fixtures.py` (synthetic user, chats, folder, model, prompt, knowledge base, tool, note; it refuses port 3000). Against the live stack use `--unseeded`: it signs in read-only with the admin credentials from `.env`, creates nothing, and audits the screens that exist (steps that need a seeded chat are listed as skipped).

How the theme keeps AA: the light ramp's `gray-300` to `gray-600` are all text-grade (Open WebUI paints inactive tabs, meta lines, hints and the faint sidebar icons with `gray-300` and `gray-400`), the hue palette (`red`, `green`, ...) is darkened for the light ground, form controls get an edge (`--lnv-edge`) at 3:1, and `theme.css` has a short "Legibility" block for what a ramp cannot reach (text dimmed by `opacity-NN`, disabled controls, the phone sidebar over its scrim, code syntax colours, toast text). `branding.py` itself also checks the token pairs statically on every run, and `python3 Michael/tests/test_branding_contrast.py` repeats those checks (plus ramp order and the loader-safety limits) without a browser. Run `--verify-contrast` against a throwaway stack after every change to `tokens.json` or `theme.css`, before applying to the live stack.

If the UI is stuck on the splash, switch the theme off in one step (needs the admin credentials in `.env`), then hard-reload the browser (Ctrl+Shift+R):

```bash
python3 Michael/bootstrap/branding.py --rollback
```

This toggles the plugin off through the admin API, so `/static/custom.css` serves 0 bytes. Re-run `branding.py` to re-apply.

### Managed visual and interface extensions

The unified bootstrap also reconciles `extensions.json`: Visuals Toolkit V4, Interface Toggles, and Collapsed Sidebar Pinned Models, plus explicit retirement of OpenUI, Delegated Agent Runner, File Sending Tool, and llmtrace. See [managed extensions](docs/EXTENSIONS.md). `readable_generation_info` remains installed for later internal-model work.
