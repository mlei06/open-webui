# Deployment

How the stack is built, configured, provisioned, upgraded and tested. For what each feature does, use the
feature files listed in [README.md](README.md).

## Principles and policy

- **Public repository.** The fork (`mlei06/open-webui`, upstream `open-webui/open-webui`) is public. Never commit
  internal API keys, private endpoint or CA material, real office documents, real case data, employee names,
  service URLs, tokens, databases or user exports. Use ignored runtime files (`.env`, `runtime/`) and synthetic
  fixtures. Confirm company policy before publishing deployment-specific details.
- **Minimal divergence from upstream.** Stack-specific code lives under `Michael/`. Upstream source patches are
  small and documented (today: the MCP argument guard and the narrow chat-file redirect, see
  [Backend overlay](#backend-overlay)). Prefer a bootstrap integration over changing application internals, and
  preserve upstream license notices and branding requirements (Open WebUI's license restricts replacing its
  branding for deployments of more than 50 users without an enterprise licence or written permission).
- **TLS is never disabled.** Mount the corporate CA material and configure each runtime's trust: Open WebUI reads
  `AIOHTTP_CLIENT_SSL_CERT_FILE`, other runtimes need their own setting. The one recorded exception is the SMTP
  relay check (see [mcp.md](mcp.md#mail)), kept by an explicit owner decision and documented as a risk.
- **Pinned versions.** Container images are pinned by tag or digest; no `latest`. A moving upstream image is not
  substituted without a compatibility review.
- **Secrets and user state are not declarative configuration.** Keys, accounts, chats, uploaded files and the
  database are per deployment. The repository holds only variable names and public-safe defaults.
- **Least privilege for executable capabilities.** No Docker socket or host filesystem is mounted into agents or
  the terminal. File ownership is validated before a tool touches a file; tools take scoped opaque references (file
  ids, job ids, `visual_id`), never model-supplied URLs or arbitrary paths.
- **No reproducibility claim without a clean-volume run.** A fresh deployment must pass provider TLS, preset
  provisioning, MCP discovery, file translation, restart and upgrade checks before the stack is called reproducible
  (see [Testing](#testing-and-verification)).

## Components

| Service | Role | Notes |
|---|---|---|
| `open-webui` | Chat, uploads, identity, presets, tools, functions, skills | Built from `Dockerfile.qdts-backend` onto a retained baseline image. Port `${OPEN_WEBUI_PORT:-3000}`. Mounts `runtime/certs` (read-only), `backend/open_webui/main.py` (read-only) and `runtime/plotly` (read-only). `ENABLE_OLLAMA_API=false`. |
| `open-terminal` | Per-user shell, Python and file workspace | Pinned `open-terminal:0.14.0` plus Chromium, Plotly and the confinement patch. No published port. 2 CPUs, 2 GiB, 512 processes. See [terminal.md](terminal.md). |
| `terminal-shared-writer` | Fills the read-only `/shared` area | Profile `admin`, no network, runs only on request. |
| `employee-directory` | Employee REST API, UI and MCP server | Built from `EMPLOYEE_DIRECTORY_SRC`. UI on `127.0.0.1:${EMPLOYEE_DIRECTORY_PORT:-8780}` only. Not attached to any preset today. |
| `employee-directory-write` | Write-capable directory instance | Profile `write`, disabled by default and admin-only. |
| `mail-service` | Draft and send mail | Built from `MAIL_SERVICE_SRC`. No published port. Provider `mock` or `smtp`. |
| `qdts-cases` | Read-only QDTS replica, 13 legacy tools at `/mcp` and 8 v2 tools at `/mcp/v2` | Built from `QDTS_CASES_SRC`. Serves a derived index mounted read-only. |
| `qdts-indexer` | One-shot index builder | Profile `index`. Mounts the approved normalized source read-only and replaces the index atomically. |

Volumes: `open-webui` (external, default name `open-webui_open-webui`, override with `OPEN_WEBUI_VOLUME`; create it
once on a fresh host with `docker volume create open-webui_open-webui`), `terminal-data` (`/home`),
`terminal-shared`, `qdts-cases-data`, `employee-directory-data`, `mail-service-data`. The compose project name is
fixed to `michael`. Local services that are not in this repository (translator gateway, PATH backend) are reached
through `host.docker.internal`.

## Restart the stack (the one path)

```sh
python3 Michael/bootstrap/provision.py --init-env
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build
python3 Michael/bootstrap/provision.py
```

1. **`--init-env`** (`bootstrap/init_env.py`) only adds what is missing to `Michael/.env` (mode 600) and never
   overwrites or prints a value. It finds the sibling source checkouts (`MAIL_SERVICE_SRC`,
   `EMPLOYEE_DIRECTORY_SRC`, `QDTS_CASES_SRC`; pass `--mail-src`, `--employee-src`, `--cases-src` to override),
   sets `MAIL_PROVIDER` (`smtp` when `SMTP_HOST` is set, else `mock`), `MAIL_SMTP_TLS_VERIFY=false` for smtp, and
   generates `WEBUI_SECRET_KEY`, `MAIL_MCP_API_KEY`, `QDTS_MCP_API_KEY` and `OPEN_TERMINAL_API_KEY`. It sets
   `QDTS_CUSTOMER_NAMES=plain` but never discovers the approved data folder (`QDTS_DEVQDTS_DATA`).
2. **`docker compose up -d --build`** starts the services. QDTS cases needs its derived index built first
   (see [mcp.md](mcp.md#qdts-cases)).
3. **`provision.py`** runs the bootstrap scripts in dependency order against the running stack.

### What `provision.py` runs

| Order | Step | Script | What it does |
|---|---|---|---|
| 1 | wait | | Waits for `/health`. |
| 1b | session key | | NOTE when the running container has no `WEBUI_SECRET_KEY`. |
| 2 | admin | | Signs in with `OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`; on a fresh volume creates that account (Open WebUI makes the first account the admin even with sign-up closed). |
| 2b | accounts | `accounts.py` | Closes sign-up, default role `user` ([accounts.md](accounts.md)). |
| 3 | davy | `davy_connection.py` | Model connection ([Model providers](#model-providers)). An unreachable host is a NOTE. |
| 4 | xai | `xai_connection.py` | Second connection; skipped when `XAI_API_KEY` is empty. |
| 5 | mcp | `mcp_servers.py` | Tool servers ([mcp.md](mcp.md)). |
| 6 | filter | `user_context.py` | User context filter ([functions.md](functions.md)). |
| 7 | audit | `audit_log.py` | Audit event function ([functions.md](functions.md)). |
| 8 | translator | `translator_tool.py` | Document Translator tool ([tools.md](tools.md)). |
| 9 | kbtool | `kb_manager_tool.py` | Knowledge Base Manager tool, installed but attached to no preset ([knowledge.md](knowledge.md)). |
| 10 | office | `office_tools.py` | PowerPoint and Word generator tools ([tools.md](tools.md)). |
| 10b | extensions | `extensions.py` | Managed tools and functions, explicit retirements ([tools.md](tools.md#managed-extensions)). |
| 10c | skills | `skills.py` | Skills ([skills.md](skills.md)). |
| 11 | knowledge | `knowledge_bases.py` | Knowledge bases and seed files. |
| 11b | terminal | `open_terminal.py` | Registers the Open Terminal connection and shares it with all users (`--access all`), which refuses unless the file API is confined ([terminal.md](terminal.md)). |
| 12 | presets | `presets.py` | Presets, the review action, web search, the follow-up question prompt ([models.md](models.md)). |
| 13 | branding | `branding.py` | Lenovo theme ([branding.md](branding.md)). |
| 14 | employees | `seed_employees.py` | Only with `--employees FILE`. |
| 15 | mail relay | `smtp_check.py` | Only with `MAIL_PROVIDER=smtp`; never sends mail. |
| 16 | access | `access.py` | Read-only audit that everything is usable by all users ([accounts.md](accounts.md)). |

The terminal step runs before the presets so each preset's default terminal (`terminal_id`) exists; it can also be run alone (`bootstrap/open_terminal.py`, see [terminal.md](terminal.md)).

Options: `--check` (read-only, exit 1 on drift), `--only a,b` and `--skip a,b` (step names above), `--employees FILE`,
`--update-knowledge` (overwrite seed files edited in the app), `--env-file FILE`, `--starter-file PATH` (stage the
private PowerPoint starter, see [tools.md](tools.md#powerpoint-generation)). Each script keeps its own PASS/FAIL
output; provisioning only orders them, hands them one admin session and adds first-run admin creation and the final
audit. Branding is a NOTE when the private plugin file `tools/theme_designer_pro.py` or `runtime/brand/lenovo-logo.svg`
is absent.

## Runtime setup

1. Copy `.env.example` to `.env` and fill in private values locally.
2. Certificates: copy the approved private CA bundle to `runtime/certs/davy-ca-bundle.pem` (`runtime/` and `.env`
   are ignored). Run `python3 Michael/bootstrap/build_ca_bundle.py`; it writes `runtime/certs/ca-bundle.pem`, the
   host's system bundle plus every other `.pem` in that folder. Open WebUI trusts only the file named by
   `AIOHTTP_CLIENT_SSL_CERT_FILE` (default `/certs/ca-bundle.pem`, an in-container path), so the private bundle alone
   cannot verify public services such as xAI. Re-run it when certificates change.
3. Start the stack with the compose command above.

Open WebUI persists connection settings in its database; on an existing volume saved values win over environment
variables, so change them in Admin Settings or through the bootstrap scripts (or set
`ENABLE_PERSISTENT_CONFIG=false`). Compose loads the whole `.env` into the Open WebUI container, so everything in it
is readable inside that container by anyone with admin or shell access to it; there is no per-service split.

### Environment variables

Placeholders live in `.env.example`; real values only in the private `.env`.

| Group | Variables |
|---|---|
| Model providers | `OPENAI_API_BASE_URLS` (ending in `/v1`), `OPENAI_API_KEYS`, `XAI_API_BASE_URL`, `XAI_API_KEY`, `AIOHTTP_CLIENT_SSL_CERT_FILE` |
| Open WebUI | `OPEN_WEBUI_PORT`, `OPEN_WEBUI_URL`, `OPEN_WEBUI_IMAGE`, `OPEN_WEBUI_BACKEND_BASE`, `WEBUI_SECRET_KEY`, `ENABLE_VALVE_ENCRYPTION`, `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE`, `WEBUI_NAME` |
| Admin credentials (bootstrap) | `OPEN_WEBUI_ADMIN_API_KEY` (needs `ENABLE_API_KEYS=true`) or `OPEN_WEBUI_ADMIN_EMAIL` plus `OPEN_WEBUI_ADMIN_PASSWORD` |
| Models | `PRESETS_BASE_MODEL`, `TRANSLATOR_BASE_MODEL` |
| Web search | `PERPLEXITY_API_KEY`, `ENABLE_WEB_SEARCH`, `WEB_SEARCH_ENGINE` |
| Terminal | `OPEN_TERMINAL_API_KEY`, `OPEN_TERMINAL_IMAGE` |
| Translator | `TRANSLATOR_GATEWAY_URL`, `TRANSLATOR_API_KEY`, `TRANSLATOR_ID` |
| Employee directory | `EMPLOYEE_DIRECTORY_SRC`, `EMPLOYEE_DIRECTORY_PORT`, `EMPLOYEE_DIRECTORY_MCP_URL`, `EMPLOYEE_MCP_API_KEY`, `EMPLOYEE_MCP_WRITE_TOOLS`, `EMPLOYEE_ADMIN_TOKEN`, `EMPLOYEE_WRITE_MCP_URL`, `EMPLOYEE_WRITE_MCP_API_KEY`, `EMPLOYEE_WRITE_MCP_TOOLS` |
| Mail | `MAIL_SERVICE_SRC`, `MAIL_MCP_URL`, `MAIL_MCP_API_KEY`, `MAIL_PROVIDER`, `MAIL_MCP_ALLOW_SEND` (false by default; true lets a model send with `send_draft`), `MAIL_SHARED_SENDER`, `MAIL_ALLOWED_DOMAINS`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_SMTP_CA_FILE`, `MAIL_SMTP_TLS_VERIFY` |
| PATH | `PATH_MCP_URL`, `PATH_MCP_API_KEY` |
| QDTS cases | `QDTS_CASES_SRC`, `QDTS_CASES_IMAGE`, `QDTS_MCP_V2_URL`, `QDTS_MCP_API_KEY`, `QDTS_CUSTOMER_NAMES`, `QDTS_DEVQDTS_DATA`, `QDTS_MAX_CHARS` (response budget, default 60,000 characters) |

### Session key

`WEBUI_SECRET_KEY` signs sign-in sessions and is the source of the tool-valve encryption key
(`ENABLE_VALVE_ENCRYPTION`). `--init-env` generates one when missing and never prints it. Without a stable key
every container restart can create a new one: browsers keep a sign-in the server no longer accepts (the page hangs
on its loading screen) and saved tool keys become unreadable. After a key change recreate the container; everyone
is signed out once, and a browser that hangs needs its site data cleared. Re-run `bootstrap/translator_tool.py` to
re-enter the translator key. Keep the value stable afterwards.

## Model providers

`bootstrap/davy_connection.py` makes the running Open WebUI use `OPENAI_API_BASE_URLS` and `OPENAI_API_KEYS`
even on an existing volume: through the admin API it adds or updates the connection (matched by URL, so re-runs
never duplicate it; other connections are kept), disables the Ollama API in the persisted config, and verifies that
models are listed. `AIOHTTP_CLIENT_SSL_CERT_FILE` is a path inside the container; the script only checks that the
matching file exists under `Michael/runtime/certs/`, and the real trust test is the verify step. A verification
failure usually means a wrong key or an untrusted or missing CA bundle.

`bootstrap/xai_connection.py` adds xAI as a second OpenAI-compatible connection with the same conventions (and does
nothing when `XAI_API_KEY` is empty). The approved base model is Laguna S 2.1 (`laguna-s-2.1`) on the internal provider. With the
plain-name QDTS case integration installed, provisioning refuses an xAI key, a saved outside connection or a
preset base other than the approved model (`bootstrap/case_safety.py`); xAI is an outside service and gets only synthetic or approved
data. These are provisioning protections, not runtime data-loss prevention.

## Backend overlay

Open WebUI's own backend is built from `Dockerfile.qdts-backend`, which layers two files onto a retained local
baseline image (`michael-open-webui:baseline-3ab858c1a0d7`, not published by this repository):

- `backend/open_webui/utils/mcp/arguments.py` and `utils/middleware.py`: the **MCP argument guard**. Unknown root
  arguments in an MCP tool call return an actionable error before the server is invoked; nested arguments are
  preserved for the server's strict typed validation, and argument values are never echoed. Builtin, terminal,
  direct and other non-MCP projection is unchanged. It applies in the legacy, resumed-native and streaming-native
  paths (tests execute the real dispatch blocks with fakes: `tests/test_mcp_arguments.py`).
- `backend/open_webui/main.py` is mounted read-only on recreation to keep a narrow authenticated chat-file
  compatibility redirect (only canonical UUID file ids under `/c/api/v1/files/<id>/content`, redirecting to the
  Files API, which keeps its ownership checks).

The default image tag is `michael-open-webui:qdts-v2`. On another host supply a reviewed `OPEN_WEBUI_BACKEND_BASE`;
before changing the base verify the mounted `main.py` and both overlaid files against it. A Python backend change
needs an Open WebUI rebuild and restart (a connection or prompt refresh cannot install code), and the overlay does
not rebuild the frontend.

Rebuild and recreate only Open WebUI:

```sh
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml build open-webui
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-build --no-deps --pull never open-webui
```

## Upgrade, backup and rollback

- **Never** use `down -v`, remove Open WebUI's volume, delete a live WAL file or replace private configuration
  wholesale. A failure is not authority to refresh raw sources or discard unlanded code.
- Back up before changing: Open WebUI's database (a private SQLite backup), the named volumes, and any connection or
  model configuration you are about to change (the bootstrap scripts that overwrite resources save a mode-0600 copy
  under ignored `runtime/` first; those backups can contain secrets).
- Recreate Open WebUI with its existing image, volume and `WEBUI_SECRET_KEY` when adding a network or service:
  `up -d --no-deps --no-build --pull never --force-recreate open-webui`.
- Rolling back means restoring the previous image **and** the matching data or index, then the previous
  connection, preset and prompt records through the admin APIs. Reverting Git alone changes nothing at runtime.
- QDTS index and image upgrades are paired and described in [mcp.md](mcp.md#qdts-cases). Terminal image upgrades are
  in [terminal.md](terminal.md).

## Testing and verification

Unit tests are standard-library `unittest` files in `Michael/tests/` and need no running stack; most import the
tool sources and use fakes. Run one file, or all of them from the repository root:

```sh
uv run --no-project --with fastapi --with pydantic --with python-pptx --with python-docx --with pillow \
  --with httpx --with markdown-it-py --with mdit-py-plugins --with pyyaml --with lxml --with mcp==1.27.2 \
  --with aiohttp --with plotly --with cairosvg python Michael/tests/test_presets.py
```

| Area | Test files |
|---|---|
| Provisioning, presets, accounts | `test_provision.py`, `test_presets.py`, `test_icons.py`, `test_skills.py`, `test_extensions.py` |
| Tool servers and QDTS | `test_mcp_servers.py`, `test_mcp_arguments.py`, `test_cases_integration.py` |
| Tools | `test_office_tools.py`, `test_slide_template.py`, `test_document_delivery.py`, `test_document_translator.py`, `test_visual_figures.py`, `test_visual_export.py`, `test_workspace_delivery.py`, `test_workspace_files.py`, `test_delivery_contract.py`, `test_delegate_subtask.py` |
| Terminal | `test_open_terminal.py`, `test_terminal_confine.py` (needs `--with open-terminal==0.14.0`) |
| Functions | `test_mail_review.py`, `test_audit_log.py`, `test_user_context.py` (the last two load the function source and run inside the Open WebUI container, see their headers) |
| Knowledge and branding | `test_knowledge_bases.py`, `test_branding_contrast.py` |

Throwaway-stack end-to-end checks create their own compose project, port, volume and mock mail provider; never
point them at the live stack: `stack_e2e.py` (users, access, chats through every preset via the real tool loop in
`socket_chat.py`, privacy between users, the knowledge-base confirmation dialog, mail drafting and sending),
`user_context_e2e.py`, `knowledge_e2e.py`, `cases_e2e.py`, plus `request_log_proxy.py` for inspecting the exact
provider request. Browser checks for the theme are `branding.py --verify-render` and `--verify-contrast`
([branding.md](branding.md)). `tests/fixtures/` holds synthetic files only.

Keep package-level tests separate from Compose integration tests, and report them separately: schema checks, offline
routing, real provider behaviour and the deployed native tool loop are different evidence. A source-only result does
not establish deployment or model reliability.

### Acceptance for a clean deployment

A fresh checkout plus approved private configuration should produce the same managed presets, connections, tools
and skills without manual UI setup (the initial admin identity is bootstrapped from `.env`); provider requests must
succeed with certificate verification on; a second start must change nothing and preserve user state; translation
must handle several files and partial failure with authorized downloads; users must not reach each other's files or
jobs; MCP services must survive restarts; no secrets or real user data may appear in the repository, image layers
or logs; versions must be pinned and upgrades tested.

## Origins and open decisions

Michael began as an onboarding plan for coworkers to clone the fork, supply approved secrets and CA material, and
run one Compose stack that provisions the same presets, tools and MCP connections. The plan's components are now
built: Open WebUI, the bootstrap reconciler, the translator gateway integration, Open Terminal, the QDTS, PATH, mail
and directory integrations. SearXNG and stdio MCP bridges were not built: Open WebUI's MCP client speaks Streamable
HTTP only, so a stdio server needs a supervised bridge, and no service may be assumed to expose stdio on the network.

Decisions still to settle: the publication boundary (public fork versus a private company repository), company SSO
and account provisioning, the backup and managed-resource reconciliation policy, and the supported coworker hosts
(Docker Desktop/WSL/Linux), resources and VPN access.
