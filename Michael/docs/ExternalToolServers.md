# External tool servers: how they work, and how we provision ours

For a coworker who administers our Open WebUI. It explains how Open WebUI attaches external tool servers (MCP and OpenAPI), what we declare in `Michael/mcp/mcp.json`, and how `Michael/bootstrap/mcp_servers.py` registers it. Everything about Open WebUI here was read in this repository's code (paths are under `backend/open_webui/`) and exercised on a throwaway stack; section 9 lists what was and was not run. Companion documents: [Tool.md](Tool.md) (tools in general, the Document Translator tool) and [Filter.md](Filter.md).

## Table of contents

1. [The short version](#1-the-short-version)
2. [How Open WebUI handles external tool servers](#2-how-open-webui-handles-external-tool-servers)
3. [The inventory: mcp.json reference](#3-the-inventory-mcpjson-reference)
4. [Our servers](#4-our-servers)
5. [Deploying the employee directory](#5-deploying-the-employee-directory)
6. [Adding a server](#6-adding-a-server)
7. [How provisioning works, verifying, troubleshooting](#7-how-provisioning-works-verifying-troubleshooting)
8. [Security notes](#8-security-notes)
9. [What was verified, and limits found](#9-what-was-verified-and-limits-found)

## 1. The short version

- A **tool server** is a separate service that offers tools to the model. Open WebUI supports two kinds: **MCP** (Model Context Protocol over Streamable HTTP) and **OpenAPI** (any REST service that publishes an OpenAPI document).
- All registered servers live in **one list** in Open WebUI's database (config key `tool_server.connections`). Admins edit it in Admin Settings > External Tools, or through the admin API.
- `Michael/mcp/mcp.json` is our declaration of that list. `python3 Michael/bootstrap/mcp_servers.py` makes Open WebUI match it, then checks that each server answers and lists its tools. It is the **only** thing that registers servers (`translator_tool.py` no longer does).
- We declare three servers: the **translator gateway**, the **employee directory** (read only) and an **employee directory write variant** that is declared but disabled.

## 2. How Open WebUI handles external tool servers

### 2.1 MCP versus OpenAPI

| | MCP tool server | OpenAPI tool server |
|---|---|---|
| Protocol | MCP over **Streamable HTTP** only (`utils/mcp/client.py` uses the SDK's `streamablehttp_client`). No stdio, no legacy SSE transport. | Plain HTTP calls described by an OpenAPI document. |
| Tools discovered | At the **start of every chat request** that selects the server: connect, `initialize`, list tools (`utils/middleware.py`, `connect_mcp_server`). | When the connection list is saved or the app loads: the spec is fetched and cached (`utils/tools.py`, `get_tool_servers`). |
| Tool id in lists | `server:mcp:<id>` | `server:<id>` |
| Auth types | `none`, `bearer`, `session`, `system_oauth`, `oauth_2.1`, `oauth_2.1_static` | `none`, `bearer`, `session`, `system_oauth` (spec fetch itself supports only `bearer` and `none`) |
| Our script supports | yes (`type: mcp`) | yes (`type: openapi`), verified at spec level only |

What the model sees: each tool is renamed `<id>_<tool name>`, for example `employee_directory_search_employees`. So the **id is part of every function name**; keep it short and stable.

### 2.2 Storage and precedence

- Stored as the persistent config key `tool_server.connections` (`config.py`). The environment variable `TOOL_SERVER_CONNECTIONS` (a JSON list) is only the **first-boot seed**. After that the database wins: with `ENABLE_PERSISTENT_CONFIG` on (the default) changing the variable does nothing on an existing volume. We therefore provision through the API, never through the variable.
- The list is **replaced as a whole** by the admin route. There is no per-server endpoint, so an update must read the list, change one entry, and write everything back. `mcp_servers.py` does exactly that and keeps entries it does not manage untouched.

### 2.3 Admin API routes (admin only)

| Route | Purpose |
|---|---|
| `GET /api/v1/configs/tool_servers` | Returns `{"TOOL_SERVER_CONNECTIONS": [...]}`. The admin API returns bearer keys **in clear**. |
| `POST /api/v1/configs/tool_servers` | Replaces the whole list. |
| `POST /api/v1/configs/tool_servers/verify` | Takes one connection. MCP: connects, runs `initialize`, lists **all** the server's tools. OpenAPI: fetches the spec. On failure it answers a bare `400 Failed to create MCP client` (or `...connect to the tool server`) with no reason. |
| `GET /api/v1/tools/` | What the signed-in user can see, including `server:` entries after access control. |

### 2.4 Connection fields

Model `ToolServerConnection` (`routers/configs.py`); extra keys are allowed and kept.

| Field | Meaning |
|---|---|
| `type` | `mcp` or `openapi` (default `openapi`). |
| `url` | MCP endpoint, or the base URL of an OpenAPI server. Must be reachable **from the Open WebUI container**. |
| `path` | OpenAPI: spec path or full URL (default `openapi.json`). MCP: empty. |
| `auth_type`, `key` | See 2.1. `bearer` sends `Authorization: Bearer <key>`. `session` sends the **requesting user's own Open WebUI token** instead, so it cannot also carry a service key. |
| `headers` | Extra static headers. Values may use templates such as `{{USER_ID}}`, `{{USER_EMAIL}}`, `{{CHAT_ID}}`. |
| `config.enable` | Off = not listed and not usable. |
| `config.function_name_filter_list` | Comma-separated string. See 2.6. |
| `config.access_grants` | Who may use it. See 2.5. |
| `info.id`, `info.name`, `info.description` | `id` is the stable key and part of the tool id; name and description are shown to people. |

### 2.5 Who can use a server (access grants)

`has_connection_access` in `utils/access_control/__init__.py`:

- **No grants** (missing or empty list): visible to **admins only**.
- `[{"principal_type":"user","principal_id":"*","permission":"read"}]`: **everyone**.
- `{"principal_type":"group","principal_id":"<group id>","permission":"read"}` entries: members of those groups. A user entry with a specific user id works the same way.
- Access is checked both when listing tools and again when a chat connects. A user without access gets **no error**: the server is silently left out of that chat.

### 2.6 Function name filter list

`config.function_name_filter_list` (a comma-separated string) limits which of the server's tools the model gets. The rule (`utils/misc.py`, `is_string_allowed`) is not an exact match: an entry matches a tool name that **ends with** it, and a leading `!` blocks. An empty list exposes everything. `verify` ignores the filter and lists every tool, so a verified server can still hand the model fewer tools, which is what we want for the translator (section 4).

### 2.7 How a chat selects a server

- Per model: the model preset's tools (`meta.toolIds`, entries such as `server:mcp:doctranslator`). These are used whenever someone chats with that preset.
- Per chat: the user switches the server on in the chat's tools menu (it must be listed for them, so access applies).
- API callers: pass `tool_ids: ["server:mcp:<id>"]` in the chat completion request.

With native function calling (the default) the model decides when to call a tool; Open WebUI makes the call and feeds the result back.

### 2.8 Timeouts

- MCP session `initialize`: 10 s (`MCP_INITIALIZE_TIMEOUT`).
- HTTP timeout for MCP and OpenAPI tool calls: `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER`, which falls back to `AIOHTTP_CLIENT_TIMEOUT` (300 s by default).
- Fetching an OpenAPI spec: `AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA` (10 s).
- There is no per-connection timeout. TLS for tool servers uses `AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL`, which follows the CA bundle in `AIOHTTP_CLIENT_SSL_CERT_FILE`.

### 2.9 How the container reaches a service

- **Sibling service in our compose project:** use the service name, for example `http://employee-directory:8000/mcp`. Both services are on the project's default network. Do not publish a port for this.
- **A service on the host:** `host.docker.internal`. `docker-compose.yaml` maps it (`extra_hosts: host.docker.internal:host-gateway`). `localhost` and `127.0.0.1` mean the container itself and always fail. On a native Linux engine the service must listen on a non-loopback interface; on Docker Desktop (our WSL setup) the translator gateway, bound to host loopback only, was reachable at `host.docker.internal:8766` (tested).
- The server may check the `Host` header. The directory does that only when `EMPLOYEE_ALLOWED_HOSTS` is set.

### 2.10 What is not supported

- MCP over stdio or the old SSE transport; Open WebUI cannot start an MCP process.
- Files from MCP tools: an image result is stored and shown, but any other embedded file is reduced to a text placeholder, and `resource_link` results are dropped. This is why translation downloads go through the workspace tool, not the MCP gateway.
- Handing a chat attachment to an MCP tool: the tool receives only the arguments the model writes.
- Per-user credentials with a service key: `session` uses the user's Open WebUI token; there is no per-user secret store for tool servers.
- Per-tool access control, other than the filter list.
- Seeding the list from the environment after first boot (see 2.2).
- Browsers can add **direct** tool servers (user-side connections, off by default: permission `features.direct_tool_servers`). Those are separate from this list and not provisioned.

## 3. The inventory: mcp.json reference

`Michael/mcp/mcp.json` is validated against `Michael/mcp/mcp.schema.json` (JSON Schema 2020-12; editors that read `$schema` give completion). `schemaVersion` is `1`.

```json
{ "$schema": "./mcp.schema.json", "schemaVersion": 1, "servers": [ { ...server... } ] }
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `^[a-z][a-z0-9_]{0,31}$`. Stable key; becomes `server:mcp:<id>` and prefixes tool names. Changing it creates a new server. |
| `name`, `description` | yes | Shown in Admin Settings and to the model. |
| `type` | yes | `mcp` or `openapi`. |
| `transport` | yes | `streamable-http` (type `mcp`) or `openapi-http` (type `openapi`). |
| `url` | yes | `http(s)` URL as seen from the Open WebUI container. May contain `${VARIABLE}` references; each must be declared in `env`. |
| `spec_path` | openapi only | Spec path or full URL; default `openapi.json`. |
| `env` | yes | One entry for **every** variable the server uses (URL and key): `name`, `required`, `secret`, `description`, optional `default`. A required variable has no default; a secret has no default and is never printed. Values come from the process environment, then `Michael/.env`, then the default. Empty counts as unset. |
| `auth` | yes | `{"type":"none"}` or `{"type":"bearer","key_env":"NAME"}`. Add `"optional": true` and an empty key means no authentication instead of an error. |
| `access` | yes | `{"type":"public"}` (everyone), `{"type":"admin"}` (admins only), or `{"type":"groups","groups":["Group name"]}` (names are resolved to ids; an unknown name is an error). |
| `enabled` | yes | `false` registers the server switched off. A disabled server whose variables are not set is skipped. |
| `tools` | yes | Tool names the server must list; `verify` fails if one is missing. For OpenAPI these are operation ids. |
| `function_name_filter_list` | no | What Open WebUI exposes to the model (suffix match, `!` blocks, see 2.6). Every entry of `tools` must pass it. Omit to expose all tools. |
| `headers` | no | Custom request headers sent on every call, for example `{"X-User-Email": "{{USER_EMAIL}}"}`; Open WebUI fills `{{USER_EMAIL}}`, `{{USER_ID}}`, `{{USER_NAME}}`, `{{CHAT_ID}}` and `{{MESSAGE_ID}}` per call. Public-safe values only; keys go in `env` and `auth`. |

Not in v1 because Open WebUI has no field for them: per-server timeouts, OAuth auth, custom headers, forwarding cookies.

## 4. Our servers

| id | Tools the model gets | URL variable (default) | Auth | Access | Enabled |
|---|---|---|---|---|---|
| `doctranslator` | `translation_capabilities`, `get_translation_status`, `cancel_translation` | `TRANSLATOR_GATEWAY_URL` (required, for example `http://host.docker.internal:8766/mcp`) | bearer, `TRANSLATOR_API_KEY` (required) | public | yes |
| `employee_directory` | `search_employees`, `get_employee`, `get_direct_reports`, `get_management_chain` | `EMPLOYEE_DIRECTORY_MCP_URL` (`http://employee-directory:8000/mcp`) | bearer with `EMPLOYEE_MCP_API_KEY` only if set, else none | public | yes |
| `mail` | `create_draft`, `update_draft`, `get_draft`, `list_drafts`, `discard_draft` | `MAIL_MCP_URL` (`http://mail-service:8000/mcp`) | bearer, `MAIL_MCP_API_KEY` (required); headers `X-User-Email`, `X-Chat-Id`, `X-Message-Id` | public | yes |
| `employee_directory_write` | `search_employees`, `get_employee`, `create_employee`, `update_employee`, `delete_employee` | `EMPLOYEE_WRITE_MCP_URL` (`http://employee-directory-write:8000/mcp`) | bearer, `EMPLOYEE_WRITE_MCP_API_KEY` (required) | admin | **no** |

**Translator.** The gateway offers eight tools. We expose only the three read-only helpers; the tools that carry file content as base64 are filtered out so the model never has to copy a document through its context. Translation itself runs through the Document Translator workspace tool and preset ([Tool.md](Tool.md), section 6), which attaches this connection by its id `doctranslator`. The id is therefore fixed.

**Employee directory.** The service has a fuzzy `search_employees` that returns candidates with a `resolution` (`exact`, `confident`, `ambiguous`, `none`). The directory's tool descriptions tell the model to search first, pass the returned `id` on, and ask the user when the result is ambiguous. Our filter list keeps the model to the four read tools even if someone enables write tools on the main instance.

**Write variant.** The directory enables write tools per process (`EMPLOYEE_MCP_WRITE_TOOLS`), so a write-capable endpoint with its own key means a second instance. `docker-compose.yaml` has `employee-directory-write` behind the compose profile `write`: same image, same data volume, write tools on, its own required bearer key (it refuses to start without one), no published port. The `employee_directory_write` entry is admin-only and disabled. To turn it on:

1. Set `EMPLOYEE_WRITE_MCP_API_KEY` in `Michael/.env` and start it: `docker compose --env-file Michael/.env -f Michael/docker-compose.yaml --profile write up -d employee-directory-write`.
2. Change `"enabled"` to `true` for that entry in `mcp.json` and run `mcp_servers.py`.
3. Admins can then select it in a chat. It can create, update and soft delete only; permanent delete and import are never exposed through MCP.

The two instances share one SQLite file on the volume (the read instance migrates; the write instance does not). That worked in our test, but it is two writers on one SQLite file: keep write traffic light.

## 5. Deploying the employee directory

`Michael/docker-compose.yaml` builds the `employee-directory` service from the employee-directory source. In `Michael/.env`:

| Variable | Meaning |
|---|---|
| `EMPLOYEE_DIRECTORY_SRC` | Path to a checkout of the employee-directory repository (the build context). Default `./services/employee-directory`. Keep the real path in the private `.env`. |
| `EMPLOYEE_DIRECTORY_PORT` | Host port of the UI, default `8780`, published on **127.0.0.1 only**. |
| `EMPLOYEE_MCP_WRITE_TOOLS` | Write tools on the main instance; leave empty (read only). |
| `EMPLOYEE_ADMIN_TOKEN` | Enables permanent delete and import in the directory. Empty disables them. |
| `EMPLOYEE_MCP_API_KEY` | Optional bearer key for `/mcp`. If you set it, the directory requires it and `mcp_servers.py` sends it: re-run both. |

Data lives in the named volume `employee-directory-data` (project-prefixed, for example `michael_employee-directory-data`); the compose project keeps it across rebuilds. The service has a healthcheck on `/healthz`.

```bash
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build employee-directory
python3 Michael/bootstrap/seed_employees.py /path/to/employees.json      # add --dry-run first if you like
python3 Michael/bootstrap/mcp_servers.py
```

`seed_employees.py` takes the file path at run time, streams it into a temporary file inside the running container, runs the service's own `seed` command and removes the temporary file. The file is never copied into the repository or an image, and the script prints only the service's counts. Re-running reports `unchanged`. The input is a list (or an object with an `employees` list) of `id`, `name` and optional `aliases`; email is derived as `<id>@<domain>`. Records edited by hand are kept unless `--overwrite-manual`.

## 6. Adding a server

1. Add an entry to `servers` in `mcp.json`. Copy the nearest existing one and change `id`, `name`, `description`, `url`, `tools`. Declare every variable in `env` with `secret` set honestly.
2. Put the variable values in `Michael/.env` (and placeholders in `.env.example`). Never put a value, key or internal host name in `mcp.json`: use `${VARIABLE}` with a public-safe `default` only when the default is safe to publish.
3. Choose `access`. Start with `admin`, test, then widen.
4. Run `python3 Michael/bootstrap/mcp_servers.py --check`, then without `--check`.
5. To give a model preset the server, add `server:mcp:<id>` to its tools (Workspace > Models) or switch it on in a chat.

An OpenAPI server: `type: openapi`, `transport: openapi-http`, `url` is the base URL, `spec_path` the spec path, and `tools` are operation ids.

## 7. How provisioning works, verifying, troubleshooting

```bash
python3 Michael/bootstrap/mcp_servers.py            # register/update, then verify
python3 Michael/bootstrap/mcp_servers.py --check    # change nothing; exit 1 if the live list differs
python3 Michael/bootstrap/mcp_servers.py --prune    # also remove servers it registered that are no longer declared
python3 Michael/bootstrap/mcp_servers.py --no-verify --config other.json
```

Settings: the admin credentials of the other bootstrap scripts (`OPEN_WEBUI_ADMIN_API_KEY`, or admin email and password; `OPEN_WEBUI_URL`) plus the variables each server declares. API keys need `ENABLE_API_KEYS=true`. Standard library only.

Steps, in order:

1. Validate `mcp.json` against the schema, and check the rules a schema cannot (unique ids, every `${VAR}` and key declared, secrets without defaults, every `tools` entry allowed by the filter).
2. Resolve variables. A missing required variable fails **before anything is sent** and names the variable.
3. Read the whole list, merge each declared server by `info.id` (declared fields win; extra keys already on the connection are kept), and write the list back **only if something changed**. Connections it does not manage stay exactly as they were. A connection it creates carries `managed_by: michael/mcp.json`; `--prune` removes only connections with that marker whose id is no longer declared.
4. Verify each enabled server through Open WebUI's verify route and confirm every declared tool is listed. Disabled servers are skipped.

Output is `[PASS]`, `[FAIL]` or `[SKIP]` lines and `RESULT: PASS` or `FAIL`; exit status 0 or 1. Keys are never printed: they are read, sent and compared, nothing else. Running it twice changes nothing the second time. `translator_tool.py` no longer touches connections; it only reports whether `doctranslator` is registered, and either script may run first.

| Symptom | Likely cause |
|---|---|
| `missing required variable(s) NAME` | Set it in `Michael/.env` or the environment. |
| `verification failed ... HTTP 400` | Open WebUI gives no reason. Check the URL from inside the container (`docker exec <container> python3 -c "import urllib.request as u; print(u.urlopen('<url>').status)"`: a 401 means reachable, wrong key), not `localhost`; the key; the `Host` allowlist; the server logs. |
| `no group named` | Create the group in Admin Settings first, or fix the name. |
| `lists none of N declared tool(s)` | The server changed, or `tools` has a typo. |
| `no admin credentials` / `HTTP 401` or `403` on the config route | Wrong or non-admin key, or `ENABLE_API_KEYS` is off. |
| A user does not see a server | `access` is `admin`, or they are not in the group. The server is hidden silently. |
| The model never calls the tool | The server must be selected in the chat or the preset, and the model must support native function calling. |

To see what Open WebUI holds: `GET /api/v1/configs/tool_servers` as admin (returns keys: keep it out of logs), or Admin Settings > External Tools.

## 8. Security notes

- **The translator key is shared.** One bearer key for every Open WebUI user, so all users share one translator identity and job ids are not scoped per user. It is stored in Open WebUI's config (readable in clear by admins through the API) and in `Michael/.env`.
- **The employee directory UI is open.** The UI and REST API have no sign-in, by decision. Anyone who can reach the port can read and change employees. We publish it on `127.0.0.1` only; keep it that way, and do not put it behind a public route. Only permanent delete and import need `EMPLOYEE_ADMIN_TOKEN`.
- **The MCP endpoint is open too** unless `EMPLOYEE_MCP_API_KEY` is set. It is reachable by anything on the compose network. The optional key limits that to holders of the key.
- **Write tools are off by default.** The main instance exposes none, and our filter list hides any it might gain. The write variant is a separate instance with its own key, admin-only in Open WebUI and disabled until you enable it.
- **What the model can do.** With the read-only directory it can search and read employee records (name, email, position, manager chain) and nothing else: no create, update, delete, permanent delete or import. With the translator it can list capabilities and read or cancel a job id it was given. It never sees bearer keys; Open WebUI adds them to the request.
- **What the model cannot do.** It cannot choose a server it was not given, reach a server a user has no access to, or call a tool the filter list hides. It cannot be stopped from sending what a tool returns to its provider: employee data returned by the directory goes to whichever model the chat uses, including an outside service such as xAI. Use only approved models with real directory data.
- **`.env` goes into the Open WebUI container** (`env_file: .env`), so `EMPLOYEE_ADMIN_TOKEN` and the keys are visible to anything running inside that container, including admin-authored workspace tools. Admin tokens are therefore only as safe as Open WebUI admins are.
- **Public repository.** `mcp.json` holds variable names and public-safe defaults only. The real employee file, keys and clone paths belong in `.env` or outside the repository.

## 9. What was verified, and limits found

Verified in code: sections 2.1 to 2.8 (files named in the text). Verified on a throwaway stack (own compose project, ports and volume, removed afterwards): both servers provisioned and verified; a second run and `--check` changed nothing; a bad URL, a wrong key and a missing variable each failed clearly; a connection added by hand survived every run; `--prune` removed only the managed connection; the same result in either order with `translator_tool.py`; group access (a user saw the directory only after joining the group); the optional bearer key on the directory; the write instance (refuses to start without its key; admin could create a synthetic employee through it, the user could not see it); an OpenAPI server verified at spec level. Chats with grok-4.6 called `search_employees` and answered correctly (see the task report for the transcripts).

Limits found:

- `verify` shows every tool the server offers, not the filtered set, and its failure message has no reason.
- For an ambiguous name, grok-4.6 searched, saw `ambiguous`, and then asked the user which person was meant (it used Open WebUI's built-in `ask_user` question tool, so a script driving the API without a browser sees it wait).
- The OpenAPI path was not used in a chat; only registered and verified.
- Not tested: editing a managed connection in the admin UI (the `managed_by` marker may not survive; the next run re-adopts it by id), OAuth auth types (not supported by the script), and a native Linux engine reaching a loopback-only host service.
