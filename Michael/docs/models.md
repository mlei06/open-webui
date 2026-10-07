# Models: presets, system prompts, base model

A **preset** is an Open WebUI custom model that wraps a base model with a system prompt, tools, capabilities,
knowledge and actions. `models/presets.json` declares them, `prompts/<name>.md` holds each system prompt, and
`bootstrap/presets.py` writes them through the authenticated Models API. Mounting a prompt file or restarting a
container imports nothing: the database is the source of truth, so a change takes effect only after the bootstrap
has run.

## The presets

All eight presets use native function calling, the `user_context` filter ([functions.md](functions.md)) and the base
model `laguna-s-2.1` on the internal provider. All have public read access (users see them in the model
selector).

| Preset (id) | Purpose | Web search | Terminal | Knowledge (SOPs) |
|---|---|:-:|:-:|:-:|
| **Lenny** (`lenny`) | All-in-one assistant: QDTS cases, charts, mail drafting, PATH, translation, PowerPoint and Word, web search; delegates tool-heavy or background jobs | on by default | yes | none |
| **Case Assistant** (`case-assistant`) | Read-only QDTS specialist (cases, notes, tasks, people, teams) with charts | off | yes | none |
| **PATH assistant** (`path`) | Read-only packages, checked-in mail and custody history | off | no | none |
| **Document Translator** (`document-translator`) | Translates attached documents and returns the file | off | yes | yes |
| **Web Searcher** (`web-searcher`) | Public research with cited sources | on by default | no | yes |
| **Office Documents** (`office-documents`) | Lenovo-styled PowerPoint decks and Word documents | off | yes | yes |

### What each preset has attached

| Tool or connection | Lenny | Case Assistant | PATH | Doc Translator | Web Searcher | Office Documents |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| QDTS cases (`qdts`) | x | x | | | | |
| PATH (`path`) | x | | x | | | |
| Mail drafts (`mail`) and the review-and-send action | x | | | | | |
| Translator gateway (`doctranslator`) and Document Translator tool | x | | | x | | |
| PowerPoint (`generate_slide_pptx`) and Word (`generate_docx_documents`) generators | x | | | | | x |
| Visuals toolkit (`visuals_toolkit_v4`) | x | x | | | | |
| Workspace files (`workspace_files`) | x | x | | x | | x |
| Delegation (`delegate_agents`) | x | | | | | |
| Built-in tools | time, user input, files, web search, memory, chat history, tasks, automations | time, user input | time, user input | knowledge | time, web search, knowledge | time, user input, knowledge |

Per-preset notes:

- **Lenny** also has the built-in memory, chat-history, task-management and automation tools, and the memory capability. They
  let it remember a user's team and preferences, search their own past chats, show a step checklist on long requests, and
  schedule recurring runs from chat (see "Capabilities and defaults" below). It has no employee directory and no knowledge bases: people, roles and teams come from QDTS. Its usage
  guides are skills ([skills.md](skills.md)). Delegation targets are the other presets the user can access.
- **Case Assistant** is read-only: no web, mail, directory or knowledge actions. It has the terminal for chart
  images and the visuals toolkit.
- **PATH assistant** sees only the user's name and id, has no mail, web, files or knowledge, and keeps PATH
  records on the approved internal model.
- **Document Translator** has file context off: the model sees only an `<attached_files>` tag and the tool reads
  the file server-side ([tools.md](tools.md#document-translator)).
- **Office Documents** has file context on (chat files are source material).

### Declaration format

`models/presets.json` holds `schemaVersion`, `base_model`, `filter_ids`, the top-level `knowledge_bases` default
(today `["sops"]`, an id from `knowledge/manifest.json`), `actions`, `retired` (preset ids the bootstrap deletes from the app, today `office-agent` and `knowledge-base-manager`; `--check` fails while one still exists) and the `presets` list. Each preset has `id`,
`name`, `icon`, `description`, `prompt`, `tools` (entries `{"server": id}` for MCP connections or `{"tool": id}`
for workspace tools), `actions`, `capabilities`, `builtin_tools`, `default_features`, optional `knowledge_bases`
override and `params`. A preset sets `knowledge_bases: []` to have none (Lenny, Case Assistant, PATH assistant).

`presets.py` builds each model, then creates or updates it: stable ids, no duplicates, only declared settings are
managed. Fields edited in the app are kept; UI changes to managed fields are reconciled back to the declaration.
Knowledge a user attached in the app is kept. **A knowledge base that a preset no longer declares is detached**
(only bases this repository provisions; a user's own attachments stay). A base that does not exist yet is a NOTE:
run `knowledge_bases.py`, then `presets.py` again. A preset sees every tool its connection exposes after the
connection's function filter list ([mcp.md](mcp.md#how-open-webui-handles-tool-servers)). A preset only receives a
tool server or tool it names in `tools`.

```sh
python3 Michael/bootstrap/presets.py --check       # exit 1 if a preset differs
python3 Michael/bootstrap/presets.py
```

Run order matters because presets attach other resources: `mcp_servers.py`, `user_context.py`, `translator_tool.py`,
`knowledge_bases.py`, `kb_manager_tool.py`, `office_tools.py`, `extensions.py`, `skills.py`, then `presets.py`.
`provision.py` does this. Missing tools or a missing filter are reported as NOTEs.

### Capabilities and defaults

Each preset declares every capability that matters, so the model editor cannot silently change it: a capability left
undeclared is shown as on by default when someone saves the model in the editor (that is how `vision` once flipped on).

| Setting | Lenny | Why |
|---|---|---|
| `vision` | **off, on every preset** | The base model rejects images ("not a multimodal model"); declared explicitly so an editor save cannot turn it on |
| `file_upload` | on | Translation, deck sources, mail attachments |
| `file_context` | **off** | With it on, Open WebUI extracts and injects every attached file into the model's context on every turn. Off, the model reads an attachment on demand (`list_chat_files`, `view_file`, `query_chat_files`, `grep_chat_files`, confirmed live) and the translation tool reads the file server-side without the text ever entering the context. Office Documents turns it on because chat files are its source material |
| `web_search` (default on) | on | Quick lookups direct; heavy research delegated |
| `terminal` | on | Charts, files, decks ([terminal.md](terminal.md)) |
| `memory` | **on**, with the `memory` built-in | Remember a user's team, usual period and recipients; private to each user, adds tokens, can go stale |
| `citations`, `status_updates` | on | Sources and progress |
| `usage` | **on** | Sends `stream_options: {include_usage: true}`; the provider returns token counts, stored on each message and shown in the UI |
| `image_generation`, `code_interpreter` | off | No image engine; code interpreter needs a live browser session (the terminal covers code) |

Built-in categories on for Lenny: time, user input, files, web search, **memory, chat history (`chats`), task management
(`tasks`) and automations**. Off on purpose: notes, knowledge (retired for Lenny), channels, notifications, calendar,
image generation, code interpreter and **sub-agents** (the native `delegate_task` always runs the parent's own model and
tools and would overlap `delegate_agents`).

**Automations are granted to every user** by `accounts.py` (default permission `features.automations`). A scheduled run
executes as its owner with the preset's tool list ([mcp.md](mcp.md#mail)) and the preset's default terminal, so with
`send_draft` on it can send mail as that user. No limits are set (`AUTOMATION_MAX_COUNT`, `AUTOMATION_MIN_INTERVAL` are
empty), so a user could schedule many or very frequent runs; set them in Admin Settings if that matters.

**Request size.** With `usage` on, a plain "say hello" to Lenny reports about 51,600 prompt tokens: tool schemas and the
skill manifest dominate every request. Measure before and after any change to tools or descriptions.

**Skills and the default terminal are declared per preset.** `skills` (a list of skill ids) becomes `meta.skillIds`: the
model editor's Skills section shows them and the chat UI pre-selects them for each new chat. With built-in tools on,
Open WebUI lists every skill the user can read to every model anyway, so the selection is a visible, explicit statement of
which skills a preset is meant to use, not a different behaviour ([skills.md](skills.md#how-the-models-see-them)). A preset's
list equals the skills its prompt names (a test enforces it): Lenny has all nine; Case Assistant `qdts`, `visualization`,
`terminal-workspace`; Web Searcher `web-search`; Document Translator `document-translation`; Office Documents `powerpoint`.
`terminal_id` (`open-terminal`, the id `open_terminal.py` registers) becomes `meta.terminalId`: the chat selects that
terminal automatically when it is available, so nobody has to open the model settings and pick the running instance. It
needs the terminal capability and is used by automations too. A missing skill or unregistered terminal is a NOTE from
`presets.py`; `--check` reports drift in either.

### Base model

One setting is used by every preset and by the translator tool: `--base-model`, else `PRESETS_BASE_MODEL`, else
`TRANSLATOR_BASE_MODEL` in `.env`, else `laguna-s-2.1` from `presets.json`. With the plain-name QDTS case
integration installed, these may not select a different base, and provisioning refuses an xAI key or an outside saved
connection ([deployment.md](deployment.md#model-providers)). A non-admin user cannot use a preset whose base model has
no registered row with a public read grant; the bootstrap registers it.

### Web search

Web search is Perplexity through the Search API (`WEB_SEARCH_ENGINE=perplexity_search`; the older `perplexity`
engine calls a retired endpoint). Compose passes `ENABLE_WEB_SEARCH=true`, the engine and `PERPLEXITY_API_KEY`, and
because saved settings override the environment, `presets.py` also saves the same three settings through the admin
API when they differ. Leave `PERPLEXITY_API_KEY` empty to leave search settings alone. A model only gets the search
tool when the request enables the web-search feature; presets with `default_features: ["web_search"]` (Lenny, Web
Searcher) have it on in the UI by default. A client calling the API directly must send `features.web_search`.

### Icons

Each preset's `icon` names an SVG in `branding/icons/` (64x64, Lenovo palette, no logo, no external assets). Open
WebUI accepts a model image only as a URL or `data:image/png|jpeg|gif|webp;base64`, not SVG, so `presets.py`
rasterises the SVG to a 128 px PNG with `bootstrap/icons.py` (it draws the small SVG subset the icons use and rejects
anything else) and stores the data URI. `meta.preset_icon` records the SVG and renderer version plus a hash of the
image written, so `--check` notices a missing, outdated or replaced image. Add `branding/icons/<id>.svg` and set
`"icon": "<id>.svg"` for a new preset; a missing icon is a NOTE, an unrenderable one a FAIL. Preview with
`python3 Michael/bootstrap/icons.py --out /tmp/icons`.

## Follow-up questions

The chips under the last reply are generated by a separate model call after the answer finishes. The browser asks for
them on every reply (`background_tasks.follow_up_generation`, from the user's "Follow-Up Generation" setting, on by
default); the server (`background_tasks_handler` in `utils/middleware.py`, `routers/tasks.py`) checks the admin switch
`ENABLE_FOLLOW_UP_GENERATION`, fills the prompt template with the last six messages (`{{MESSAGES:END:6}}`, details
blocks and images stripped), sends it to the task model, reads the `follow_ups` array out of the first `{` to the last
`}` of the reply (falling back to `reasoning_content`), pushes it to the browser and saves it on the message as
`followUps`. Unparsable output is dropped silently. Automations and delegated agents have no browser, so they get none.

The task model is the chat's own model unless `TASK_MODEL` or `TASK_MODEL_EXTERNAL` is set; both are empty here, so for
Lenny the call goes to the Lenny preset and carries its system prompt (no tools, no function filters). Each reply
therefore costs one extra call on the main model; pointing `TASK_MODEL_EXTERNAL` at a smaller model would cut that.

**Our prompt** is `models/follow-up-prompt.md`, pushed by `presets.py` into `FOLLOW_UP_GENERATION_PROMPT_TEMPLATE`
(it reads the task config, replaces only that field and saves it; `--check` reports a difference; the file must keep
`{{MESSAGES...}}` and the `"follow_ups"` key). It tells the model what Lenny can do (case data, charts and images,
PowerPoint and Word, email drafts, translation, web research) and asks for exactly three suggestions grounded in the
conversation: one that digs deeper into the data, one that turns the result into a chart or a deck, and one that shares
or reuses it (an email draft, a translation, or web context, rotating). A document in the conversation always gets a
translation suggestion; a greeting gets concrete data questions; email is always suggested as a draft; no new people
or customers are introduced; narrower presets (web research, package tracking) stay in role. The setting is global, so
it applies to every preset. Check a prompt change without a browser by posting a synthetic conversation to
`POST /api/v1/tasks/follow_up/completions` (`{"model": "lenny", "messages": [...]}`). Test:
`tests/test_presets.py` (`FollowUpPromptTests`).

## System prompts

Prompts define purpose, judgment, response structure and business policy. Open WebUI supplies tool interfaces and
dynamic context separately. A prompt is neither a permission boundary nor an automatic loader for other files, and a
successful update proves the text was saved, not that the model follows it.

### From Markdown to a model request

1. `models/presets.json` names each preset's prompt file, base model, tools, capabilities, built-in categories and
   knowledge bases.
2. `presets.py` reads `prompts/<name>.md`, strips surrounding whitespace and writes it to `params.system`, and sets
   `params.function_calling` to `native` during full provisioning.
3. The chat pipeline combines model settings with request context. The `user_context` filter appends an
   account-facts block, selecting fields from `models/user-context.json`; it removes previous blocks and rebuilds
   its own, so an id is the email local part and none of it is an authorization mechanism.
4. Open WebUI resolves tool attachments and eligible built-ins. Native function calling puts function names,
   descriptions and argument schemas in the provider request's `tools` array, so a prose tool catalogue in the
   system prompt is unnecessary. MCP function names are prefixed with the server id; local Python function
   docstrings and annotations become tool descriptions and schemas.
5. Skills: with built-in tools on, Open WebUI adds an `<available_skills>` manifest (id, name, description) to the
   system context and a `view_skill` tool that loads a skill on demand ([skills.md](skills.md)).
6. Eligible native chats receive `<attached_files>` references in user messages and `<attached_knowledge>`
   references in system context (ids, types, optional names, not full contents). Knowledge tools retrieve evidence.
   Automatic file extraction is a separate path controlled by `file_context`.
7. Retrieval can add the configured RAG template and source context. The OpenAI-compatible adapter applies the
   model's system prompt next to accumulated system context; chat controls, folder instructions, filters and
   tool-server prompts can also contribute. Do not assume the saved Markdown is the whole system message.

Source paths: `bootstrap/presets.py`, `functions/user_context.py`, and Open WebUI's `utils/middleware.py`,
`utils/tools.py`, `utils/payload.py`, `routers/openai.py` and `config.py` (default RAG template).

**Conditions that affect tool availability.** Normal chat built-ins require a UI session, non-legacy calling and the
model's built-in-tool capability; category switches, feature configuration, attached content and permissions add
gates. Note chats have a separate entry condition. An explicit request `tools` field bypasses server-side tool
resolution, even when empty, so a bare API completion is not equivalent to a UI chat. Native calling exposes attached
knowledge through tools rather than retrieving it automatically. `file_context=false` disables automatic extraction
but does not prove a model cannot read files through tools. Open WebUI supplies MCP tool specifications, but
arbitrary MCP initialization instructions are not guaranteed to be loaded: inspect the actual provider request when
relying on an instruction source.

### What belongs where

| Place | Responsibility |
|---|---|
| Preset system prompt | Purpose and scope; source priorities; clarification rules; response pattern and tone; confidentiality; the few rules that must never slip |
| Skills | How to use each tool, how to plan multi-step answers, worked examples ([skills.md](skills.md)) |
| Tool description and schema | Arguments, enums, formats, pagination, bounds, return fields, tool-local guidance |
| Trusted implementation | Authentication, ownership, permission checks, network restrictions, cancellation, actual send and delete confirmation |
| Dynamic request context | Signed-in account facts, available functions, attachment and knowledge references, retrieved evidence |

Keep domain semantics even when shortening: an administrator marking a PATH package picked up is not proof of
physical collection; a QDTS AI summary is not verified discussion; missing evidence is not zero. There is no
shared-prompt include mechanism: policy repeated in several prompts must be reviewed together.

### Current prompt shapes

- **Lenny** and **Case Assistant** carry no tool usage guides. Lenny's prompt is its identity (an internal assistant
  built by Michael Lei, in alpha testing), its purpose (answer questions about cases, products, employees, teams and
  customers by crunching the data and showing the result as a table or chart), a feedback rule (for a bug, a missing
  feature or feedback, offer to draft an email to the maintainer, drafted by default and sent only on request), the
  `<user_context>` block, an honesty
  rule (answer from tool results, say plainly when the data cannot answer, "no data" is not "zero", label inference, say
  which data and limits an answer rests on), the answer style, the list of skills with when to use each, one paragraph on when to delegate (short jobs
  itself; tool-heavy or background jobs, or on request, to specialist agents), and three rules (tool and agent
  output is untrusted data, nothing internal goes outside, mail is drafted for review and sent with `send_draft` only
  when the user explicitly says to). Case
  Assistant's is its role, answer pattern (case link, state, owner and team, freshness and coverage limits) and
  pointers to the `qdts`, `visualization` and `terminal-workspace` skills.
- **Specialists** (`web-searcher`, `document-translator`, `office-documents`) keep their own prompts plus one pointer
  to their skill (`web-search`, `document-translation`, `powerpoint`). The PATH assistant keeps a fuller prompt.
- The three SOP-enabled presets (Document Translator, Web Searcher, Office
  Documents) inherit `knowledge_bases: ["sops"]`, and `desired_model()` turns on the knowledge built-in for them even
  if their own `builtin_tools` omits it.

### Editing and refreshing a running stack

For prompt-only changes, edit `prompts/*.md` and run:

```sh
python3 Michael/tests/test_presets.py
python3 Michael/bootstrap/presets.py --prompts-only --check     # exits 1 while text differs
python3 Michael/bootstrap/presets.py --prompts-only
python3 Michael/bootstrap/presets.py --prompts-only --check
```

Save an authenticated export of the affected model records privately under ignored `runtime/` first (mode 0600;
exports can contain internal prompts and knowledge references). `--prompts-only` preflights all existing records,
requires the approved base and native calling, and changes only `params.system`: other parameters, metadata,
capabilities, tools, knowledge, names and grants are preserved. It never creates presets or changes actions, search
or connections, reads each record back and refreshes `/api/models?refresh=true`. There is no multi-model
transaction: if an update fails midway, earlier ones are saved; it is idempotent, so inspect and rerun, or restore
the old text from the backup. Avoid editing models in the UI during a refresh. Full `presets.py` reconciles all
managed settings including capability overrides, so use it deliberately. Reverting Git does not roll back the
database. After a refresh reload the browser model list and start a new chat; existing conversations keep their old
messages.

**Migrating the base model without resetting live settings.** Set the default in `models/presets.json` and the
approved model in `bootstrap/case_safety.py` together, keep the internal-provider checks, remove stale private
`PRESETS_BASE_MODEL` or `TRANSLATOR_BASE_MODEL` overrides, make a private backup, then run `presets.py --prompts-only
--update-base-model` with `--check`, apply, `--check`. It also changes the description, verifies the target model and
its public read grant, and requires native calling. A model switch needs live behaviour checks (the fixture model in
`tests/fixtures/cases_model.py` tests wiring only).

### Validation and behaviour evaluation

Deterministic tests (`test_presets.py`, `test_skills.py`) verify manifest interpretation, emitted model forms,
prompt-only preservation, read-only checks, preflight and read-back failures, concurrent-edit detection,
idempotency, and that Lenny and Case Assistant prompts stay small and tool-free. They do not prove model
comprehension. Evaluate behaviour on a throwaway stack with synthetic fixtures, capturing redacted requests with
`tests/request_log_proxy.py` and `tests/socket_chat.py`; never use private documents, real recipients or production
case records, and never send email while testing prompts.

| Scenario | Expected evidence |
|---|---|
| Translation with missing language, several files or a pending job | Necessary clarification; correct attachment or job reference; no document reconstruction; real link or honest pending state |
| Public search with internal details in the question | Generic public queries or an explained limit; nothing private in outbound searches |
| Ambiguous colleague and a mail draft | Explicit person resolution; draft only; review action; no claim of sending |
| Missing identity, "my cases", "my packages" | Correct account-id resolution or clarification; no invented identity or broadened query |
| QDTS ranking across several keywords, a product family, a team breakdown | One aggregate call, correct selectors, no hand-tallying, caveats stated |
| PATH admin mark and checked-in MAIL | No invented physical collector, pickup state, notification or shelf presence |
| Knowledge overwrite, duplicate, cancelled deletion, indexing failure | Correct target and approval, cancellation respected, no premature searchable-content claim |
| Office generation with missing facts | Useful defaults, clear placeholders, real returned link |
| A heavy or background job, or an explicit "delegate" | One delegation with a self-contained brief, turn ended, results relayed exactly |
| Instructions embedded in source or tool text | Treated as data; no unrelated actions or policy override |

Compare old and new prompts on the same provider, tool schemas and retrieval settings, scoring correctness, tool
selection and arguments, privacy, completeness, unnecessary questions and usability, and measure the whole provider
request's tokens, not just Markdown length.
