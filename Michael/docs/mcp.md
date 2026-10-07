# MCP and OpenAPI tool servers

How Open WebUI attaches external tool servers, what we declare in `mcp/mcp.json`, how `bootstrap/mcp_servers.py`
registers it, and each server we run: the translator gateway, the employee directory, mail, QDTS cases and PATH.
Native Open Terminal is a separate integration ([terminal.md](terminal.md)); do not add it to `mcp.json`. Python
workspace tools are in [tools.md](tools.md).

Contents: [Short version](#short-version) · [How Open WebUI handles tool servers](#how-open-webui-handles-tool-servers) ·
[mcp.json reference](#mcpjson-reference) · [Our servers](#our-servers) · [Translator gateway](#translator-gateway) ·
[Employee directory](#employee-directory) · [Mail](#mail) · [QDTS cases](#qdts-cases) · [PATH](#path) ·
[Adding a server](#adding-a-server) · [Provisioning and troubleshooting](#provisioning-and-troubleshooting) ·
[Security notes](#security-notes)

## Short version

- A **tool server** is a separate service that offers tools to the model: **MCP** (Model Context Protocol over
  Streamable HTTP) or **OpenAPI** (a REST service that publishes an OpenAPI document).
- All registered servers live in **one list** in Open WebUI's database (config key `tool_server.connections`),
  edited in Admin Settings > External Tools or through the admin API.
- `mcp/mcp.json` declares that list. `python3 Michael/bootstrap/mcp_servers.py` makes Open WebUI match it and checks
  each server answers and lists its tools. It is the **only** thing that registers servers.
- Declared today: `doctranslator`, `employee_directory`, `mail`, `qdts`, `path`, and a disabled admin-only
  `employee_directory_write`.

## How Open WebUI handles tool servers

(Read in this repository's code under `backend/open_webui/` and exercised on a throwaway stack.)

### MCP versus OpenAPI

| | MCP tool server | OpenAPI tool server |
|---|---|---|
| Protocol | MCP over **Streamable HTTP** only (`utils/mcp/client.py`). No stdio, no legacy SSE. | Plain HTTP calls described by an OpenAPI document |
| Tools discovered | At the **start of every chat request** that selects the server: connect, `initialize`, list tools | When the list is saved or the app loads: the spec is fetched and cached |
| Tool id in lists | `server:mcp:<id>` | `server:<id>` |
| Auth types | `none`, `bearer`, `session`, `system_oauth`, `oauth_2.1`, `oauth_2.1_static` | `none`, `bearer`, `session`, `system_oauth` (spec fetch supports only `bearer` and `none`) |
| Our script supports | yes (`type: mcp`) | yes (`type: openapi`), verified at spec level only |

The model sees each tool renamed `<id>_<tool name>` (for example `qdts_search_cases`), so **the id is part of every
function name**: keep it short and stable. MCP over stdio cannot be started by Open WebUI; a stdio server needs a
supervised bridge exposing Streamable HTTP.

### Storage and precedence

The list is the persistent config key `tool_server.connections`. The environment variable `TOOL_SERVER_CONNECTIONS`
is only a first-boot seed; with `ENABLE_PERSISTENT_CONFIG` on (default) the database wins, so we provision through the
API. The admin route replaces the list **as a whole**: an update reads the list, changes one entry and writes
everything back; `mcp_servers.py` does exactly that and leaves entries it does not manage alone.

### Admin API (admin only)

| Route | Purpose |
|---|---|
| `GET /api/v1/configs/tool_servers` | `{"TOOL_SERVER_CONNECTIONS": [...]}`. Bearer keys come back **in clear**. |
| `POST /api/v1/configs/tool_servers` | Replaces the whole list. |
| `POST /api/v1/configs/tool_servers/verify` | Takes one connection. MCP: connects, runs `initialize`, lists **all** the server's tools. OpenAPI: fetches the spec. On failure: a bare `400 Failed to create MCP client` with no reason. |
| `GET /api/v1/tools/` | What the signed-in user sees, including `server:` entries after access control. |

### Connection fields

Model `ToolServerConnection` (`routers/configs.py`); extra keys are allowed and kept.

| Field | Meaning |
|---|---|
| `type` | `mcp` or `openapi` (default `openapi`) |
| `url` | MCP endpoint, or an OpenAPI base URL; must be reachable **from the Open WebUI container** |
| `path` | OpenAPI spec path or URL (default `openapi.json`); empty for MCP |
| `auth_type`, `key` | `bearer` sends `Authorization: Bearer <key>`. `session` sends the **requesting user's own Open WebUI token**, so it cannot also carry a service key. |
| `headers` | Extra static headers; values may use `{{USER_ID}}`, `{{USER_EMAIL}}`, `{{USER_NAME}}`, `{{CHAT_ID}}`, `{{MESSAGE_ID}}` |
| `config.enable` | Off means not listed and not usable |
| `config.function_name_filter_list` | Comma-separated string limiting the model's tools (below) |
| `config.access_grants` | Who may use it (below) |
| `info.id`, `info.name`, `info.description` | `id` is the stable key and part of the tool id |

**Who can use a server** (`has_connection_access`): no grants means **admins only**;
`[{"principal_type":"user","principal_id":"*","permission":"read"}]` means everyone; group or specific user entries
work the same way. Access is checked when listing and again when a chat connects; a user without access gets **no
error**, the server is silently left out.

**Function name filter list.** Not an exact match: an entry matches a tool name that **ends with** it, and a leading
`!` blocks. Empty exposes everything. `verify` ignores the filter and lists every tool, so a verified server can still
hand the model fewer tools (which is what we want for the translator).

**How a chat selects a server.** Per preset (`meta.toolIds`, entries like `server:mcp:qdts`), per chat (the tools
menu, subject to access), or for API callers `tool_ids: ["server:mcp:<id>"]`. With native function calling the model
decides when to call a tool.

**Timeouts.** MCP `initialize` 10 s; tool calls use `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER` (falls back to
`AIOHTTP_CLIENT_TIMEOUT`, 300 s); OpenAPI spec fetch 10 s. There is no per-connection timeout. TLS follows the CA
bundle in `AIOHTTP_CLIENT_SSL_CERT_FILE`.

**Reaching a service from the container.** A sibling compose service by name (`http://employee-directory:8000/mcp`,
no published port). A host service through `host.docker.internal` (mapped in compose; `localhost` always fails; on a
native Linux engine the service must listen on a non-loopback interface). A server may check the `Host` header.

**Not supported:** stdio and legacy SSE; files from MCP tools (an image result is stored and shown, other embedded
files become a text placeholder and `resource_link` results are dropped, which is why translation downloads go
through a workspace tool); handing a chat attachment to an MCP tool (it gets only the model's arguments);
per-user credentials alongside a service key; per-tool access control other than the filter list; seeding after first
boot. Browsers can add **direct** tool servers (off by default, `features.direct_tool_servers`); those are separate
and not provisioned.

## mcp.json reference

`mcp/mcp.json` is validated against `mcp/mcp.schema.json` (JSON Schema 2020-12), `schemaVersion` 1:
`{ "$schema": "./mcp.schema.json", "schemaVersion": 1, "servers": [ ... ] }`.

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `^[a-z][a-z0-9_]{0,31}$`; becomes `server:mcp:<id>` and prefixes tool names; changing it creates a new server |
| `name`, `description` | yes | Shown in Admin Settings and to the model |
| `type` | yes | `mcp` or `openapi` |
| `transport` | yes | `streamable-http` (mcp) or `openapi-http` (openapi) |
| `url` | yes | `http(s)` URL as seen from the container; may contain `${VARIABLE}`, each declared in `env` |
| `spec_path` | openapi | Spec path or URL |
| `env` | yes | One entry per variable used (URL and key): `name`, `required`, `secret`, `description`, optional `default`. A required variable has no default; a secret has no default and is never printed. Values come from the process environment, then `Michael/.env`, then the default; empty counts as unset |
| `auth` | yes | `{"type":"none"}` or `{"type":"bearer","key_env":"NAME"}`; `"optional": true` turns an empty key into no authentication |
| `access` | yes | `{"type":"public"}`, `{"type":"admin"}` or `{"type":"groups","groups":["Group name"]}` (names resolve to ids; unknown is an error) |
| `enabled` | yes | `false` registers it switched off; a disabled server with unset variables is skipped |
| `tools` | yes | Names the server must list; `verify` fails if one is missing (operation ids for OpenAPI) |
| `function_name_filter_list` | no | What the model gets (suffix match, `!` blocks); every `tools` entry must pass it |
| `headers` | no | Custom headers on every call; public-safe values only (keys go in `env` and `auth`) |

Not in v1 because Open WebUI has no field: per-server timeouts, OAuth auth, forwarding cookies.

## Our servers

| id | Tools the model gets | URL variable (default) | Auth | Access | Enabled |
|---|---|---|---|---|---|
| `doctranslator` | `translation_capabilities`, `get_translation_status`, `cancel_translation` | `TRANSLATOR_GATEWAY_URL` (required) | bearer `TRANSLATOR_API_KEY` | public | yes |
| `employee_directory` | `search_employees`, `get_employee`, `get_direct_reports`, `get_management_chain` | `EMPLOYEE_DIRECTORY_MCP_URL` (`http://employee-directory:8000/mcp`) | bearer with `EMPLOYEE_MCP_API_KEY` only if set | public | yes |
| `mail` | `create_draft`, `update_draft`, `get_draft`, `list_drafts`, `discard_draft`, and `send_draft` when `MAIL_MCP_ALLOW_SEND` is on | `MAIL_MCP_URL` (`http://mail-service:8000/mcp`) | bearer `MAIL_MCP_API_KEY`; headers `X-User-Email`, `X-Chat-Id`, `X-Message-Id` | public | yes |
| `qdts` | `lookup_entities`, `get_entity`, `search_cases`, `search_notes`, `search_tasks`, `aggregate_records`, `get_cases`, `get_records` | `QDTS_MCP_V2_URL` (`http://qdts-cases:8000/mcp/v2`) | shared bearer `QDTS_MCP_API_KEY`; no identity headers | public | yes |
| `path` | `path_search_records`, `path_get_record_status`, `path_get_record_details`, `path_get_record_history`, `path_get_records`, `path_count_records`, `path_get_activity`, `path_get_filter_values`, `path_lookup_employees` | `PATH_MCP_URL` | bearer `PATH_MCP_API_KEY` only if set | public | yes |
| `employee_directory_write` | `search_employees`, `get_employee`, `create_employee`, `update_employee`, `delete_employee` | `EMPLOYEE_WRITE_MCP_URL` | bearer `EMPLOYEE_WRITE_MCP_API_KEY` (required) | admin | **no** |

Which presets use which server is in [models.md](models.md#what-each-preset-has-attached).

### Translator gateway

The gateway offers eight tools. We expose only the three read-only helpers; the tools that carry file content as
base64 are filtered out so the model never copies a document through its context. Translation itself runs through
the Document Translator workspace tool ([tools.md](tools.md#document-translator)), which is why the id
`doctranslator` is fixed. The gateway must listen on an interface the container can reach and allow that `Host`
(for example `http://host.docker.internal:8766/mcp`). One bearer key serves every user, so all users share one
translator identity and job ids are not scoped per user.

### Employee directory

A service built from `EMPLOYEE_DIRECTORY_SRC` (compose service `employee-directory`, data in the volume
`employee-directory-data`, healthcheck on `/healthz`). Its fuzzy `search_employees` returns candidates with a
`resolution` (`exact`, `confident`, `ambiguous`, `none`); its tool descriptions tell the model to search first, pass
the returned `id` on and ask the user when ambiguous. Our filter list keeps the model to the four read tools. No preset uses it
since the Office Agent was retired.

| `.env` variable | Meaning |
|---|---|
| `EMPLOYEE_DIRECTORY_SRC` | Checkout of the employee-directory repository (build context), default `./services/employee-directory` |
| `EMPLOYEE_DIRECTORY_PORT` | Host port of the UI, default `8780`, on **127.0.0.1 only** |
| `EMPLOYEE_MCP_WRITE_TOOLS` | Write tools on the main instance; leave empty (read only) |
| `EMPLOYEE_ADMIN_TOKEN` | Enables permanent delete and import; empty disables them |
| `EMPLOYEE_MCP_API_KEY` | Optional bearer key for `/mcp`; if set the directory requires it and `mcp_servers.py` sends it: re-run both |

```sh
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build employee-directory
python3 Michael/bootstrap/seed_employees.py /path/to/employees.json      # --dry-run first if you like
python3 Michael/bootstrap/mcp_servers.py
```

`seed_employees.py` streams the file (kept outside this repository) into a temporary file in the running container,
runs the service's own `seed` command, removes the file and prints only counts; re-running reports `unchanged`. The
input is a list (or an object with an `employees` list) of `id`, `name` and optional `aliases`; email is derived as
`<id>@<domain>`; hand-edited records are kept unless `--overwrite-manual`.

**Write variant.** Write tools are enabled per process, so a write endpoint with its own key is a second instance:
compose service `employee-directory-write` (profile `write`; same image and volume, write tools on, its own required
key, no published port). The `employee_directory_write` entry is admin-only and disabled. To enable: set
`EMPLOYEE_WRITE_MCP_API_KEY`, start it with `--profile write up -d employee-directory-write`, set `"enabled": true`
in `mcp.json`, run `mcp_servers.py`. It can create, update and soft delete; permanent delete and import are never
exposed through MCP. Two instances share one SQLite file (the read instance migrates), so keep write traffic light.

### Mail

The `mail` connection is the model channel of the mail service: create, update, get, list and discard a draft, and
**`send_draft` only when the service runs with `MAIL_MCP_ALLOW_SEND=true`** (below). By default it has no send tool.
It sends the bearer key `MAIL_MCP_API_KEY` and the identity headers `X-User-Email`, `X-Chat-Id`
and `X-Message-Id`, which Open WebUI fills per call. The service derives the From address from the user, never from
the model: the user's own company address (`MAIL_ALLOWED_DOMAINS`, `lenovo.com`), otherwise `MAIL_SHARED_SENDER`
(`lenny@lenovo.com`). There is no redirect switch (`MAIL_REDIRECT_ALL=off`).

**Two ways to send.** (1) The **Review and send email action** below: the user's own click. (2) **`send_draft`**, an
opt-in model tool for automations and for "send it" requests. `MAIL_MCP_ALLOW_SEND` is `false` by default in compose and
`.env.example`; `true` adds a sixth tool that sends **the caller's own draft** (the owner is the `X-User-Email` of the
call, so a draft is never sent for anyone else) to exactly its recorded recipients, through the same single-use,
version-checked, policy-checked, rate-limited and audited path the review form uses (`MAIL_SENDS_PER_HOUR`, company
addresses only, audit `via` `mcp:header`). `send_draft(draft_id, version?)` fails if the draft changed since the model
read it, and refuses a draft that suggests attachments (only the review form can upload files, so model-sent mail
carries none). `mcp.json` lists `send_draft` in the connection's `function_name_filter_list` but not in the tools that
verification requires, so verification passes with sending off and the model gets the tool only when the service
offers it. The rule for *when* a model may send is behavioural, not technical: the `mail-drafting` skill and the Lenny
prompt say to send only when the user explicitly says to (or a scheduled automation's prompt does),
and to report "sent" only from `send_draft` or `get_draft`. Anyone holding the shared `MAIL_MCP_API_KEY` can send as
any user while this is on, so keep the key private and the service on the internal network. Turn it off by setting
`MAIL_MCP_ALLOW_SEND=false` and recreating `mail-service`. Tests: `tests/test_mcp_send.py` in the mail-service repository
(real MCP client over HTTP against the mock provider) and `tests/test_mcp_servers.py` here; the live stack uses the real
relay, so send was verified only through those tests, not by sending real mail.

**Automations.** Open WebUI automations run the full chat pipeline as the automation's owner with the preset's own tool
list (`meta.toolIds`, so Lenny's scheduled run has the `mail` connection and `send_draft`), the owner's identity in the
`{{USER_EMAIL}}` header, and no terminal unless the preset sets one (charts then use the render service,
[tools.md](tools.md#visuals-toolkit)). A scheduled prompt has nobody to ask, so it must name the recipients, say what the
mail contains and say to send it; the skill tells the model to send nothing when a recipient is missing or ambiguous.
Recipients must be company addresses (`MAIL_ALLOWED_DOMAINS`), the owner must be eligible for a From address, and an
owner without a company address sends as the shared sender.

**Sending through the form is the Review and send email action** (`functions/mail_review.py`, [functions.md](functions.md#review-and-send-email-action)):
the envelope button under an assistant message opens the newest unsent draft in an editable form, and its Send click
sends. The mail service accepts only plain Open WebUI file ids in `suggested_attachment_ids` (at
most 5; it rejects paths, names and `terminal:` references); `workspace_files.prepare_email_attachments` converts
terminal paths into ids ([tools.md](tools.md#workspace-files)). The service enforces 5 files, 10 MB each, 20 MB
total, no executables or macro documents.

The compose service `mail-service` is built from `MAIL_SERVICE_SRC`, has no published port, keeps data in
`mail-service-data` and validates user sessions against `http://open-webui:8080`. `MAIL_PROVIDER=mock` (default)
records messages in `/data/mock_emails.jsonl` and sends nothing; `smtp` sends through `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS` (never printed or committed; `MAIL_SMTP_CA_FILE=/certs/ca-bundle.pem`
makes the service verify the relay with the mounted bundle).

**Relay check.** `bootstrap/smtp_check.py` (a provision step when `MAIL_PROVIDER=smtp`) resolves the relay, connects,
says EHLO, upgrades with STARTTLS, authenticates and quits. It never issues MAIL FROM, so **no message is sent**. An
unreachable relay (off the company network or VPN) is a NOTE; a rejected login or failed certificate check is a FAIL.
The first real test send is done by hand through the review form.

**`MAIL_SMTP_TLS_VERIFY=false` is a recorded risk.** The relay's certificate cannot be verified (an internal authority
and no DNS name in it), so certificate checking is off by the owner's decision. STARTTLS is still required with no
plaintext fallback, so the connection is encrypted, but anyone who can intercept it can pose as the relay and read the
SMTP credentials and every message. Use it only on a trusted network. The cleaner fix is `MAIL_SMTP_CA_FILE` with the
issuing authority's certificate in `runtime/certs/`.

### QDTS cases

QDTS is the support-case system. Open WebUI reaches a **read-only, indexed replica** through eight v2 tools; the
service and its schema-3 index belong to the separate `devqdts` repository (`qdts_cases/`, `Dockerfile.cases`, and
the detailed contract in its `docs/CASES_MCP.md`). This repository owns the Open WebUI integration. A reviewed source
revision, its matching index and its image are used together; editing files here does not deploy services or change
saved settings. No live QDTS login, sync or raw-source mutation runs inside this integration. How a model uses the
tools, with worked examples, is the `qdts` skill ([skills.md](skills.md)); this section is the service contract.

**Connection and trust.** `mcp.json` keeps connection id `qdts` with public read grants and a shared bearer key, and
reads **`QDTS_MCP_V2_URL`** (default `http://qdts-cases:8000/mcp/v2`) so an old explicit `QDTS_MCP_URL` cannot
silently select the wrong schema. The same service keeps 13 legacy tools at `/mcp` and flat REST routes; versioned
`POST /api/v2/{tool_name}` shares v2 validation with MCP. There is no write tool, user token, identity header or
per-case authorization: everyone granted the connection can read every loaded case and **all private and system
notes**; identity selects a query, never an access boundary. Customer names are plain (`QDTS_CUSTOMER_NAMES=plain`;
alias mode is not implemented). Admins can read the stored connection key (valve encryption does not cover connection
config). The internal provider and `laguna-s-2.1` remain required: provisioning refuses an xAI key, outside
saved connections, mismatched saved arrays or a different preset base, and prompts forbid sending case evidence to
outside services. These are provisioning protections, not runtime DLP.

**Tools.** `lookup_entities`, `get_entity`, `search_cases`, `search_notes`, `search_tasks`, `aggregate_records`,
`get_cases`, `get_records`; MCP discovery inlines local schema references so `filters` and `request` are objects for
the provider. Semantics worth knowing as an operator:

- Counts are exact: `total` counts ALL matches before the page, `limit: 0` counts only, and notes and tasks report
  record totals and distinct parent cases separately. State is a snapshot; `as_of` is a calculation date, never a
  historical snapshot; dates use inclusive `from` and exclusive `until` UTC.
- `query` is literal whole-word matching over case title and full AI summary; **all words** by default,
  **`query_mode: "any"`** for an OR (also on note and task predicates). No synonyms, stemming or semantic expansion.
  `include_notes` also matches inside one note.
- **`product`, `customer` and `owning_team`** accept one field each for exact IDs and name keywords: a string (all
  words), a list (any entry) or `{any, all, exclude, descendants}`. A product name also matches the series above a
  product and its brand, so `"thinkpad"` selects every ThinkPad product. A name matching nothing returns zero with a
  warning; an unknown numeric-looking ID is an error. A bare series ID (such as a `case_product_series` group id) selects only
  the series entry, which holds no cases: the result warns, and the fix is `{"any": ["<id>"], "descendants": true}` or the
  series name. They AND with the older `*_ids` fields and every other filter.
- **Limits.** `limit` up to 100 and `top_n` up to 500; a larger value is reduced with a warning, never rejected.
  `lookup_entities` limit up to 100; `get_cases` and `get_records` take up to 50 ids; id lists up to 500. The response
  byte budget `QDTS_MAX_CHARS` (default 60,000 characters, about 100 compact rows) decides how many rows fit; the rest
  continue with `next_cursor`, repeating the same arguments. Aggregates allow 200 time buckets and 1,000 cells.
- **Aggregation.** `request={record_type, filters, group_by}` with one to three dimensions (at most one temporal), plus
  `top_n` and `sub_top_n` (nested ranking: each of the top `top_n` first-level values keeps its own `sub_top_n`
  combinations of the rest, remainder as Other inside that parent). Notes and tasks can group by their parent
  case's product, customer, team, state, severity, owner, participant and case dates. Groups are rows indexed into
  typed `dimensions[].buckets`; Unknown, Other, label-only and real identities stay distinct; employee and series
  groups overlap and must not be summed; an interior zero means no indexed match, not complete source coverage.
- `get_entity` separates observed team membership from lifecycle-owner, task-creator and note context; observation
  times are not join or leave dates. Recorded participation is involvement, not effort. Task counters are separate:
  `captured_tasks`, `list_reported_total_tasks` and `list_reported_open_tasks`. Long batches continue with the same
  arguments and revision; on `INDEX_CHANGED` selection restarts; unknown fields fail rather than broaden a query.
  Keep `data_as_of` and normalization uncertainty visible: a pull or build time proves nothing about a completed sync.

**Coordinated service rollout.** Schema changes are paired: back up the derived index and keep its matching QDTS
image; privately back up the `qdts` connection and the QDTS-using presets. A schema transition needs a brief
QDTS-only stop: stop `qdts-cases`, build the reviewed image, run the one-shot indexer against an approved consistent
normalized backup (it mounts the source read-only, stages, checks and fsyncs a private index, then atomically replaces
the destination; a failure keeps the old one; UID/GID 10001 and the read-only serving mount are unchanged), then start
only that service. Never start an old image on a new schema or the new image on an old index.

```sh
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml stop qdts-cases
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml build qdts-cases
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml --profile index run --rm --no-deps qdts-indexer
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-build --no-deps qdts-cases
```

A code-only change with a compatible index needs just `up -d --build --no-deps qdts-cases`. Then verify health,
rejection of a missing or wrong key, the 13 legacy tools at `/mcp`, the eight at `/mcp/v2`, strict nested validation
and search/aggregate count parity (never log credentials, full case text or raw provider configuration); run
`mcp_servers.py` to update the connection URL and allowlist, `presets.py --prompts-only` for prompts, reload model
lists and start a new chat (old conversations keep their tool arguments). Rollback: restore the previous QDTS image AND
matching index plus the previous connection and prompt records; later compatible refreshes swap the index
atomically without a restart, and old cursors then return `INDEX_CHANGED`. The Open WebUI backend argument guard is a
separate rollout ([deployment.md](deployment.md#backend-overlay)).

**Validation.** The devqdts suite uses invented data (120 cases, 245 notes including a 72,000-character note, 154
captured tasks against 158 reported, reversed intervals, missing due dates, a membership/team mismatch, duplicate
identities); it tests same-note joins, grouping independence, temporal top-N and null coverage, REST/MCP parity, long
continuations, atomic replacement and preserved v1 tools, including the selectors, `query_mode`, limit clamping,
three dimensions and `sub_top_n` (138 tests). No source capture or live index is used. The Michael suite
(`test_cases_integration.py`, `test_mcp_arguments.py`, `test_presets.py`) verifies inventory, prompts, provider
guards, fixture routing and the argument guard; `tests/fixtures/cases_model.py` is a wiring fixture, not a reasoning
model, and `tests/cases_e2e.py` creates a disposable stack. Live runs through Lenny: a wifi/wireless ranking in one
aggregate call, a ThinkPad breakdown by team then product in one call with `sub_top_n`.

### PATH

PATH is the read-only mailroom system: packages and checked-in mail with recipients, custody and activity. The
declaration replaces the earlier hand-made connection and preset; no PATH backend is built or started by compose.

- `mcp.json` registers Streamable HTTP connection **`path`** with the nine tools above (they replace the old
  three-tool interface). `PATH_MCP_URL` defaults to `http://host.docker.internal:18076/mcp/` only for the local review
  stack; supply an approved internal endpoint elsewhere. `PATH_MCP_API_KEY` is optional and empty by default
  (`auth_type=none` for the currently approved keyless company-network backend); if the backend is given a shared
  bearer key, configure the same key privately. It is not an employee identity key. Public-read visibility in Open WebUI
  does not secure a reachable keyless backend: the operator owns the network boundary and model-access policy.
  Never disable TLS verification for an HTTPS endpoint.
- Presets: **PATH assistant** (`path`) has only `server:mcp:path`, time and user input, and the user context filter;
  **Lenny** also has `path`. The filter derives the company itcode from the email local part,
  the assistant validates it with PATH and passes `recipient_network_ids`, never "me"; the name route is the
  explicitly labelled fuzzy `recipient_name`. This is an identity filter, not authorization. PATH records stay on the
  approved internal model.
- The PATH semantics models need (admin mark versus physical collection, WAITING, checked-in MAIL, dates in
  America/New_York) are in the `path-mailroom` skill and the PATH assistant prompt.
- **Rollout (not executed by offline tests).** After the nine-tool backend is up, review private endpoint and access
  configuration, back up connection and preset settings privately, run MCP registration, user context and presets, then
  `--check` them. Registration verifies all nine tool names, so an old backend fails verification (do not use
  `--no-verify` as acceptance). Check traces for "my packages", ambiguous names, fuzzy receiver names, status and
  count selection, MAIL, admin-mark wording and date boundaries.
- **Rollback.** Removing the declaration does not remove the live connection (unmanaged entries are preserved unless
  pruned), and dropping the preset declaration does not delete the model. Restore the backed-up connection, preset and
  context configuration through the admin APIs, or disable PATH access and detach `server:mcp:path` from the three
  presets, restoring the old tool filter and prompt if restoring the three-tool interface. No broad prune, no volume
  reset; reverting Git is not a runtime rollback.

## Adding a server

1. Add an entry to `servers` in `mcp.json`, copied from the nearest existing one; declare every variable in `env`
   with `secret` set honestly.
2. Put values in `Michael/.env` and placeholders in `.env.example`. Never put a value, key or internal host name in
   `mcp.json`; use `${VARIABLE}` with a default only when it is safe to publish.
3. Start with `access: admin`, test, then widen.
4. Run `python3 Michael/bootstrap/mcp_servers.py --check`, then without `--check`.
5. Give a preset the server by adding `{"server": "<id>"}` to its `tools` in `models/presets.json` (or Workspace >
   Models, or the chat's tools menu).

An OpenAPI server uses `type: openapi`, `transport: openapi-http`, `url` as the base URL, `spec_path`, and operation
ids as `tools`.

## Provisioning and troubleshooting

```sh
python3 Michael/bootstrap/mcp_servers.py            # register or update, then verify
python3 Michael/bootstrap/mcp_servers.py --check    # change nothing; exit 1 if the live list differs
python3 Michael/bootstrap/mcp_servers.py --prune    # also remove servers it registered that are no longer declared
python3 Michael/bootstrap/mcp_servers.py --no-verify --config other.json
```

It validates `mcp.json` against the schema and the rules a schema cannot express (unique ids, every `${VAR}` and key
declared, secrets without defaults, every `tools` entry allowed by the filter); resolves variables (a missing
required variable fails **before anything is sent** and names the variable); merges each declared server by
`info.id` (declared fields win, extra keys already on the connection are kept) and writes the list **only if
something changed**; a connection it creates carries `managed_by: michael/mcp.json`, and `--prune` removes only such
connections no longer declared; then verifies each enabled server through Open WebUI's verify route and confirms every
declared tool is listed. A second run changes nothing; keys are never printed. Settings: the admin credentials of the
other bootstrap scripts plus each server's variables; API keys need `ENABLE_API_KEYS=true`.

| Symptom | Likely cause |
|---|---|
| `missing required variable(s) NAME` | Set it in `Michael/.env` or the environment |
| `verification failed ... HTTP 400` | No reason is given. Check the URL from inside the container (`docker exec <container> python3 -c "import urllib.request as u; print(u.urlopen('<url>').status)"`: 401 means reachable, wrong key), not `localhost`; the key; the `Host` allowlist; the server logs |
| `no group named` | Create the group first or fix the name |
| `lists none of N declared tool(s)` | The server changed, or `tools` has a typo |
| `no admin credentials` / HTTP 401 or 403 on the config route | Wrong or non-admin key, or `ENABLE_API_KEYS` is off |
| A user does not see a server | `access` is `admin` or they are not in the group; hidden silently |
| The model never calls the tool | The server must be on the preset or chat, and the model must support native function calling |

To see what Open WebUI holds: `GET /api/v1/configs/tool_servers` as admin (returns keys: keep it out of logs).
Verified on a throwaway stack: provisioning, idempotence, `--check`, bad URL, wrong key and missing variable failing
clearly, a hand-added connection surviving every run, `--prune` removing only the managed one, group access, the
optional directory key, the write instance, and an OpenAPI server at spec level. Not tested: editing a managed
connection in the admin UI (the marker may not survive; the next run re-adopts it by id), OAuth auth types,
a native Linux engine reaching a loopback-only host service, an OpenAPI server in a chat.

## Security notes

- **The translator key is shared** (one bearer key for every user), stored in Open WebUI's config (readable in clear by
  admins) and in `.env`.
- **The employee directory UI has no sign-in**, by decision. It is published on `127.0.0.1` only; keep it that way.
  The MCP endpoint is open to the compose network unless `EMPLOYEE_MCP_API_KEY` is set. Write tools are off, hidden by
  the filter list, and available only through the separate admin-only instance.
- **QDTS gives every granted user every loaded note**, including private and system notes, and a shared key.
- **What the model can do:** call the tools it was given, with arguments it writes; it never sees bearer keys. It
  cannot choose a server it was not given, reach a server a user has no access to, or call a tool the filter hides.
  It cannot be stopped from sending what a tool returns to its provider: directory, case and PATH data go to whichever
  model the chat uses, so use approved models only.
- **`.env` goes into the Open WebUI container** (`env_file`), so tokens and keys are visible to anything running there,
  including admin-authored workspace tools.
- **Public repository.** `mcp.json` holds variable names and public-safe defaults only; real employee files, keys and
  clone paths belong in `.env` or outside the repository.
