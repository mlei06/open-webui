# Events in Open WebUI, and what we can do with them

For a coworker who has not used Open WebUI plugins before. Concepts come from the official documentation (the Event function page: <https://docs.openwebui.com/features/extensibility/plugin/functions/event/>, plus the Functions index, valves and "Under the Hood" pages next to it). Facts about *this* code base come from `backend/open_webui/` in this repository and from experiments on a throwaway stack (Open WebUI 0.11.4, one container, SQLite, synthetic users, deleted afterwards). Section 16 separates the two. Companion documents: [Filter.md](Filter.md), [Tool.md](Tool.md).

## Table of contents

1. [What an event function is](#1-what-an-event-function-is)
2. [Events versus filters, tools, actions and pipes](#2-events-versus-filters-tools-actions-and-pipes)
3. [The lifecycle, step by step](#3-the-lifecycle-step-by-step)
4. [The `Event` class contract, with a tested example](#4-the-event-class-contract-with-a-tested-example)
5. [The event payload and the catalog](#5-the-event-payload-and-the-catalog)
6. [Valves](#6-valves)
7. [Install: admin UI and admin API](#7-install-admin-ui-and-admin-api)
8. [Debugging](#8-debugging)
9. [What an event function can do: routes, state, database](#9-what-an-event-function-can-do-routes-state-database)
10. [Errors, ordering, blocking and several workers](#10-errors-ordering-blocking-and-several-workers)
11. [Limits and gotchas](#11-limits-and-gotchas)
12. [Security guidance](#12-security-guidance)
13. [Case study: Theme Designer Pro](#13-case-study-theme-designer-pro)
14. [Use cases for the Michael stack, ranked](#14-use-cases-for-the-michael-stack-ranked)
15. [The prototype: Audit Log](#15-the-prototype-audit-log)
16. [What is verified here and what is from the official docs](#16-what-is-verified-here-and-what-is-from-the-official-docs)

## 1. What an event function is

Open WebUI has an internal **event bus**. Whenever something notable happens (the server starts, a user signs in, a role changes, a function is switched on, a model is created, a chat finishes) the server builds a small record, the **event**, and hands it to a list of sinks: socket cleanup, event functions, webhooks and user notifications (`backend/open_webui/events.py`, `EVENT_SINKS`).

An **event function** is a Python plugin whose single entry point, `Event.event(...)`, is called for **every** event, after the thing has already happened. It decides by the event name what to react to. It runs inside the server process, with the real FastAPI `app`, so it can read and write application state and the database, and it can register HTTP routes. It cannot veto or edit the action that triggered it.

**Two meanings of "event".** A tool's `__event_emitter__` (see [Tool.md](Tool.md)) sends live status messages to the chat UI of one user while the tool runs. This document is about the other kind: system events on the server-side bus, handled by an event function.

Event functions are a function type like Filter, Pipe and Action. Open WebUI picks the type from the class the file defines (`Pipe`, `Filter`, `Action`, `Event`; `backend/open_webui/utils/plugin.py`).

## 2. Events versus filters, tools, actions and pipes

| | Runs when | Triggered by | Can change the request or reply? | Typical use |
|---|---|---|---|---|
| **Event** (function) | Something happened anywhere in Open WebUI (about 180 event names) | The system, the admin, a user action, startup or shutdown | **No**: it runs after the fact | Audit log, provisioning, alerts, startup work, registering a route |
| **Filter** (function) | Around each chat request | A chat request | **Yes**: edits the request (`inlet`) and reply (`outlet`, `stream`) | Inject context, redact, block, rewrite |
| **Action** (function) | A user clicks a button under a message | The user | Acts on that message | "Export", "Summarize this" button |
| **Pipe** (function) | A user picks it as a model | A chat request | It *is* the model | Custom provider or agent |
| **Tool** (not a function) | The model decides to call it, or an MCP/OpenAPI server is attached | The model, during a chat | Returns data to the model | Translator, employee directory |
| **Event webhook** (no code) | The same events as above | The system | No | Send selected events to a URL or chat as JSON; configured in Admin > Settings > General > Events |

Rules of thumb: need to **change what the model sees or says** → Filter. Need the model to **do something** → Tool. Need a **button** → Action. Need to **react to the platform itself** (accounts, config, plugins, lifecycle) → Event. Need only to **forward** events to another system → the built-in event webhook, no code.

## 3. The lifecycle, step by step

Each row below was observed on the throwaway stack (section 16) and matches the code (`routers/functions.py`, `events.py`, `utils/plugin.py`, `main.py`).

| Moment | What happens to your function |
|---|---|
| **Create** (`POST /functions/create`) | The source is executed (`exec`) **immediately**, even though a new function is **inactive**: module top-level code runs, the class is instantiated. A syntax error, import error, exception or missing class rejects the create. No events are delivered (it is inactive). |
| **Enable** (`POST .../toggle` on an inactive function) | The server first delivers `function.enable_started` to all active event functions **plus this one** (the only case where an inactive function gets an event), **waits for them**, then writes `is_active=true`, then publishes `function.enabled`. Your function sees both. |
| **Valves saved** | `function.valves_updated` is published (in the background). The new values are read from the database on the next event. |
| **Source saved** (`POST .../update`) | The new source is executed before it is stored. On failure the save is rejected and, observed here, the **existing function is switched to inactive**. On success the next event runs the new code (a fresh module, a fresh `Event()`). |
| **Server starts** | After the database and runtime config are ready the server publishes `system.startup.started`; after the tool and terminal servers are initialised it publishes `system.startup.completed`. Active event functions are loaded from the database on the first of these. Module top-level code runs again in the new process. |
| **Any other activity** | The matching event is published and delivered to every active event function. |
| **Disable** (`POST .../toggle` on an active function) | `function.disable_started` is delivered and **awaited** *before* `is_active=false` is written. It is your last chance to tidy while active. `function.disabled` follows but is **not** delivered to you (you are inactive by then). |
| **Delete** | No `function.disable_started`, and `function.deleted` is **not** delivered to the function being deleted (observed). |
| **Server stops** | `system.shutdown.started` and `system.shutdown.completed` are published as the lifespan ends. **Observed here: neither reached the function**, in three stops (one `docker restart`, two `docker kill -s TERM`) on an idle single-process stack. Treat shutdown as best effort; see the Theme Designer Pro case study for the one plugin that depends on it. |

```
create ──► [exec module, inactive, no events]
enable ──► enable_started (awaited, you included) ─► is_active=true ─► enabled
          ...  every event: user.created, auth.login, config.updated, ...  ...
          valves saved ─► valves_updated ─► (valves re-read on next event)
disable ─► disable_started (awaited, last event you get) ─► is_active=false
restart ─► [process dies; nothing guaranteed]  ► startup.started ─► startup.completed
```

Two traps follow from the table:

- **Enable is not startup, and startup is not enable.** Toggling on does not replay `system.startup.*`, and a restart does not replay `function.enable_started`. If your setup must happen in both cases, handle both events and make the setup safe to run twice.
- **Startup events can arrive in either order and in the same millisecond.** They are delivered by separate background tasks; on the throwaway stack `startup.completed` was sometimes handled before `startup.started`. Do not rely on their order, and do not assume the tool servers are ready at `startup.started`.

## 4. The `Event` class contract, with a tested example

A file is an event function when it defines a top-level `class Event`. The server calls `Event.event(**params)` and ignores the return value. Source: `dispatch_event_functions` in `backend/open_webui/events.py`.

**What `event()` receives.** The server inspects your signature. Declare only what you need, or add `**kwargs` to receive all:

| Argument | Meaning |
|---|---|
| `event` | The payload as a `dict` (section 5). Sensitive keys are already removed. |
| `__event_name__` | `"user.created"`, `"system.startup.completed"`, ... Branch on this. |
| `__event_id__` | A UUID for this one occurrence. Use it as an idempotency key. |
| `__event__` | The same event as a pydantic object. |
| `__id__` | The id of **your** function. |
| `__app__` | The real FastAPI application (routes, `app.state`). Always set. |
| `__request__` | The HTTP request that caused the event, or `None` (it is `None` for `system.*`). |

`event()` may be `async def` (normal) or a plain `def`; both are accepted. A plain `def` runs **on the event loop**, so it blocks the whole server while it runs (section 10).

**Minimal example.** Tested on the throwaway stack: created, enabled, valve set to `hi`, a user was added, the function was disabled; the log lines were exactly the ones the code predicts.

```python
"""
title: Minimal Event
description: Example event function. Logs a line for a few events and shows the lifecycle checks.
version: 0.1
"""

import logging

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)  # lines appear in the server log (docker logs)


class Event:
    class Valves(BaseModel):
        greeting: str = Field(default='hello', description='Word printed in the log line.')

    def __init__(self):
        # Runs every time Open WebUI (re)loads this source: at create, at the first
        # dispatch after a save or restart. Keep it cheap and free of side effects.
        self.valves = self.Valves()

    async def event(
        self,
        event: dict,                 # the payload (redacted); always passed
        __event_name__: str = None,  # e.g. "user.created"
        __id__: str = None,          # the id of THIS function
        __app__=None,                # the FastAPI app (routes, app.state)
        **kwargs,                    # also offered: __event__, __event_id__, __request__
    ):
        # Called for EVERY event, so return early for the ones you do not care about.
        if __event_name__ == 'system.startup.completed':
            log.info('%s: server is up', self.valves.greeting)
        elif __event_name__ == 'user.created':
            log.info('%s: new user %s', self.valves.greeting, (event.get('subject') or {}).get('id'))
        elif __event_name__ in ('function.enable_started', 'function.disable_started'):
            # These fire for EVERY function, so check that the subject is this one.
            if (event.get('subject') or {}).get('id') == __id__:
                log.info('%s: I am about to be %s', self.valves.greeting, __event_name__.split('.')[1].replace('_started', 'd'))
```

Observed log (ids shortened): `hello: I am about to be enabled` (valve still at its default at that moment), `hi: new user b9accd04-...`, `hi: I am about to be disabled`.

The admin editor also has a starter: in **Admin > Functions > new function**, the "Function starter" drop-down offers **Filter** or **Event** and pastes an event template (`src/lib/components/admin/Functions/FunctionEditor.svelte`).

## 5. The event payload and the catalog

Real `user.role_updated` payload from the experiments (synthetic user):

```json
{
  "schema": "0.11.4", "id": "204861e1-...", "event": "user.role_updated",
  "resource": "user", "operation": "role_updated", "created_at": 1790909605,
  "instance_id": "1d75e80a-...", "version": "0.11.4", "source": "api",
  "actor":   {"id": "9183a248-...", "name": "Throwaway Admin", "email": "admin@example.com",
              "role": "admin", "created_at": 1790909396, "updated_at": 1790909396, "type": "user"},
  "subject": {"type": "user", "id": "ac0b2395-..."},
  "data":    {"role": "user"},
  "message": null
}
```

- `source` says how it came about (`api` for the admin panel, `admin`, `password` for a sign-in, `system` for lifecycle; the official docs also name `scim`, `oauth`, `trusted_header`).
- **Redaction.** Keys such as `password`, `token`, `api_key`, anything ending in `_key`, `_token` or `_secret` are dropped; strings are cut at 1000 characters; the actor is reduced to id, name, email, role and timestamps (`_sanitize`, `_actor` in `events.py`). Do not count on it to remove everything: `chat.finished` carries the assistant's reply text (up to 1000 characters), and `model.provider_request.failed` carries the last four characters of the API key.
- **The catalog.** The running server lists every name at `GET /api/events` (admin only). On this version it returns **181** events in 29 areas: `auth`, `user`, `group`, `chat`, `message`, `channel`, `model`, `knowledge`, `file`, `folder`, `note`, `memory`, `prompt`, `skill`, `tool`, `function`, `pipeline`, `config`, `system`, `calendar`, `automation`, `feedback`, `image`, `audio`, `terminal`, `notification`, `retrieval` and others. The official page says "170+"; ask your own server for the truth.
- **What the catalog does not contain.** There is **no event for a tool call**, for a model call's token usage, or for a user opening a page. For those use a Filter or a Tool wrapper (section 14).

## 6. Valves

Declare a pydantic `Valves` class and assign `self.valves = self.Valves()` in `__init__`. Admins edit them under **Admin > Functions > gear icon**. What was verified:

- Before **every** dispatch the server reads the saved values from the database and replaces `self.valves`, so a change takes effect on the next event with no restart. (Do not cache valve values at import time.)
- Valves are stored encrypted when `ENABLE_VALVE_ENCRYPTION=true`, which `Michael/docker-compose.yaml` sets by default: the database column held a Fernet token, not JSON. If `WEBUI_SECRET_KEY` changes, the old values cannot be decrypted and the function silently falls back to the defaults in its `Valves` class (`utils/valves.py`). Keep the key stable.
- Saving valves publishes `function.valves_updated`, so a function can react to its own configuration changing.
- Official docs also describe `UserValves` (per-user settings) for filters and tools. An event runs for the system, not for one signed-in user, so we used `Valves` only and did not test `UserValves` on an event function.

## 7. Install: admin UI and admin API

**Admin UI.** Admin Panel > Functions > **+** > choose the **Event** starter or paste your source > Save > turn the **enable toggle** on. A new function is inactive. Event functions have no "global" switch and no per-model attachment: an active one receives every event.

**Admin API.** The same routes as in [Filter.md](Filter.md) section 4.2 (all under `/api/v1/functions`, admin only; verified in `routers/functions.py`). The type is inferred, so the body is the same:

```bash
export OWUI=http://localhost:3000                 # your Open WebUI base URL
export TOKEN="<admin API key or session token>"   # set in your shell, do not commit

jq -n --rawfile src my_event.py '{id:"my_event", name:"My Event", content:$src, meta:{description:"example"}}' |
curl -sS -X POST "$OWUI/api/v1/functions/create" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @-

# the toggle FLIPS the flag: read first, toggle only if needed
curl -sS "$OWUI/api/v1/functions/id/my_event" -H "Authorization: Bearer $TOKEN" | jq '{type,is_active}'   # type must be "event"
curl -sS -X POST "$OWUI/api/v1/functions/id/my_event/toggle" -H "Authorization: Bearer $TOKEN"

curl -sS "$OWUI/api/events" -H "Authorization: Bearer $TOKEN" | jq '.events | length'                      # the catalog
```

Notes: the id must be a Python identifier (lower-cased); a bad id or a source that fails to load returns HTTP 400 with a generic message (the Python error is in the server log). Unknown ids answer **401**, not 404. For a repeatable install use a bootstrap script like `Michael/bootstrap/audit_log.py` (section 15).

## 8. Debugging

- **Logs.** Anything written with `logging` (`log = logging.getLogger(__name__)`) or `print` appears in the container log: `docker logs <container> 2>&1 | grep -i <your tag>`. `log.info` shows at the default level. The log prefix is `function_<id>:event:<line>`.
- **Load problems.** `Error loading module: <id>: <error>` is logged by `utils/plugin.py`, and the function is set inactive. `Event function failed for <id>` followed by a traceback is an exception inside your handler (section 10).
- **See what arrives.** Temporarily log `__event_name__` and `__event_id__` for every call, or write the full payload to a file in the container, then trigger the action in the UI. Remember a handler sees *every* event: log selectively on a busy server.
- **Check the function state.** `GET /api/v1/functions/id/<id>` shows `is_active` and `type`. A function that deactivated itself (failed load) shows `false` with nobody having touched it.
- **Reload.** Saving the source or restarting the container is enough; there is no hot-reload hook of your own.
- **Is my route there?** Use `curl` without a token (see section 9 for the SPA catch-all trap).
- **Where files go.** Inside the container the data volume is `/app/backend/data`; a file you write elsewhere (`/tmp`) disappears when the container is recreated, but survives a `docker restart`.

## 9. What an event function can do: routes, state, database

All proven on the throwaway stack with small synthetic functions.

**Register HTTP routes: yes, but mind the catch-all.** `__app__.add_api_route(...)` works. Open WebUI mounts the web app as a catch-all at `/`, and a route added **after** it is shadowed: a plain `add_api_route('/probe/append', ...)` answered with `index.html`, not with the handler. Insert the route **before** the mount named `static` instead (this is what Theme Designer Pro does, "position 529" in its log):

```python
from starlette.routing import Mount
__app__.add_api_route('/my/status', handler, methods=['GET'])
route = __app__.routes.pop()
at = next((i for i, r in enumerate(__app__.routes) if isinstance(r, Mount) and r.name == 'static'), len(__app__.routes))
__app__.routes.insert(at, route)
```

Guard it so it registers once (`any(r.path == '/my/status' for r in __app__.routes)`). **A route has no authentication unless you add it**: the probe answered an anonymous request with data. And **a route outlives the function**: after disabling and after deleting the function the route still answered (the closure keeps running) until the next restart.

**Add middleware: no.** `__app__.add_middleware(...)` raised `RuntimeError: Cannot add middleware after an application has started` (Starlette). Use routes, or a Filter for chat traffic.

**Read and write `app.state`: yes.** It is shared by all code in the process (other plugins included, no namespacing), and per process: a value set in one worker is invisible to the others.

**Database and config: yes.** Importing `open_webui.models.*` works (the probe route counted users with `Users.get_users()`). The runtime config is database-backed with no in-memory copy, so `await Config.upsert({'ui.default_user_role': 'pending'})` took effect at once: in the test the value was changed through the admin API to `user`, the server restarted, and a function reapplied `pending` on `system.startup.completed` (`Config.get` and `Config.upsert` from `open_webui.models.config`). These are internal APIs and can change between releases.

**Files:** the process user can write the data volume (the audit prototype writes `audit/events-*.jsonl` there, mode 0600).

## 10. Errors, ordering, blocking and several workers

**An exception does not break Open WebUI.** `dispatch_event_functions` catches every `Exception` per function, logs `Event function failed for <id>` with the traceback, and moves on. Observed: a function raising on `auth.login` left the sign-in successful, the function active, and the next function still ran. Nothing is retried.

**A failure to *load* is different.** If the module raises while it is executed (syntax error, failed import, exception in `__init__` or at top level, no class), the loader sets the function **inactive**. This also happens with nobody editing it: a function that raises only when some file exists was active until a restart, then came up inactive. Fix the code and enable it again.

**Ordering between event functions.** Active functions are read with a database query that has no `ORDER BY`, and they are called one after another for each event. Observed order was creation order, but nothing promises it; never make one function depend on another having run. For one event a slow handler delays the functions after it; events themselves are independent tasks.

**Awaited or fire-and-forget.** All events except the two toggle events are delivered by `asyncio.create_task`: the HTTP request that caused them does not wait (a 3 s handler on `auth.login` left sign-in at 0.21 s). `function.enable_started` and `function.disable_started` are **awaited** by the toggle request: a 3 s handler made the toggle take 3.01 s (and it delays the toggle of *other* functions too, since every active event function sees every toggle).

**Blocking code blocks everyone.** Handlers run on the server's event loop. A `time.sleep(4)` inside the handler made `/health` take 3 s. Use `await asyncio.sleep`, `await asyncio.to_thread(...)`, or async clients for any slow or blocking work (the audit prototype writes its file with `to_thread`).

**Module code can run more than once.** The module is executed at create, again at the first dispatch, and at each restart; at startup, when `startup.started` and `startup.completed` are handled at the same moment, we saw the module loaded twice in a row. Keep top-level code free of side effects and guard one-time work with a flag on `app.state`.

**Several workers or replicas.** Each worker is a separate process with its own module, its own `app.state` and its own routes. Observed with `UVICORN_WORKERS=2`:

- `system.startup.*` reached the function **in both workers** (pids 11 and 12): startup work runs once per worker.
- A request-scoped event (enable, valves saved, sign-in) is handled by **the one worker** that served that request. A route registered on that event existed in that worker only: 18 of 20 requests to the route fell through to the web app. Register routes on the startup event (every worker gets it) rather than on a request-scoped event.
- There is **no exactly-once delivery and no coordination**. Anything that must happen once (send mail, provision something) needs a lock or idempotency key, for example Redis `SET NX EX` keyed on `__event_id__` (official docs, section "Concurrency, multiple replicas, and exactly-once"; the Michael stack runs one worker, so this is not needed today).

## 11. Limits and gotchas

- **After the fact only.** You cannot block, delay or rewrite the triggering action. To stop something, use a Filter, or hold the object (for example keep a new account `pending`).
- **Delivered to everyone.** Your handler gets every event, including every other function's toggle and valve change. Filter by name first, by subject id second.
- **No delivery guarantee.** Fire-and-forget, in memory: events that happen while the server is down, or that arrive while it is stopping, are lost.
- **Shutdown is unreliable** (section 3): do not put anything essential there.
- **Disabling does not undo.** Routes, `app.state` values, background tasks and monkey-patches created by your code stay until the process restarts. Clean up in `function.disable_started`, and remember a deleted function never sees its own deletion.
- **The function lives in the data volume.** An event function is stored in the same database as everything else. If the volume is reset, the function is gone too and cannot re-apply anything; only a bootstrap script outside the volume can (section 14, rank 4).
- **No tool-call or token events** exist (section 5).
- **Internal APIs move.** `open_webui.*` imports are not a stable interface; re-test after every upgrade.
- **Unreviewed code at import.** Creating a function runs it at once, even before it is enabled (section 3).
- **Version.** Event functions need Open WebUI 0.10.0 or later (official docs); the lifecycle events used here were observed on 0.11.4.

## 12. Security guidance

- An event function is **server code with no sandbox**: it can read every user's data in the database, call admin functions, write files and open network connections. Only admins can create, edit, enable or delete it (`get_admin_user` on those routes in `routers/functions.py`); keep it that way and treat the authors of event functions as admins.
- It also **runs unattended**, on activity nobody is watching. Review the whole source before importing, especially community code; prefer files kept in this repository. Do not paste keys into the source: use Valves (encrypted at rest here) or environment variables.
- **Payload privacy.** Decide per function which events it receives data from. Log ids, not content: `chat.finished` and `message.*` carry user text. The audit prototype excludes those areas by default.
- **Routes need their own authentication.** Add an admin or token check, as Theme Designer Pro does (it answered 401 to an anonymous request and 200 to an admin).
- **Outbound calls.** A handler that posts events to an external service sends whatever it forwards; Grok is an outside service for us. Send metadata only, and review what the payload contains first.
- **Failure behavior.** Make the handler fail closed for data safety (write nothing rather than the wrong thing) and loud for operations (log with context).
- **Disable ≠ off.** If you must stop a misbehaving function immediately, disable it and then restart the container to drop routes and tasks it left behind.
- Never test a new event function on the live instance first: use a throwaway stack (distinct compose project name, host port and a fresh volume).

## 13. Case study: Theme Designer Pro

Theme Designer Pro (author `@G30`, version 1.8.1, MIT) is a community plugin installed on our instance and configured by `Michael/bootstrap/branding.py` (see [Filter.md](Filter.md) section 7). Its source, `Michael/tools/theme_designer_pro.py`, is local and untracked, about 15,200 lines. It is an **event function** (`class Event`). What it does to be a theme designer is plain Python; what makes it an event function is that it uses the lifecycle events to **install itself into the running server**. Read from its source, and checked by installing the local copy on the throwaway stack:

| Moment | What its `Event.event()` does |
|---|---|
| **First event it receives** (at startup, `system.startup.*`, or at enable) | Registers its web UI at `/api/v1/theme-designer` by inserting routes **before** the static mount (log: `Registered routes at /api/v1/theme-designer (position 529)`), and publishes its CSS and loader script fragments into a shared "static-asset registry" kept on `app.state`, so `/static/custom.css` and `/static/loader.js` are composed per request. Observed: the route answered 401 without a token and 200 for an admin after enabling. |
| **`function.enable_started` for itself** | Clears its "disabled" flag (guarded by a sequence number so rapid OFF-ON-OFF toggles cannot interleave), forces a republish and tells open browser tabs to refetch the theme. |
| **`function.disable_started` for itself** | The "last chance" hook: sets a disabled flag, withdraws its CSS fragment, and broadcasts a "disable" to open tabs so the theme disappears without a reload. Observed log: `Function is being disabled ... withdrawing CSS fragment` and, afterwards, its route answering **503**. The route itself stays registered, as section 9 predicts. |
| **Valve changes** | On every event it compares the valves with its last snapshot; a change to Canvas FX, sidebar or overlay style triggers a broadcast to browsers, and any change tells peer workers (over Redis, if `REDIS_URL` is set) to reread. Valves are read at compose time, not cached. |
| **`system.shutdown.started/completed`** | Cancels its Redis subscriber and heartbeat and closes its SSE streams "while the loop is still running". Its comments call this the only reliable shutdown hook. **Our experiment did not see shutdown events reach a function** on an idle single-process stack, so on our stack that cleanup either runs late or not at all; it has been harmless here because the process is exiting anyway. |

Why this case matters: it shows the pattern for features that must exist from the first page load (before any user acts): register on the first event, make registration idempotent, keep state on `app.state` and class attributes, and handle both startup and enable. It also shows the cost: 15,000 lines running in the server process, un-audited, with a route and a loader script served to every user. See [Filter.md](Filter.md) section 7 for how we harden it and for the rule not to press Save in its own designer.

## 14. Use cases for the Michael stack, ranked

The stack: about 20 users, tools for the translator and the employee directory, the User Context filter, a mail tool coming, the Lenovo theme, and the Grok and Davy models. Ranked by **value for effort**; each says honestly whether an event is the right mechanism. "Hook" means the event names used.

| # | Use case | Mechanism | Value | Effort | Risk |
|---|---|---|---|---|---|
| 1 | **Audit trail of admin and security changes** | **Event** | High | Low | Low |
| 2 | **Model-provider failure alert** (Davy, Grok) | **Event**, or the built-in webhook | High | Low | Low |
| 3 | **Startup self-test and status** (models and tool servers) | Event for the in-server view; **external check** for liveness | Medium | Medium | Medium |
| 4 | **Re-apply settings on every start** (drift guard) | Event for drift; **bootstrap script** for volume resets | Medium | Low | Medium |
| 5 | **Onboarding and account provisioning** | Built-in defaults first, then **Event** | Medium | Medium | Medium |
| 6 | **Usage counting** (chats per user and model) | Event for counts; **Filter or provider dashboard** for tokens and cost | Medium | Medium | Low |
| 7 | **Offboarding cleanup for the mail tool** | Event (`user.deleted`) | Low now, rises with the mail tool | Low | Medium |
| 8 | **Scheduled or periodic work** | **Host cron or `bootstrap --check`**, not Event | Low | Medium | Medium |
| 9 | **Guardrails** (block data to Grok) | **Filter**, not Event | High | Low | Low |

**1. Audit trail (the prototype, section 15).** Hook: `auth.*`, `user.*` (notably `user.role_updated`), `group.*`, `config.*`, `function.*`, `tool.*`, `model.*`, `system.*`. Sketch: build one JSON line per event (ids, names, source, actor, subject, metadata, no content) and append it to a daily file in the data volume. Why event: this is exactly what the bus is for, and the catalog already distinguishes "who made someone admin" (`user.role_updated`, with `source`) from other edits. Risks: contains emails; the file grows (valve `retention_days`); it is **not tamper-evident** (an admin or a function can edit it): ship it to an append-only store if that matters. If you only need to *forward* events to a collector, the built-in **event webhook** needs no code. Not covered: **tool calls**: there is no tool-call event; to audit what the translator or directory was asked, log in the tool itself or at the MCP server.

**2. Provider failure alert.** Hook: `model.provider_request.failed`; its `data` has `error_type` (`model_not_found`, `authentication_failed`, `rate_limited`, `server_failed`, `upstream_error`), `status`, `provider`, `base_url`, `requested_model`. Sketch: count failures per provider in a sliding window kept on `app.state`; when a threshold is crossed, log at ERROR once and optionally POST to a chat webhook (metadata only). Value: an expired Davy or Grok key or a certificate problem is otherwise discovered by a user. Risk: the payload includes the **last 4 characters of the API key** (`api_key_suffix`); do not forward it. Event or webhook? A webhook filtered to this event and a chat destination does it with **no code**; use a function only to add thresholds. (This event was read in the code, not triggered in the experiments.)

**3. Startup self-test.** Hook: `system.startup.completed` (every worker!) starting a background task. Sketch: `GET <connection>/models` for each configured connection, check the MCP endpoints, then keep the result on `app.state` and serve it on an admin-only route. Cautions: it runs once per worker; Grok is an outside service, so send nothing but the model-list request; and a check inside Open WebUI cannot tell you that Open WebUI is down. Better for liveness: the compose healthcheck and a bootstrap script with a check mode run from outside (`bootstrap/branding.py --check` is the existing example). Use the event only for "what does the server itself see".

**4. Settings drift guard.** Hook: `system.startup.completed`. Sketch: compare a few `Config` keys with valve values and `Config.upsert` the difference (proved: `ui.default_user_role` was changed to `user` and put back to `pending` by the function after a restart). **Right mechanism, honestly:** a *volume reset* deletes the function with everything else, so only the bootstrap scripts under `Michael/bootstrap/` can rebuild a fresh instance; the event only fights drift. The drift guard also silently reverts a setting an admin deliberately changed in the UI, so keep the list short and log every correction.

**5. Onboarding.** Hook: `auth.signup` and `user.created`. Sketch: add the new user to a default group, optionally post a welcome chat. Before writing code check what Open WebUI already does (default role, default group and OAuth group mapping), since the account exists when the event fires and the function can only react. Needs the group models (`Groups`); test on a throwaway first.

**6. Usage counting.** Hook: `chat.finished` and `chat.failed` (they carry `user_id`, `model_id`, `chat_id`). Sketch: increment per day, per user and per model in a small file or table. Limits: **no token counts** are in the event; temporary and API calls without a saved chat are not reported (`publish_chat_finished_event` returns early). For cost on Grok use a Filter `outlet` that reads usage, or the provider's own billing view.

**7. Offboarding for the mail tool.** Hook: `user.deleted`. Sketch: remove per-user mail tool state when an account is deleted. Only worth building when the mail tool stores per-user tokens.

**8. Periodic work.** An event function *can* start an `asyncio` task at startup, but it runs once per worker, dies with the process and shares the loop with the chat server. A host cron job calling a bootstrap script's check mode, or Open WebUI's own Automations, is the right place.

**9. Guardrails.** An event runs after the fact and cannot block anything. To keep sensitive data from the Grok model, use a Filter `inlet` attached to that model. An event can at most *flag* (for example log that a message was sent to Grok), which a Filter can do better.

## 15. The prototype: Audit Log

The single best use case (rank 1), generic and safe, is committed: `Michael/functions/audit_log.py` (the event function), `Michael/bootstrap/audit_log.py` (idempotent provisioning, same style as `bootstrap/user_context.py`), `Michael/tests/test_audit_log.py` (unit tests).

**What it records.** For each event whose name matches the `events` valve (default: `auth.*`, `user.*`, `group.*`, `config.*`, `function.*`, `tool.*`, `skill.*`, `pipeline.*`, `model.*`, `knowledge.access_updated`, `prompt.access_updated`, `system.*`) one line of JSON in `<data volume>/audit/events-YYYY-MM-DD.jsonl`: time (UTC), event id, name, source, actor id/email/role, subject, the event's `data`, instance id, pid. Chats, messages, files and memories are excluded by default.

**Valves:** `events` (comma list, `*` allowed), `directory` (empty = `<data>/audit`), `include_email`, `log_to_console` (one `AUDIT ...` line per record in the server log), `retention_days` (0 = keep all; otherwise old daily files are deleted at startup).

**Provision** (admin credentials from `Michael/.env`, see `.env.example`):

```bash
python3 Michael/bootstrap/audit_log.py        # creates or updates the source, enables it; valves are left as they are
```

**Read it:**

```bash
docker exec <container> sh -c 'cat /app/backend/data/audit/events-*.jsonl' |
  jq -c '[.ts, .event, .actor.email, .subject.id, .data]'
```

**Tested** on a throwaway stack: the script is idempotent (second run reports "already up to date"); admin actions produced `function.enabled`, `auth.login`, `user.created`, `user.updated`, `user.role_updated`, `function.valves_updated`, `user.deleted` and, after a restart, `system.startup.*`, all with the right actor; a chat created by a synthetic user was **not** recorded and its title appears nowhere in the files; with `directory` pointing at an unwritable place, sign-in still worked and the failure was logged as `audit record for <event> was not written`; the seven unit tests pass (`docker exec -i <container> python3 - < Michael/tests/test_audit_log.py`, after `docker cp`-ing the function to `/tmp/audit_log.py`).

**Limits.** Not tamper-evident; one file per day shared by all workers (single-line appends do not interleave, so it is safe with several workers on one volume, but replicas on separate volumes each have their own files); the order of two lines within one millisecond is not guaranteed; events lost while the server is down or stopping are lost; it does not see tool calls (section 14); shutdown is never recorded (section 3). Do not apply it to the live instance without a review of the valves, in particular `include_email` and `retention_days`.

## 16. What is verified here and what is from the official docs

**Verified from this repository's code** (read at the time of writing): `backend/open_webui/events.py` (event model, catalog, redaction, sinks, `dispatch_event_functions`), `utils/plugin.py` (type detection, loading, deactivation on load failure, cache), `models/functions.py` and `routers/functions.py` (routes, toggle ordering, create/update execute the source, delete), `main.py` (startup and shutdown publishing, `GET /api/events`, the SPA mount), `utils/valves.py`, `utils/middleware.py` (`chat.finished`), `src/lib/components/admin/Functions/FunctionEditor.svelte` (Event starter); `Michael/tools/theme_designer_pro.py` (read, not committed).

**Verified by experiment** on a throwaway stack (Open WebUI 0.11.4 from the image whose `events.py`, `routers/functions.py` and `main.py` match this tree byte for byte; project `owui-events-m17`, own volume, synthetic users; removed afterwards): which events a function receives on create, enable, valves, update, disable, delete, restart and stop; the payload shapes; exception isolation and the deactivation on a failed load; ordering; async versus blocking handlers; awaited toggle events; route registration, the SPA shadowing, no authentication, survival after disable and delete; middleware refused; `app.state` and database access; `Config.upsert` at startup; two-worker behavior; encrypted valves; Theme Designer Pro's enable, disable, route and logs; the Audit Log prototype; the minimal example in section 4.

**Not observed:** shutdown events reaching a function (none did, on an idle single-process stack; with live websocket clients it was not tested); `model.provider_request.failed` and `chat.finished` delivery (read in code only); behavior with several replicas on separate hosts or with Redis; sync (`def`) handlers (code read; blocking behavior was shown with a blocking call inside an `async def`).

**Taken from the official documentation** (<https://docs.openwebui.com/features/extensibility/plugin/functions/event/> and the Functions index, valves and "Under the Hood" pages), not re-tested: the "new in 0.10.0" statement; the `source` values `scim`, `oauth` and `trusted_header`; the example use cases (welcome chat, email verification, approval queue, ToS gate and others); the Redis `SET NX EX` pattern for exactly-once work and the `RedisLock` helper; `UserValves` as a concept; the statement that events fire on every replica; the Admin UI path for event webhooks; the "170+" event count (the running server reported 181).
