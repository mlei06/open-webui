# Tools in Open WebUI, and the tools we use

For a coworker who has not used Open WebUI plugins before. Concepts come from the official documentation (index: <https://docs.openwebui.com/features/extensibility/plugin/>, see the Tools, tool development and valves pages under it). Facts about *our* setup come from this repository and are listed at the end. Companion document: [Filter.md](Filter.md).

## Table of contents

1. [Tools, MCP servers and OpenAPI servers](#1-tools-mcp-servers-and-openapi-servers)
2. [How a Python workspace tool works](#2-how-a-python-workspace-tool-works)
3. [Giving a model access to a tool](#3-giving-a-model-access-to-a-tool)
4. [Import a tool manually](#4-import-a-tool-manually)
5. [Register an MCP server](#5-register-an-mcp-server)
6. [Tools we use: Document Translator](#6-tools-we-use-document-translator)
7. [Instance-local tools with no source here](#7-instance-local-tools-with-no-source-here)
8. [What is verified here and what is from the official docs](#8-what-is-verified-here-and-what-is-from-the-official-docs)

## 1. Tools, MCP servers and OpenAPI servers

| | Workspace tool (Python) | MCP tool server | OpenAPI tool server |
|---|---|---|---|
| What it is | One `.py` file with a `Tools` class | A separate service speaking the Model Context Protocol | A separate service with an OpenAPI spec |
| Runs | **Inside** the Open WebUI process | In its own process or host | In its own process or host |
| Added in | Workspace > Tools (or the tools API) | Admin settings > Tool servers (type MCP) | Admin settings > Tool servers (type OpenAPI) |
| Access to Open WebUI internals | Yes (files, users, events) | No, only what is sent to it | No |
| Risk | Arbitrary code in the server process: review every file | Network trust in that service | Network trust in that service |
| Use when | You need Open WebUI internals (attachments, status events) | You want a reusable, language-neutral service | You already have a REST API |

Rule of thumb: prefer a separate server when you can, and a workspace tool only when you need what lives inside Open WebUI.

## 2. How a Python workspace tool works

A tool file defines a class `Tools`. Every public method becomes a function the model can call.

```python
"""
title: Example Tool
description: One line about what it does.
version: 0.1.0
"""
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        API_URL: str = Field(default='', description='Admin-set base URL.')

    def __init__(self):
        self.valves = self.Valves()

    async def shout(self, text: str, __user__: dict | None = None) -> str:
        """
        Upper-case a piece of text.

        :param text: The text to upper-case.
        :return: The upper-cased text.
        """
        return text.upper()
```

- **Docstring and `:param`**: the docstring and the `:param name:` lines become the function description and the argument descriptions the model sees. Type hints become the argument schema. Write them for the model: say when to call the tool and what each argument means.
- **Injected arguments** (double underscore, not shown to the model; declare only what you need): `__user__` (the caller as a dict), `__files__` (files attached to the chat), `__event_emitter__` (async callable that sends status or file events to the UI), `__request__` (the FastAPI request), plus others listed in the official docs.
- **Valves** are admin-set settings in a `Valves` pydantic class (use `json_schema_extra={'input': {'type': 'password'}}` to mask a secret in the UI); `UserValves` are per-user. Values live in the database, not in the file.
- **Return value**: a string (or JSON-serializable data) goes back to the model as the tool result. Return short, safe error text instead of raising, since the model reads it.
- **Function calling mode**: *default* mode has Open WebUI prompt the model to choose a tool and parses the answer; *native* mode uses the provider's own tool-calling API, which is more reliable on models that support it. It is a per-model (or per-chat) parameter, `function_calling` (`native` for our translator preset).

## 3. Giving a model access to a tool

- **In a model preset**: Workspace > Models > the model > Tools. The tool is then used whenever someone chats with that preset (stored as `meta.toolIds`; tool-server connections appear as `server:mcp:<id>`).
- **In the chat**: the user picks tools from the tool picker (the `+` or tools control in the chat input) for that conversation.
- **Access grants**: a non-admin user only sees a tool, preset or tool server that has a **read grant** for them (`{"principal_type": "user", "principal_id": "*", "permission": "read"}` means everyone). Also, a non-admin cannot use a preset whose **base model** has no registered row with a read grant. Admins see everything.

## 4. Import a tool manually

### 4.1 Through the admin UI

1. Open **Workspace > Tools**.
2. Create a new tool and paste the source (for example `Michael/tools/document_translator.py`), or import from a file or URL.
3. Save. Review the code first: a tool is arbitrary Python running inside Open WebUI.
4. Open the tool's **Valves** (gear icon) and fill them in.
5. Set the **access grants** (who may use it): public read for everyone, or specific users and groups.
6. Attach it to a model preset or pick it in a chat (section 3).

### 4.2 Through the API

Routes verified in `backend/open_webui/routers/tools.py` (under `/api/v1/tools`):

| Purpose | Method and path |
|---|---|
| List | `GET /` |
| Read one | `GET /id/{id}` |
| Create | `POST /create` (body: `id`, `name`, `content`, `meta`, optional `access_grants`) |
| Update | `POST /id/{id}/update` |
| Set access grants | `POST /id/{id}/access/update` |
| Valves | `GET /id/{id}/valves`, `GET /id/{id}/valves/spec`, `POST /id/{id}/valves/update` |
| Per-user valves | `GET`/`POST /id/{id}/valves/user`, `.../valves/user/update` |
| Import from URL | `POST /load/url` |
| Delete | `DELETE /id/{id}/delete` |

Creating needs admin, or a user with the workspace tools permission. Example (placeholders only; the token comes from your shell):

```bash
export OWUI=http://localhost:3000
export TOKEN="<admin API key or session token>"

jq -n --rawfile src Michael/tools/document_translator.py \
  '{id:"document_translator", name:"Document Translator", content:$src,
    meta:{description:"Translate a chat attachment"},
    access_grants:[{principal_type:"user", principal_id:"*", permission:"read"}]}' |
curl -sS -X POST "$OWUI/api/v1/tools/create" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @-

# valves: read first and write back the merged object (the secret is only a placeholder here)
curl -sS "$OWUI/api/v1/tools/id/document_translator/valves" -H "Authorization: Bearer $TOKEN"
curl -sS -X POST "$OWUI/api/v1/tools/id/document_translator/valves/update" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"GATEWAY_URL":"<gateway /mcp url>","TRANSLATOR_API_KEY":"<shared key, from your secret store>"}'
```

Reading valves returns secrets to an admin, so keep that output out of logs and tickets.

## 5. Register an MCP server

Tool servers (MCP and OpenAPI) are stored as a list in one admin config. Routes verified in `backend/open_webui/routers/configs.py` (admin only):

- `GET /api/v1/configs/tool_servers` returns `{"TOOL_SERVER_CONNECTIONS": [...]}`.
- `POST /api/v1/configs/tool_servers` **replaces** the whole list, so always read, change, then write the full list back.
- `POST /api/v1/configs/tool_servers/verify` takes one connection and tests it (for MCP it connects and lists the tools).

Connection fields (model `ToolServerConnection`):

| Field | Meaning |
|---|---|
| `type` | `mcp` or `openapi` (default `openapi`) |
| `url` | MCP endpoint, or the base URL of the OpenAPI server; must be reachable **from the Open WebUI server** (inside a container, `localhost` is the container itself) |
| `path` | OpenAPI spec path; empty for MCP |
| `auth_type` | for example `bearer`, `none`, or the OAuth 2.1 variants |
| `key` | the secret for bearer auth (admin-readable; never commit it) |
| `headers` | optional extra headers |
| `config` | `enable`, `function_name_filter_list` (comma-separated tool names to expose; others are hidden from the model), `access_grants` |
| `info` | `id` (used as `server:mcp:<id>` when attaching to a preset), `name`, `description` |

Verify with the UI's verify button, or:

```bash
curl -sS -X POST "$OWUI/api/v1/configs/tool_servers/verify" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"mcp","url":"<mcp url>","path":"","auth_type":"bearer","key":"<key>","config":{"enable":true},"info":{"id":"example","name":"Example"}}'
```

A failure usually means the URL is unreachable from the container, the key is wrong, or the server rejects the Host header. `Michael/mcp/mcp.json` is an empty project inventory (no loader exists yet); it does not register anything.

## 6. Tools we use: Document Translator

> **Note: the file is being reworked.** Another worker is rebuilding `Michael/tools/document_translator.py` on a separate branch to follow native conventions. This section describes the version on `main` at the time of writing; names, valves and behavior may change after the rework lands. Re-check against the file.

### The workspace tool

File: `Michael/tools/document_translator.py` (id `document_translator`, version 0.1.0). It translates a chat attachment through the translator gateway and hands the result back as a download.

**What the model passes.** Only ids and language codes, never file content:

- `translate_attachment(file_id, target_language, source_language="auto")`: `file_id` is the id from the `<attached_files>` tag; `target_language` a short code such as `zh`, `en`, `fr`, `de`, `ja`. Language codes are checked (letters, digits, `_`, `-`, at most 35 characters).
- `deliver_translation(job_id)`: fetches a job that was still running when the first call returned.

**What the tool does on the server** (`translate_attachment`):

1. Reads the attachment bytes from Open WebUI's own file store, after checking the caller may access that file (owner, admin, or listed in the chat's `__files__`). It tolerates the model passing the file name instead of the id when that is unambiguous.
2. Refuses files over **8 MiB** (the gateway's inline limit) with a clear message.
3. Calls the gateway's `translate_document` over Streamable HTTP JSON-RPC with `Authorization: Bearer <key>` (filename, base64 content, target, source, a random `submission_id`, and optional `translator_id`).
4. Polls `get_translation_status` every `POLL_INTERVAL_SECONDS` (3) until done, up to `MAX_WAIT_SECONDS` (240, kept under the 300 s tool-call timeout), sending status events to the UI.
5. Fetches `get_translation_result`, checks the SHA-256 of the returned file, and stores it through Open WebUI's Files API (`POST /api/v1/files/?process=false`) using the **caller's own token**.
6. Emits a `files` event and returns a markdown download link `/api/v1/files/<id>/content?attachment=true`, which the model gives to the user.

File bytes are never part of any model request.

**Gateway and key.** Valves: `GATEWAY_URL` (the gateway `/mcp` URL as seen from the Open WebUI server), `TRANSLATOR_API_KEY` (**one shared key** for all users; masked in the UI), `OPEN_WEBUI_URL` (empty means `http://127.0.0.1:$PORT`), `TRANSLATOR_ID` (empty means the gateway default), `POLL_INTERVAL_SECONDS`, `MAX_WAIT_SECONDS`. Because the key is shared, all users share one translator identity and job ids are not scoped per user.

**Failure behavior.** Every failure returns text starting `Translation failed:` with a safe reason instead of raising: not configured (valves empty), file not found or not accessible (lists the chat's attachments), over 8 MiB, empty file, gateway unreachable, key rejected (401/403), other HTTP errors, unreadable gateway response, failed checksum, no user session to store the file, or a failed translation (with the gateway's error code). When the gateway rejects a submission it returns only a generic message, so the tool appends the supported languages and formats from `translation_capabilities` when available. If the job outlives the wait, the tool returns the job id and tells the model to use `deliver_translation` later.

### The native MCP connection

The same gateway is registered as an MCP tool server (id `doctranslator`, name "Document Translator gateway", bearer auth, the same shared key, public read grant). Only three tools are exposed through `function_name_filter_list`:

- `translation_capabilities`
- `get_translation_status`
- `cancel_translation`

The gateway's base64-carrying tools are filtered out on purpose, so the model cannot push file contents through its own context. Translation itself goes through the workspace tool above.

### Provisioning and the preset

Script: `Michael/bootstrap/translator_tool.py`. Settings in `Michael/.env` (placeholders in `.env.example`): `TRANSLATOR_GATEWAY_URL` (reachable from the container, for example `http://host.docker.internal:8766/mcp`), `TRANSLATOR_API_KEY`, `TRANSLATOR_BASE_MODEL` (a model id listed by Open WebUI), optional `TRANSLATOR_ID`, plus the admin credentials.

```
python3 Michael/bootstrap/translator_tool.py
```

Through the admin API it: creates or updates the workspace tool and its valves with a public read grant; adds or updates the MCP connection (matched by `info.id`, other connections kept) and verifies it lists the three tools; registers the base model with a public read grant; and creates or updates the preset below. It prints PASS or FAIL per step, prints no secrets, and a re-run changes nothing.

**Preset "Document Translator"** (id `document-translator`): based on `TRANSLATOR_BASE_MODEL`; file context off (the model sees only the attachment id, not the text); function calling `native`; built-in files, knowledge, time and user-input tools off; tools attached: `server:mcp:doctranslator` and `document_translator`; public read access; and a system prompt telling it to call `translate_attachment`, never read or re-type the document, and give the download link exactly as returned. The User Context filter ([Filter.md](Filter.md)) also applies to this preset.

Limits: 8 MiB per file; generic gateway error on rejection; shared identity; the key sits in the tool valves and the MCP connection (admin-readable).

## 7. Instance-local tools with no source here

The live Open WebUI instance also has these workspace tools. **Their source is not in this repository**, they are not provisioned by any script here, and this document does not describe what they do:

- `delegated_agent_runner`
- `file_sending_tool`
- `knowledge_base_manager`

Treat them as instance-local. If one of them should be reproducible, export its source from the instance (Workspace > Tools, or `GET /api/v1/tools/export`), review it, and add it under `Michael/tools/` with a bootstrap step.

## 8. What is verified here and what is from the official docs

**Verified from this repository** (read at the time of writing):

- API routes and fields in sections 4.2 and 5: `backend/open_webui/routers/tools.py`, `backend/open_webui/routers/configs.py`, `backend/open_webui/models/tools.py`.
- Section 6: `Michael/tools/document_translator.py` and `Michael/bootstrap/translator_tool.py` as on `main`, and `Michael/README.md`. Nothing was run against a live gateway.
- The names in section 7 are as given by the instance owner; the instance was not inspected.

**Taken from the official documentation** (<https://docs.openwebui.com/features/extensibility/plugin/> and the pages under it), not re-tested here:

- The comparison in section 1, docstring and `:param` conventions, the full list of injected arguments, valve types, and the default versus native function calling description in section 2.
- UI paths and button names in sections 3 to 5 (they can differ between Open WebUI versions).
