# Functions: filters, actions, event functions

An Open WebUI **Function** is a Python plugin that runs inside the server process and changes how chats or the
platform behave. Concepts come from the official documentation
(<https://docs.openwebui.com/features/extensibility/plugin/>); facts about this stack come from this repository and
experiments on a throwaway stack (Open WebUI 0.11.4). Workspace tools are in [tools.md](tools.md).

Contents: [Function types](#function-types) · [Security](#security) · [Importing, verifying, rolling back](#importing-verifying-rolling-back) · [Token usage display](#token-usage-display) ·
[User context filter](#user-context-filter) · [Review and send email action](#review-and-send-email-action) ·
[Event functions](#event-functions) · [Audit log](#audit-log) · [Interface functions](#interface-functions)

## Function types

| Type | Class | What it does |
|---|---|---|
| **Filter** | `Filter` | Edits the request on its way to the model (`inlet`) and the reply on its way back (`outlet`, `stream`) |
| **Pipe** | `Pipe` | Is itself a "model"; its `pipe` method produces the answer |
| **Action** | `Action` | Adds a button under a chat message; runs when the user clicks it |
| **Event** | `Event` | Reacts to system events (sign-ins, config changes, startup); cannot change the triggering action |

Source files are single `.py` files with a header docstring (`title:`, `author:`, `version:`, `description:`,
optional `requirements:`); Open WebUI reads the type from the class the file defines. Valves (admin settings) and
UserValves (per-user settings) are pydantic classes whose values live in the database, not the file. A function must
be **enabled** to run; a filter can also be **global** (every model) or attached to specific models (Workspace >
Models > Filters, `meta.filterIds`). Choose global for behaviour wanted everywhere (identity, logging); if most
models need it and a few must not, use global plus a config the filter reads, as the user context filter does.

### How a filter works

```
user message -> inlet(body) -> model -> stream(event) per chunk -> outlet(body) -> chat
```

`inlet(body, ...)` runs once per chat request before the model; `body` is the OpenAI-style request. `outlet` runs
after the answer on the stored chat; `stream` runs per streamed chunk. Optional injected arguments: `__user__`,
`__model__`, `__metadata__`, `__request__`, `__event_emitter__`. With several filters active, a lower `priority` valve
runs first.

## Security

Functions run arbitrary Python inside the Open WebUI process, with access to its environment, database and file
store. Importing one is the same as running someone else's code on the server. Only admins can create, import,
update, enable or delete functions (keep it that way); read the whole source before importing, especially community
code, and prefer files kept in this repository so changes are reviewed. Never paste keys into a function's source:
use valves. Anyone who can edit functions can read user account data in a filter. Never test a new function on the live
instance first: use a throwaway stack (own compose project name, port and volume).

## Importing, verifying, rolling back

For ours, use the bootstrap scripts (`user_context.py`, `audit_log.py`, `presets.py` for the action, `extensions.py`
for the interface functions); they do everything below idempotently.

**Admin UI:** Admin Panel > Functions > **+**, paste the source or import a file or URL, save, turn **enable** on,
turn **global** on for a global filter, set valves (for the user context filter set `models_config_json` to the
contents of `models/user-context.json`).

**Admin API** (`/api/v1/functions`, admin only): list `GET /`, read `GET /id/{id}`, create `POST /create` (`id`,
`name`, `content`, `meta`), update `POST /id/{id}/update`, enable or disable `POST /id/{id}/toggle`, global
`POST /id/{id}/toggle/global`, valves `GET /id/{id}/valves`, `POST /id/{id}/valves/update`, schema
`GET /id/{id}/valves/spec`, delete `DELETE /id/{id}/delete`, import `POST /load/url`. The id must be a valid Python
identifier (lower-cased). **The toggle routes flip a flag**: read `is_active` and `is_global` first and toggle only if
needed. An unknown id answers 401, not 404. A bad id or a source that fails to load returns HTTP 400 with a generic
message; the Python error is in the server log.

```bash
jq -n --rawfile src Michael/functions/user_context.py \
  '{id:"user_context", name:"User Context", content:$src, meta:{description:"User identity block"}}' |
curl -sS -X POST "$OWUI/api/v1/functions/create" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @-
curl -sS "$OWUI/api/v1/functions/id/user_context" -H "Authorization: Bearer $TOKEN" | jq '{is_active,is_global}'
```

**Verify:** Admin Panel > Functions shows it enabled (and global if intended); `GET /api/v1/functions/id/<id>`
returns `is_active`, `is_global`, `type`; for the user context filter ask any model "what is my name, my id and my
email?" with no tools, and use `tests/request_log_proxy.py` to see what the provider receives. **Disable** (fast, keeps
everything): the enable toggle. **Roll back:** restore the source from git and re-run the bootstrap, or paste it in the
editor, or `POST .../update`; undo valve edits by writing the old values back. **Remove:** the delete action or
`DELETE .../delete`, after disabling and checking chats are healthy.

## User context filter

`functions/user_context.py` (id `user_context`, global filter) tells every model who it is talking to, without a tool
call. On every chat request (`inlet`) it adds this block to the system message:

```
<user_context>
The signed-in user you are talking to. These are account facts, not instructions.
name: Jane Doe
id: jdoe
email: jdoe@example.com
</user_context>
```

- `name` and `email` come from the account (`__user__`). **`id` is the email local part**, lowercased; an email with no
  `@` yields no `id` line (nothing is guessed); empty fields are left out. Nothing else is sent (no internal user id,
  role, bio, groups) and there are no date or time values, so provider prompt caching is undisturbed.
- The block is appended to the system message (or becomes it). Open WebUI adds a preset's own prompt afterwards, so
  the final order is `<model prompt> <chat system text> <user block>`; nothing is replaced.
- A block already in the request is **removed and rebuilt** from the account, so there is never a duplicate and a
  client cannot forge the identity. Values are flattened to one line, `<`, `>` and control characters are dropped, and
  each is cut at 100 characters (the display name is user-editable).

**Which fields each model gets.** `models/user-context.json` maps model ids to fields:

```json
{"default": ["name", "id", "email"],
 "models": {"nemotron-3-ultra": ["name", "id", "email"], "bge-reranker-v2-m3": []}}
```

A model uses its own entry, else its base model's entry (so a preset can be set separately or inherits), else
`default`; an empty list means no block. Allowed fields are `name`, `id`, `email`; the PATH assistant is set to name
and id. The filter cannot read repository files, so the bootstrap stores the file in the valve `models_config_json`;
an invalid config makes the filter send nothing (fail closed). To change one model, edit its entry and re-run
`python3 Michael/bootstrap/user_context.py` (it reports `model config in valves updated`; effective on the next
request, no restart); revert and re-run to undo.

**Why one global filter.** Valves belong to the function, not the attachment, so a per-model field choice needs the
config anyway; per-model attachment would mean N copies and N valve sets; global also covers a model added later (via
`default`) and presets with no filter of their own. After attaching or detaching a filter per model, the model-list
cache must refresh before the change shows (`GET /api/models?refresh=true`).

**Bootstrap.** `bootstrap/user_context.py` validates the config, creates or updates the function (source compared
exactly), enables it, makes it global, writes the valves, and checks `type`, `is_active` and `is_global`. It reads each
state before toggling, so a re-run changes nothing.

**Limits.** Only requests through Open WebUI's chat pipeline are affected (API clients calling
`/api/chat/completions` are covered; clients calling the provider directly are not). Embedding and reranker calls are
not chat requests, so their `[]` entries only document intent. Background tasks (titles, tags, follow-ups) are not
expected to run inlet filters. A model can repeat the facts to the user, and users can type the same text themselves:
**the block is not a security boundary**. Non-admin users see only models with a registered read grant. Tests:
`tests/test_user_context.py` (unit, runs inside the container) and `tests/user_context_e2e.py` (throwaway stack only:
synthetic users, every model asked their name, id and email, compared with the logged provider request, plus the preset
merge, a forged block, a name-only model, and embedding calls without a system message).

## Review and send email action

`functions/mail_review.py` is the **Action** that sends mail. It is declared under `actions` in `models/presets.json`,
installed and activated by `presets.py` (active, not global), and attached to Lenny and the Office Agent only. The
mail tools can only draft ([mcp.md](mcp.md#mail)); sending is the envelope button under an assistant message: it opens
the chat's newest unsent draft in an editable form (From read-only, To, Cc, Subject, Message, and a tick list of the
chat files the model suggested through `suggested_attachment_ids`). Pressing Send makes the action upload the ticked
files from Open WebUI's file store to the mail service and send, using the clicking user's own session token, which
the model never sees. Attachment bytes never pass through the model; the mail service enforces its limits. Report
"sent" only from `get_draft`. Tests: `tests/test_mail_review.py` (needs `httpx` and `pydantic`). The click-through in a
browser has not been tested.

## Event functions

An **event function** reacts to the server's internal event bus. Whenever something notable happens (startup, a
sign-in, a role change, a function toggled, a model created, a chat finished) the server builds a small record and
hands it to a list of sinks: socket cleanup, event functions, webhooks and notifications (`backend/open_webui/events.py`).
`Event.event(...)` is called for **every** event, after it has happened, with the real FastAPI `app`, so it can read
and write application state and the database and register routes, but it **cannot veto or edit** the action. Needs
Open WebUI 0.10.0 or later.

| | Runs when | Can change the request or reply? | Typical use |
|---|---|---|---|
| Event function | Something happened anywhere (181 event names on this version) | No: after the fact | Audit log, provisioning, alerts, startup work, routes |
| Filter | Around each chat request | Yes | Inject context, redact, block |
| Action | A user clicks a button | Acts on that message | Export, send |
| Pipe | A user picks it as a model | It is the model | Custom provider or agent |
| Tool | The model decides to call it | Returns data to the model | Translator, directory |
| Event webhook (no code) | The same events | No | Forward selected events to a URL or chat (Admin > Settings > General > Events) |

Change what the model sees: Filter. Make the model do something: Tool. A button: Action. React to the platform
itself: Event. Only forward events: the built-in webhook, no code. There is **no tool-call event**, no token-usage
event and no page-view event.

### Lifecycle (observed)

| Moment | What happens |
|---|---|
| Create | The source is executed immediately even though the function is inactive (top-level code runs, the class is instantiated); a syntax or import error rejects the create. No events are delivered yet. |
| Enable | `function.enable_started` is delivered to all active event functions **plus this one** and awaited, then `is_active=true`, then `function.enabled`. |
| Valves saved | `function.valves_updated` is published; new values are read on the next event. |
| Source saved | The new source is executed first; on failure the save is rejected and the **existing function is switched to inactive**. |
| Server start | `system.startup.started` then `system.startup.completed` (separate tasks, in either order, sometimes the same millisecond); module code runs again in the new process. |
| Disable | `function.disable_started` is delivered and awaited before `is_active=false` (your last chance to tidy); `function.disabled` is not delivered to you. |
| Delete | No `disable_started`, and `function.deleted` is not delivered to the deleted function. |
| Server stop | `system.shutdown.*` are published, but **none reached the function** in tests: treat shutdown as best effort. |

Enable is not startup, and startup is not enable: handle both and make setup safe to run twice.

### The `Event` class

A file is an event function when it defines a top-level `class Event`. The server calls `Event.event(**params)` and
ignores the return. Declare only the arguments you need (or `**kwargs`): `event` (the redacted payload dict),
`__event_name__`, `__event_id__` (a UUID, usable as an idempotency key), `__event__` (pydantic object), `__id__` (your
function's id), `__app__` (the FastAPI app), `__request__` (the causing request, or `None` for `system.*`). It may be
`async def` or `def` (a plain `def` runs on the event loop and blocks the server).

```python
class Event:
    class Valves(BaseModel):
        greeting: str = Field(default='hello')

    def __init__(self):
        self.valves = self.Valves()          # runs on every (re)load: keep it cheap, no side effects

    async def event(self, event: dict, __event_name__: str = None, __id__: str = None, __app__=None, **kwargs):
        if __event_name__ == 'user.created':
            log.info('new user %s', (event.get('subject') or {}).get('id'))
        elif __event_name__ in ('function.enable_started', 'function.disable_started'):
            if (event.get('subject') or {}).get('id') == __id__:   # fires for EVERY function
                log.info('I am about to be %s', __event_name__)
```

### Payload and catalog

A payload has `schema`, `id`, `event`, `resource`, `operation`, `created_at`, `instance_id`, `version`, `source`
(`api`, `admin`, `password`, `system`; the docs also name `scim`, `oauth`, `trusted_header`), `actor` (id, name, email,
role, timestamps), `subject`, `data`, `message`. Keys such as `password`, `token`, `api_key` and anything ending
`_key`, `_token`, `_secret` are removed and strings are cut at 1,000 characters, but this is not complete:
`chat.finished` carries up to 1,000 characters of the reply and `model.provider_request.failed` carries the last four
characters of the API key. The server lists every event at `GET /api/events` (admin only); 181 events in 29 areas on
0.11.4 (`auth`, `user`, `group`, `chat`, `message`, `channel`, `model`, `knowledge`, `file`, `folder`, `note`,
`memory`, `prompt`, `skill`, `tool`, `function`, `pipeline`, `config`, `system`, `calendar`, `automation`, `terminal` and
more).

### Valves, install and debugging

Declare a pydantic `Valves` class and assign `self.valves = self.Valves()` in `__init__`. Before **every** dispatch the
server re-reads the saved values, so a change applies on the next event without a restart (do not cache valve values at
import). Valves are encrypted at rest when `ENABLE_VALVE_ENCRYPTION=true`; if `WEBUI_SECRET_KEY` changes the values
cannot be decrypted and the function falls back to its defaults. Install through the admin UI (choose the **Event**
starter in the editor) or the API (the type is inferred; confirm `type` is `event`); an event function has no global
switch and no per-model attachment, so an active one receives every event. Debug with `logging`: lines appear in the
container log (`docker logs <container> 2>&1 | grep <tag>`, prefix `function_<id>:event:<line>`); `Error loading
module: <id>` means the function was set inactive; `Event function failed for <id>` plus a traceback is an exception
in your handler. Saving the source or restarting reloads it.

### What an event function can do, and gotchas

- **Routes: yes, with care.** `__app__.add_api_route(...)` works, but Open WebUI mounts the web app as a catch-all and a
  route added after it is shadowed; insert the route **before** the mount named `static`. A route has **no
  authentication** unless you add it, **outlives the function** (disabling or deleting does not remove it until the
  next restart), and exists only in the worker that registered it, so register on the startup event (every worker gets
  it), guarded to register once.
- **Middleware: no** (`Cannot add middleware after an application has started`). Use routes, or a filter for chat
  traffic.
- **`app.state` and the database: yes.** `app.state` is per process and shared by all plugins without namespacing;
  `open_webui.models.*` works and `await Config.upsert(...)` took effect at once (internal APIs, can change between
  releases). Files: the process can write the data volume.
- **Errors.** An exception is caught per function and logged; nothing breaks and nothing is retried. A failure to
  **load** sets the function inactive (even with nobody editing it).
- **Ordering and timing.** No ordering between event functions; all events except the two toggle events are
  fire-and-forget tasks, while `enable_started` and `disable_started` are awaited by the toggle request (a slow
  handler delays toggles of other functions too). **Blocking code blocks everyone** (a `time.sleep(4)` made `/health`
  take 3 s): use `await asyncio.sleep`, `asyncio.to_thread` or async clients.
- **Workers.** Each worker is a separate process with its own module, state and routes; `system.startup.*` reaches the
  function in every worker, request-scoped events only in the worker that served the request; there is no exactly-once
  delivery (use a lock or idempotency key on `__event_id__` if something must happen once; this stack runs one worker).
- **Limits.** After the fact only; delivered to everyone (filter by name, then subject id); no delivery guarantee and
  events during downtime are lost; disabling does not undo routes, `app.state` values or tasks (clean up in
  `disable_started`; a deleted function never sees its own deletion); the function lives in the data volume, so a
  volume reset removes it and only a bootstrap script outside the volume can restore it; `open_webui.*` imports are not
  a stable interface.
- **Security.** An event function is unsandboxed server code that runs unattended on activity nobody watches. Log ids,
  not content (`chat.finished` and `message.*` carry user text); give routes their own admin or token check; send
  only metadata to outside services; fail closed for data safety and loud for operations; after disabling a misbehaving
  function restart the container to drop routes and tasks it left behind.

Use-case notes for this stack: the audit trail below is the best fit. Provider-failure alerts (`model.provider_request.failed`,
never forward `api_key_suffix`) can use the built-in webhook with no code. Settings drift guards on
`system.startup.completed` can re-apply config but a volume reset needs the bootstrap scripts. Guardrails belong in a
filter, not an event. Periodic work belongs in host cron or `bootstrap --check`. Theme Designer Pro is the large real
event function we run ([branding.md](branding.md#theme-designer-pro)).

## Audit log

`functions/audit_log.py` is an event function that appends one JSON line per administrative or security event to
`<data volume>/audit/events-YYYY-MM-DD.jsonl` (mode 0600): time (UTC), event id, name, source, actor id, email and
role, subject, the event's `data`, instance id, pid. Metadata only: chats, messages, files and memories are excluded.
`bootstrap/audit_log.py` (a `provision.py` step) installs and enables it; re-runs change nothing and leave valves
alone.

**Valves:** `events` (comma list, `*` allowed; default `auth.*`, `user.*`, `group.*`, `config.*`, `function.*`,
`tool.*`, `skill.*`, `pipeline.*`, `model.*`, `knowledge.access_updated`, `prompt.access_updated`, `system.*`),
`directory` (empty means `<data>/audit`), `include_email`, `log_to_console` (one `AUDIT ...` line per record),
`retention_days` (0 keeps all; otherwise old daily files are deleted at startup).

```bash
docker exec <container> sh -c 'cat /app/backend/data/audit/events-*.jsonl' | jq -c '[.ts, .event, .actor.email, .subject.id, .data]'
```

Tested on a throwaway stack only: idempotent install; admin actions produced the right events with the right actor; a
chat by a synthetic user was not recorded; an unwritable directory did not break sign-in (the failure is logged);
seven unit tests (`tests/test_audit_log.py`, run inside the container). Limits: not tamper-evident (ship it to
append-only storage if that matters), events lost while the server is down or stopping, no tool calls, shutdown is
never recorded, one file per day shared by all workers (single-line appends do not interleave). Review the valves
(`include_email`, `retention_days`) before applying it to the live instance.

## Interface functions

## Token usage display

`functions/token_usage_display.py` (id `token_usage_display`, a **global, active filter**; "Token Usage & Cost Display"
2.6.0 by smetdenis, MIT, kept verbatim with its header) adds a line under each assistant reply: input, output and total
tokens, the running token total of the chat, reasoning and cached tokens, generation time, tokens per second,
context-window use (an icon that turns orange at 30% and red at 70%), and cost when the provider reports one. It is
managed by `extensions.json` (provisioning keeps it installed, active and global; valves are never written, so the
admin's settings in Admin > Functions survive).

- **Where the numbers come from.** Open WebUI saves provider-reported usage on each message; the filter reads it. A preset
  only gets real counts when its `usage` capability is on (it sends `stream_options: {include_usage: true}`): Lenny has it
  on and the provider returns counts. Without reported usage the filter falls back to a local tiktoken estimate, marked
  as an estimate.
- **Reviewed behaviour.** Read before adoption: no `exec`, `eval`, subprocess, pickle, environment or file access. With
  default valves the only outbound request is a short, cached, 2-second read of the model's own provider connection to
  learn its context size (llama.cpp and llama-swap style endpoints; failures are ignored). `cost_mode` is `auto` (use
  the provider's cost only; ours reports none, so no cost is shown), so nothing goes to `models.dev`. Fetching context
  sizes or estimated prices from `https://models.dev` happens only if an admin turns on `fetch_context_from_modelsdev`
  or `cost_mode: estimate`; nothing about users or chats is sent in those requests.
- **Pinned.** `tests/test_extensions.py` pins the file's SHA-256, checks the licence header and re-checks that it has no
  code execution or unexpected URLs, so replacing it with a new upstream version needs a fresh review first.
- **Editor saves reformat tools.** Saving a function or tool in Open WebUI's code editor re-formats the Python (black
  style), which shows up as cosmetic drift in `extensions.py --check`; provisioning restores the repository text.
  Valves changes in the admin UI are not affected.

`functions/interface_toggles.py` and `functions/collapsed_sidebar_pinned_models.py` (both @G30 version 1.0.0, event
functions managed by `extensions.json`, active and not global) contribute to `/static/loader.js` through the shared
static-asset registry, coexisting with Theme Designer Pro. Interface Toggles adds settings for switch styling, hotkey
hints, timestamps, notifications, confirmation, assistant bubbles, tool output and the Generate Message Pair
shortcut. Collapsed Sidebar Pinned Models shows the user's pinned models as sidebar icons (its API requests use the
browser session). Browser preferences stay user-owned. Both declare Open WebUI 0.11.0 or newer; reload the browser
after changes. The bootstrap checks source and active and global flags, which is not a browser rendering test.
