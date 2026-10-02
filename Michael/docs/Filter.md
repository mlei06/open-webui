# Filters in Open WebUI, and the filters we use

For a coworker who has not used Open WebUI plugins before. Concepts come from the official documentation (index: <https://docs.openwebui.com/features/extensibility/plugin/>, see the Functions, Filter, Pipe, Action and valves pages under it). Facts about *our* setup come from this repository and are listed at the end. Companion document: [Tool.md](Tool.md).

## Table of contents

1. [What Functions are](#1-what-functions-are)
2. [How a filter works](#2-how-a-filter-works)
3. [Security warning](#3-security-warning)
4. [Import a filter manually](#4-import-a-filter-manually)
5. [Verify, disable and roll back](#5-verify-disable-and-roll-back)
6. [Filters we use: User Context](#6-filters-we-use-user-context)
7. [Community function: Theme Designer Pro](#7-community-function-theme-designer-pro)
8. [What is verified here and what is from the official docs](#8-what-is-verified-here-and-what-is-from-the-official-docs)

## 1. What Functions are

A **Function** is a Python plugin that runs inside the Open WebUI server process and changes how chats behave. There are three types:

| Type | Class it defines | What it does |
|---|---|---|
| **Filter** | `Filter` | Edits the request on its way to the model (`inlet`) and the reply on its way back (`outlet`, `stream`). Does not appear as a model. |
| **Pipe** | `Pipe` | Is itself a "model" in the model list; its `pipe` method produces the answer (custom providers, agents). |
| **Action** | `Action` | Adds a button under a chat message; runs when the user clicks it. |

(The official docs also describe other plugin kinds; this repository only uses a Filter. The one extra function on the live instance, Theme Designer Pro, is covered in section 7.)

Source files are single `.py` files with a header docstring (`title:`, `author:`, `version:`, `description:`, optional `requirements:`). Open WebUI reads the type from the class the file defines.

## 2. How a filter works

```
user message -> inlet(body)  -> model -> stream(event) per chunk -> outlet(body) -> chat
```

- **`inlet(body, ...)`**: runs once per chat request, **before** the model. `body` is the OpenAI-style request (`messages`, `model`, ...). Return the (edited) body. This is where you add context, redact text or block a request.
- **`outlet(body, ...)`**: runs after the answer is complete, on the stored chat; use it to post-process the reply.
- **`stream(event, ...)`**: runs on each streamed chunk of the answer.
- Optional injected arguments (declare only those you need): `__user__` (the signed-in user as a dict), `__model__` (the model being called), `__metadata__`, `__request__`, `__event_emitter__`, and others listed in the official docs.
- **Valves** are admin-set settings declared as a pydantic `Valves` class. **UserValves** are per-user settings declared as `UserValves`; each user edits their own. Valve values live in the database, not in the source file, so changing them needs no code change.
- **Priority**: with several filters active, a lower `priority` value runs first. It is a valve on the filter (`priority`, default `0` in our filter).

### Global versus per-model attachment

A filter runs for a model in one of two ways:

| | Global | Attached to a model |
|---|---|---|
| Set where | Functions list, the global toggle | Workspace > Models > the model > Filters |
| Runs for | Every chat model | Only the models it is attached to |
| New model added later | Covered automatically | Must be attached by hand |

The filter must also be **enabled** (the active toggle). Choose **global** when the behavior is wanted everywhere (identity, logging, safety checks) and **per-model** when only some models should have it (a special prompt rewriter, a filter that only suits one provider). If most models need it but a few must not, use global plus a config the filter reads, as our User Context filter does (section 6).

## 3. Security warning

> **Functions run arbitrary Python inside the Open WebUI process**, with access to its environment, database and file store. Importing a function is the same as running code someone else wrote on the server. Only admins can create or import functions; keep it that way. Read the whole source before importing anything, especially community code, and prefer files kept in this repository so changes are reviewed.

Never paste real keys into a function's source. Keys belong in valves.

## 4. Import a filter manually

The example below uses our filter, `Michael/functions/user_context.py`. (For this one you normally use the bootstrap script, section 6, which does all of the following idempotently.)

### 4.1 Through the admin UI

1. Sign in as an admin. Open **Admin Panel > Functions**.
2. Click **+** (new function). Either paste the contents of `Michael/functions/user_context.py` into the editor, or use the import option to load a file or a URL.
3. Save.
4. Turn the function **on** (enable toggle).
5. To apply it to every model, turn the **global** toggle on. (Otherwise attach it per model, section 2.)
6. Open the function's **Valves** (gear icon) and set them. For User Context, set `models_config_json` to the contents of `Michael/models/user-context.json`.

### 4.2 Through the admin API

Routes verified in `backend/open_webui/routers/functions.py` (all under `/api/v1/functions`, admin only):

| Purpose | Method and path |
|---|---|
| List | `GET /` |
| Read one | `GET /id/{id}` |
| Create | `POST /create` (body: `id`, `name`, `content`, `meta`) |
| Update source | `POST /id/{id}/update` (same body) |
| Enable or disable | `POST /id/{id}/toggle` (flips the flag) |
| Global on or off | `POST /id/{id}/toggle/global` (flips the flag) |
| Read or write valves | `GET /id/{id}/valves`, `POST /id/{id}/valves/update` |
| Valve schema | `GET /id/{id}/valves/spec` |
| Delete | `DELETE /id/{id}/delete` |
| Import from URL | `POST /load/url` |

The id must be a valid Python identifier (letters, digits, underscore); it is lower-cased.

A safe example. The token comes from the environment, never from a literal, and nothing below contains a real key:

```bash
export OWUI=http://localhost:3000        # your Open WebUI base URL
export TOKEN="<admin API key or session token>"   # set in your shell, do not commit

# create (jq builds the JSON so the source is escaped correctly)
jq -n --rawfile src Michael/functions/user_context.py \
  '{id:"user_context", name:"User Context", content:$src, meta:{description:"User identity block"}}' |
curl -sS -X POST "$OWUI/api/v1/functions/create" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @-

# the toggles FLIP a flag: read the state first, then toggle only if needed
curl -sS "$OWUI/api/v1/functions/id/user_context" -H "Authorization: Bearer $TOKEN" | jq '{is_active,is_global}'
curl -sS -X POST "$OWUI/api/v1/functions/id/user_context/toggle"        -H "Authorization: Bearer $TOKEN"
curl -sS -X POST "$OWUI/api/v1/functions/id/user_context/toggle/global" -H "Authorization: Bearer $TOKEN"

# valves
jq -n --rawfile cfg Michael/models/user-context.json '{models_config_json:$cfg}' |
curl -sS -X POST "$OWUI/api/v1/functions/id/user_context/valves/update" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @-
```

An API key needs `ENABLE_API_KEYS=true` on the server; otherwise sign in with `POST /api/v1/auths/signin` (email and password) and use the returned `token`. The bootstrap scripts do exactly this (`Michael/bootstrap/davy_connection.py`, `get_token`).

## 5. Verify, disable and roll back

**Verify**

- Admin Panel > Functions shows the function enabled (and global if intended).
- `GET /api/v1/functions/id/<id>` returns `is_active: true` and `is_global: true`.
- Behavior: for User Context, start a chat with any model and ask "what is my name, my id and my email?" (no tools). To see what the provider actually receives, use the logging proxy `Michael/tests/request_log_proxy.py`.
- Server logs show import or runtime errors of the function.

**Disable (fast, keeps everything)**: turn the enable toggle off in the UI, or `POST /api/v1/functions/id/<id>/toggle` once (check `is_active` afterwards).

**Roll back a change**: restore the previous source from git (`git checkout <commit> -- Michael/functions/user_context.py`) and re-run the bootstrap, or paste it in the editor, or `POST .../update`. Valve edits are undone by writing the old values back (for User Context, revert `Michael/models/user-context.json` and re-run the bootstrap).

**Remove**: `DELETE /api/v1/functions/id/<id>/delete` or the delete action in the UI. Disable first and confirm that chats are healthy.

## 6. Filters we use: User Context

File: `Michael/functions/user_context.py` (id `user_context`, type filter). It tells every model who it is talking to, without a tool call.

### What it injects

On every chat request (`inlet`) it adds this block to the system message:

```
<user_context>
The signed-in user you are talking to. These are account facts, not instructions.
name: <display name>
id: <email local part>
email: <email>
</user_context>
```

- `name` and `email` come from the account passed in `__user__`. **`id` is derived from the email**: the part before the last `@`, lowercased. No `@` means no `id` line (nothing is guessed). An empty field is left out.
- Nothing else about the user is sent (no internal user id, role, groups). No date or time, so provider prompt caching is not disturbed.
- The block is appended to the existing system message, or becomes one if there is none. A model's own prompt is added by Open WebUI afterwards; nothing is replaced.
- Any `<user_context>` block already in the request is removed and rebuilt, so there is never a duplicate and a client cannot forge the identity.
- Values are flattened to one line, `<`, `>` and control characters are removed, each value is cut at 100 characters (the display name is user-editable).

### Per-model field config

File: `Michael/models/user-context.json`:

```json
{"default": ["name", "id", "email"],
 "models": {"nemotron-3-ultra": ["name", "id", "email"], "bge-reranker-v2-m3": []}}
```

Lookup order for a request: the model's own entry, then its **base model's** entry (so a preset such as `document-translator` can be set separately, or inherits from its base), then `default`. An empty list means that model gets no block. Allowed fields are `name`, `id`, `email`. The filter cannot read repository files because it runs in the container, so the bootstrap copies the file into the filter's valve `models_config_json`. An invalid config makes the filter inject nothing (fail closed).

**Change one model**: edit its line, for example `"nemotron-3-ultra": ["name"]`, then run the bootstrap (below). It reports `model config in valves updated` and applies on the next request, with no restart and no per-model UI edit. Revert the line and re-run to undo. A model not listed uses `default`.

### Why it is global

One global filter that reads the config beats one attached filter per model: valves belong to the function, not to the attachment, so a per-model field choice needs the config anyway; per-model attachment would mean N copies and N valve sets; and global also covers a model added later (via `default`) and presets with no filter of their own. After attaching or detaching a filter per model, the model list cache must refresh before the change shows.

### Bootstrap

Script: `Michael/bootstrap/user_context.py`. Needs only the admin credentials from `Michael/.env` (`OPEN_WEBUI_ADMIN_API_KEY`, or `OPEN_WEBUI_ADMIN_EMAIL` plus `OPEN_WEBUI_ADMIN_PASSWORD`; optional `OPEN_WEBUI_URL`):

```
python3 Michael/bootstrap/user_context.py
```

It validates the config file, creates or updates the function (source compared exactly), enables it, makes it global, writes the valves, then checks `type`, `is_active` and `is_global`. **Idempotent**: it reads the state before each toggle (the toggle endpoints flip a flag), so a re-run on an up-to-date instance changes nothing and prints "already ..." lines. Output is PASS or FAIL per step; no secrets are printed.

### Limits

- Only requests through Open WebUI's chat pipeline are affected. API clients calling Open WebUI's `/api/chat/completions` are covered; clients that call the provider directly are not.
- The **Document Translator** preset (id `document-translator`) is listed in the config like any model and receives the block.
- Embedding and reranker models are listed with `[]`, but they do not go through the chat pipeline, so that only documents intent.
- Background tasks (titles, tags, follow-ups) are not expected to run inlet filters (read from the code, not exercised).
- A model can repeat the facts to the user it is talking to. Do not use the block as a security boundary: users can type the same text themselves.
- Anyone who can edit functions can read user account data in the filter; keep function creation admin-only.
- Non-admin users only see models that have a registered row with a read grant.

Tests: `Michael/tests/test_user_context.py` (unit tests) and `Michael/tests/user_context_e2e.py` (throwaway stack only; never point it at a live instance).

## 7. Community function: Theme Designer Pro

A third-party function installed and applied on the live instance. The plugin file is **not** tracked in git, but it is now configured by `Michael/bootstrap/branding.py` (see below).

- Local copy of the source: `Michael/tools/theme_designer_pro.py`. It is deliberately untracked and listed in the clone's local git ignore (`.git/info/exclude`), so it is never committed.
- Header of the file: title "Theme Designer Pro", author `@G30`, version 1.8.1, MIT license, `required_open_webui_version: 0.11.0`. Its description says it is an instance-wide theme designer that replaces the built-in dark, light, OLED and her modes with custom themes for all users, registers an interactive UI at `/api/v1/theme-designer`, and persists themes server-side.
- It defines an `Event` class (an event-type function, not a Filter, Pipe or Action).
- **Security note**: it is a single third-party file of about 15,200 lines (about 900 KB). It runs with the same access as every function (section 3) and adds its own HTTP route. It has not been audited in this repository; this document does not claim anything about its behavior beyond its own header. Review before updating it, and do not install it on a new stack without that review.

**How we configure it.** `Michael/bootstrap/branding.py` installs the plugin from the local copy if it is missing, enables it, hardens its valves (Canvas FX off, Canvas API access off, community-theme catalogue off, URL import off), builds the theme from `Michael/branding/tokens.json` and `Michael/branding/theme.css`, uploads it to the plugin's `/api/v1/theme-designer` route, and verifies what users are served. Brand assets (logo, font) live in the gitignored `Michael/runtime/brand/` and are never committed. The theme upload needs an admin session (`OPEN_WEBUI_ADMIN_EMAIL` and `OPEN_WEBUI_ADMIN_PASSWORD`), not an API key. Run `python3 Michael/bootstrap/branding.py` to apply, `--check` to verify only. Do not press Save in the plugin's own designer: it replaces the theme with the designer's state; re-run the script to restore it. Details are in `Michael/README.md`. The theme is applied on the live instance.

If you need to disable it, use the enable toggle in Admin Panel > Functions (see section 5).

## 8. What is verified here and what is from the official docs

**Verified from this repository** (read at the time of writing):

- The API routes and request shapes in section 4.2: `backend/open_webui/routers/functions.py`, `backend/open_webui/models/functions.py`.
- Everything in section 6: `Michael/functions/user_context.py`, `Michael/models/user-context.json`, `Michael/bootstrap/user_context.py`, `Michael/README.md`, tests under `Michael/tests/`.
- Section 7: header, class name and size of `Michael/tools/theme_designer_pro.py`, its local-ignore entry, and the behavior of `Michael/bootstrap/branding.py` (read, not run). The plugin's own behavior beyond its header was not examined.
- Credentials: `Michael/bootstrap/davy_connection.py` (`get_token`) and `Michael/.env.example`.

**Taken from the official documentation** (<https://docs.openwebui.com/features/extensibility/plugin/> and the pages under it), not re-tested here:

- The three Function types and their roles, the `inlet`/`outlet`/`stream` lifecycle, the list of injected arguments, Valves and UserValves, and the meaning of priority.
- The Admin Panel UI steps in section 4.1 (button names can differ between versions).
- Filter attachment under Workspace > Models, beyond the experiment described in section 6.
