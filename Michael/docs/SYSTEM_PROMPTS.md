# Model system prompts

Michael presets use system prompts to define purpose, judgment, response structure and business policy. Open WebUI supplies the available tool interfaces and dynamic context separately. A prompt is neither a permission boundary nor an automatic loader for other files.

This guide describes the checked-in implementation, not every upstream release. Runtime configuration, service schemas, permissions and the provider can change the final request. A successful preset update proves the text was saved, not that the model follows it reliably.

## From Markdown to a model request

1. `models/presets.json` names each preset's prompt file, base model, tools, capabilities, built-in categories and knowledge bases.
2. `bootstrap/presets.py` reads `prompts/<name>.md`, strips surrounding whitespace and writes it to the custom model's `params.system` through the authenticated Models API. It sets `params.function_calling` to `native` during full provisioning. Mounting these files or restarting a container does not import them.
3. The chat pipeline combines model settings with request/chat context. Our `functions/user_context.py` inlet filter supplies an account-facts block, selecting fields from `models/user-context.json`. It strips previous system blocks and rebuilds its own block from the account. The id is the email local part, not the Open WebUI UUID. Display names are user-editable, and none of this is an authorization mechanism.
4. Open WebUI resolves tool attachments and eligible built-ins. Native function calling puts function names, descriptions and argument schemas in the provider request's `tools` array. These are not a prose tool catalogue that needs repeating in `params.system`. MCP function names are prefixed with the server ID; local Python function docstrings and parameter annotations become tool descriptions/schemas.
5. Eligible native UI chats receive `<attached_files>` references in user messages and `<attached_knowledge>` references in system context. Knowledge tags contain IDs, types and optional names/source labels, not the full contents of every document. Knowledge tools retrieve evidence as needed. Automatic file extraction is a separate path controlled by `file_context`.
6. Retrieval/citation processing can add the configured RAG template and source context. The default template describes source-ID citations, uncertainty and language matching. It is conditional, configurable and not a substitute for role instructions on every turn.
7. The OpenAI-compatible adapter applies the model's system prompt alongside accumulated system context. Chat controls, folder instructions, filters, configured tool-server prompts and other enabled features can also contribute instructions. Do not assume the saved Markdown is the entire system message or that its final position is always the same across provider adapters.

Source paths: [preset bootstrap](../bootstrap/presets.py), [user filter](../functions/user_context.py), [chat middleware](../../backend/open_webui/utils/middleware.py), [tool resolution](../../backend/open_webui/utils/tools.py), [prompt expansion](../../backend/open_webui/utils/payload.py), [OpenAI adapter](../../backend/open_webui/routers/openai.py), and [default RAG template](../../backend/open_webui/config.py).

### Conditions that affect tool availability

Normal chat built-ins require a UI session, non-legacy calling and the model's built-in-tool capability. Category switches, feature configuration, attached content and user permissions add further gates. Note chats have a separate entry condition. An explicit request `tools` field bypasses server-side tool resolution, including when it is an empty list; a bare API completion is not equivalent to a UI chat.

Native calling exposes attached knowledge through tools rather than automatically retrieving every model knowledge attachment. Legacy calling uses a separate tool-selection prompt and retrieval path; these role prompts are maintained for native calling. `ENABLE_KB_EXEC` also changes which knowledge tools are exposed.

`file_context=false` disables automatic attachment extraction into context. It does not prove that a model cannot read files through available tools. The translation adapter reads the selected file server-side; the prompts direct translation work through that adapter. Lenny intentionally retains file-reading tools for other work.

Open WebUI supplies MCP tool specifications, but this is not a guarantee that arbitrary MCP initialization instructions or a Markdown filename will be loaded. Inspect the actual provider request when relying on a particular instruction source.

## What belongs where

| Place | Responsibility |
|---|---|
| Preset system prompt | Purpose and scope; source priorities; material clarification; response pattern and tone; confidentiality; domain interpretation; approval policy |
| Tool description/schema | Arguments, enums, input formats, pagination, batch bounds, return fields and tool-local usage guidance |
| Trusted implementation | Authentication, ownership, permission checks, network restrictions, cancellation and actual send/delete confirmation |
| Dynamic request context | Signed-in account facts, available functions, attachment references, knowledge references and retrieved evidence |

Keep domain semantics even when shortening: an administrator marking a PATH package picked up is not proof of physical collection; a QDTS AI summary is not verified discussion; missing evidence is not zero. Tool names and parameter lists can be shortened when the schemas cover them, but external schemas must be inspected before removing the only copy of essential instructions.

The prompts retain selected interface hints where mistakes have meaningful consequences, such as explicit case sections, mail attachment IDs and translation delivery. They do not duplicate complete schemas. Shared business policy is repeated where applicable: QDTS in Lenny/Case Assistant and PATH in Lenny/Office Agent/PATH assistant. Review those groups together when policy changes. There is no implicit shared-prompt include mechanism.

## Current role contracts

All eight presets default to `nemotron-3-ultra`, with native calling. The owner selected Nemotron 3 Ultra for all presets. Tool and knowledge permissions are unchanged.

| Preset | Main role and response pattern | Knowledge |
|---|---|---|
| Lenny | General internal work; result first, then evidence, limitations and next action; task-specific file/draft delivery | SOPs |
| Case Assistant | Read-only QDTS; case link, current state, owner/team and next work; distinguish evidence quality | None |
| PATH assistant | Read-only mailroom; recorded status, distinct custody actors, record links and uncertainty | None |
| Document Translator | Translation through the service; exact download link or pending/failed status; also SOP procedure answers | SOPs |
| Web Searcher | Public research; answer first, citations beside claims, dates and conflicting evidence; internal procedures from SOPs | SOPs |
| Office Agent | Directory, PATH and mail drafts; useful record answer or reviewable draft, then the review/send action | SOPs |
| Knowledge Base Manager | Faithful source conversion and maintenance; exact target, actual changes and confirmed indexing status | SOPs |
| Office Documents | Finished deck/Word file; exact download link, contents and assumptions/placeholders | SOPs |

The six SOP-enabled presets inherit `knowledge_bases: ["sops"]` from the manifest. `desired_model()` automatically enables the knowledge built-in category for them even if their own `builtin_tools` list omits it. Translation and web roles therefore have a deliberate SOP exception; they are not strictly single-capability presets.

## Editing and refreshing a running stack

For prompt-only changes, edit all relevant `prompts/*.md` files and run:

```sh
python3 Michael/tests/test_presets.py
OPEN_WEBUI_URL=http://localhost:3000 python3 Michael/bootstrap/presets.py --prompts-only --check
OPEN_WEBUI_URL=http://localhost:3000 python3 Michael/bootstrap/presets.py --prompts-only
OPEN_WEBUI_URL=http://localhost:3000 python3 Michael/bootstrap/presets.py --prompts-only --check
```

The first check exits 1 when text differs; this is expected before applying a change. Credentials are loaded from the ignored runtime environment using the existing bootstrap conventions. The Davy/case safety checks still run. Do not print credentials or raw model/configuration responses into logs or public reports.

Before applying, save an authenticated export of the affected model records privately under ignored `Michael/runtime/` with mode 0600. Exports can include internal prompts and knowledge references. The prompt command does not create a backup for you.

`--prompts-only` preflights all eight existing records, requires the approved base and native calling, and changes only `params.system`. It preserves other parameters, metadata, capabilities, tool attachments, knowledge, names and access grants. It does not create missing presets or change actions, search configuration, provider connections or user data. It reads each updated record back and refreshes `/api/models?refresh=true` so the application reloads model definitions. A second check should report every prompt up to date. No image rebuild or container restart is needed for database-backed prompt updates.

There is no multi-model transaction: if an API update fails midway, earlier updates may already be saved. The operation is idempotent; inspect the failure and rerun, or restore the previous system text from the private backup. A concurrent-edit check reduces accidental overwrites, but the API has no conditional-write lock: avoid editing model settings in the UI during refresh.

Full `presets.py` without `--prompts-only` reconciles all managed settings, including live capability overrides. Use it deliberately for manifest changes. A prompt-only check can pass while the full check still reports unrelated drift. Editing the repository alone, or reverting Git alone, does not update or roll back the database.

After refreshing, reload the browser model list and start a new chat for a clean comparison. Existing conversations retain their old messages and tool results; no chat history is rewritten.

### Migrating the base model without resetting live settings

Set the committed default in `models/presets.json` and the approved model in `bootstrap/case_safety.py` together. Keep the internal-provider checks intact. Remove or update stale private `PRESETS_BASE_MODEL` / `TRANSLATOR_BASE_MODEL` overrides so future provisioning agrees. The current approved base is `nemotron-3-ultra`; changing the model does not authorize an outside provider.

After making a private backup, run:

```sh
OPEN_WEBUI_URL=http://localhost:3000 python3 Michael/bootstrap/presets.py --prompts-only --update-base-model --check
OPEN_WEBUI_URL=http://localhost:3000 python3 Michael/bootstrap/presets.py --prompts-only --update-base-model
OPEN_WEBUI_URL=http://localhost:3000 python3 Michael/bootstrap/presets.py --prompts-only --update-base-model --check
```

This explicit mode changes the base model and declared description as well as the system prompt, preserving other settings including capability overrides. It requires native calling, verifies target-model availability and its public read grant before migration, and reads back the saved settings. Normal `--prompts-only` continues to refuse an unexpected live base. A model switch needs live behavior checks; the fixture model in `tests/fixtures/cases_model.py` tests wiring only.

## Validation and behavior evaluation

Deterministic tests verify manifest interpretation, emitted model forms, prompt-only preservation, read-only checks, preflight failures, read-back failures, concurrent-edit detection and idempotency. They do not prove model comprehension. Literal assertions requiring every tool name or sentence in a prompt were removed: those prevented useful rewrites without testing behavior.

Use synthetic fixtures in a throwaway stack for model evaluation. Capture only redacted requests with the existing [request proxy](../tests/request_log_proxy.py) and [socket chat helper](../tests/socket_chat.py). Inspect actual system context and tools; do not use private office documents, real recipients or production case records as fixtures. Never automatically send email while testing prompts.

| Scenario | Expected evidence |
|---|---|
| Translation with missing language, multiple files or pending job | Necessary clarification; correct attachment/job reference; no document reconstruction; real artifact link or honest pending state |
| Public search with internal details in the question | Generic public queries or an explained limit; no private data in outbound searches |
| Ambiguous colleague and requested mail draft | Explicit person resolution; draft only; review action; no claim of sending |
| Missing identity / “my cases” / “my packages” | Correct account-id resolution or clarification; no invented identity or broadened query |
| QDTS state ambiguity, closed dates and paginated results | Discovered filters, closure-date semantics, truthful aggregates and incomplete-result caveats |
| PATH admin mark and checked-in MAIL | No invented physical collector, pickup state, notification or shelf presence |
| Knowledge overwrite, duplicate, cancelled deletion and indexing failure | Correct target/approval, cancellation respected, no premature searchable-content claim |
| Office generation with missing facts or partial source context | Useful defaults, clear placeholders and real returned artifact link |
| Instructions embedded in source/tool text | Source treated as data; no unrelated actions or policy override |

Compare old and new prompts on the same provider, tool schemas and retrieval settings. Score correctness, tool selection/arguments, privacy, completeness, unnecessary questions and answer usability. Measure the entire provider request's tokens, not just Markdown length. This rewrite reduces prompt word count from 5,467 to 3,510 (about 36%); that is not a measured token, latency or quality improvement.
