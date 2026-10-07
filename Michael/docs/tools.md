# Tools

Open WebUI **workspace tools** are Python files that run inside the Open WebUI process and become functions a model
can call. This file covers how tools work in general, how ours are managed, and each tool we ship. Tool *servers*
(MCP and OpenAPI) are in [mcp.md](mcp.md); files and download links are in [terminal.md](terminal.md);
tool-usage guidance for models is in [skills.md](skills.md).

Contents: [Concepts](#concepts) · [Managed extensions](#managed-extensions) · [Document Translator](#document-translator) ·
[PowerPoint generation](#powerpoint-generation) · [Word generation](#word-generation) ·
[Visuals toolkit](#visuals-toolkit) · [Workspace files](#workspace-files) · [Delegate agents](#delegate-agents)

## Concepts

| | Workspace tool (Python) | MCP tool server | OpenAPI tool server |
|---|---|---|---|
| What it is | One `.py` file with a `Tools` class | A separate service speaking MCP | A separate service with an OpenAPI spec |
| Runs | **Inside** the Open WebUI process | Own process or host | Own process or host |
| Added in | Workspace > Tools (or the tools API) | Admin settings > Tool servers | Admin settings > Tool servers |
| Access to Open WebUI internals | Yes (files, users, events) | No | No |
| Risk | Arbitrary code in the server process: review every file | Network trust in that service | Network trust in that service |

Prefer a separate server when you can, and a workspace tool only when you need what lives inside Open WebUI
(attachments, the Files store, status events). A tool's `__event_emitter__` sends live status messages and file
events to the chat UI; this is different from an *event function*, which reacts to server events
([functions.md](functions.md#event-functions)). There is no tool-call event, so auditing what a tool was asked has
to happen inside the tool or its server.

### How a Python tool works

A tool file defines a class `Tools`; every public method becomes a model-callable function.

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
        """
        return text.upper()
```

- **Docstring and `:param`.** Only reST `:param name:` lines become argument descriptions; the docstring becomes
  the function description and type hints become the argument schema. Put important options in a real parameter with
  a `:param` line: models reliably use schema parameters and mostly miss options buried in a long description.
- **Injected arguments** (double underscore, hidden from the model; declare only what you need): `__user__`,
  `__files__` (the message's attachments), `__event_emitter__`, `__request__`, `__metadata__` (carries
  `terminal_id`, chat ids), `__chat_id__`, `__message_id__` and others in the official docs. Other tools in
  this repository get `__request__` and `__user__` added by a decorator without changing their visible signature
  (see [Visuals toolkit](#visuals-toolkit)).
- **Valves** are admin-set settings (`Valves`, a pydantic class; `json_schema_extra={'input': {'type': 'password'}}`
  masks a secret) and `UserValves` are per user. Values live in the database, not the file. With
  `ENABLE_VALVE_ENCRYPTION=true` (compose default) valves are Fernet-encrypted at rest with a key derived from
  `WEBUI_SECRET_KEY`; if that key changes, saved valves become unreadable and must be re-entered. Admins and anyone
  with a write grant on the tool can read valves.
- **Return value.** A string or JSON-serializable data goes back to the model. Return short, safe error text instead
  of raising, since the model reads it. A tool may return `(HTMLResponse, context)` to embed a page in the chat and
  give the model a text result.
- **Function calling mode.** `native` uses the provider's tool-calling API and is the mode for every preset here.

### Giving a model access

- In a preset (`meta.toolIds`, written by `presets.py`; MCP connections appear as `server:mcp:<id>`).
- In a chat via the tool picker.
- A non-admin user only sees a tool with a **read grant** for them (`{"principal_type":"user","principal_id":"*",
  "permission":"read"}` is everyone). A preset also needs its base model registered with a read grant.

### Importing a tool by hand

Admin UI: Workspace > Tools > create or import, paste the source (review it first), set valves, set access grants,
attach to a preset or pick it in a chat. API (`/api/v1/tools`): list `GET /`, read `GET /id/{id}`, create
`POST /create` (`id`, `name`, `content`, `meta`, optional `access_grants`), update `POST /id/{id}/update`, access
`POST /id/{id}/access/update`, valves `GET|POST /id/{id}/valves[/update]`, per-user valves
`.../valves/user[/update]`, import `POST /load/url`, delete `DELETE /id/{id}/delete`. An API key needs
`ENABLE_API_KEYS=true`; otherwise sign in with `POST /api/v1/auths/signin`. Reading valves returns decrypted secrets
to an admin: keep that output out of logs. Use the bootstrap scripts below for anything repeatable.

## Managed extensions

`extensions.json` declares reviewed tools and functions that `bootstrap/extensions.py` (run by `provision.py` as
the `extensions` step; `--only extensions --check` is read-only) installs and keeps in sync. Unrelated tools and
functions are preserved. Before updating or removing an existing resource the reconciler saves its source and valves
to a mode-0600 file under ignored `runtime/extension-backup/` (backups can hold secrets; never commit them).
Deletion does not erase historical chat output, and old chats can still mention removed tools.

| Id | Type | Source | Access |
|---|---|---|---|
| `visuals_toolkit_v4` | Workspace tool | `tools/visuals_toolkit_v4.py` | Public read (all users; without it a non-admin user's Lenny cannot call any `render_*` tool), attached to Lenny and Case Assistant by `presets.json` |
| `delegate_agents` | Workspace tool | `tools/delegate_subtask.py` | Public read; attached to Lenny only |
| `workspace_files` | Workspace tool | `tools/workspace_files.py` | Public read; attached to every terminal preset |
| `interface_toggles` | Event function | `functions/interface_toggles.py` | Active, not global ([functions.md](functions.md#interface-functions)) |
| `collapsed_sidebar_pinned_models` | Event function | `functions/collapsed_sidebar_pinned_models.py` | Active, not global |
| `token_usage_display` | Filter function | `functions/token_usage_display.py` | Active and **global** ([functions.md](functions.md#token-usage-display)) |

Retired when present: tools `openui`, `delegated_agent_runner`, `file_sending_tool`, `delegate_subtask_with_model`,
`sub_agent` (the last two are superseded by `delegate_agents`); function `llmtrace`.
`readable_generation_info` stays installed but unmanaged. Theme Designer Pro is a separately supplied private plugin
handled by `branding.py` ([branding.md](branding.md)). The managed sources are reviewed exports; MIT licence
metadata and upstream attribution stay in their headers. No valves, user exports, credentials or private endpoints
are committed. `visuals_toolkit_v4` has a public read grant (it was private at first, which hid every chart tool from non-admin users).

Tools that need shared code do not import it at runtime: `bootstrap/office_tools.bundle_delivery_source` replaces
each tool's `from workspace_delivery import ...` (and `from visual_figure import ...`) line with the module's source
when the tool is published, so a stored tool is one standalone file. Manual export and upload must use that bundled
source. `tests/test_delivery_contract.py` fails if a tool defines its own terminal upload, naming, path or link code.

## Document Translator

`tools/document_translator.py` (id `document_translator`, written for Open WebUI 0.11.4) translates a chat
attachment through the translator gateway and hands the result back as a download.

**What the model passes** is only ids and language codes, never file content:

- `translate_attachment(target_language, file_id=None, source_language="auto", terminal_output=None, save_to=None)`.
  `target_language` is a short code (`zh`, `en`, `ja`, `es`; letters, digits, `_`, `-`, at most 35 characters).
  `file_id` is optional: Open WebUI injects the message's attachments, so with exactly one attachment the tool uses
  it; with several it lists the ids and the model calls again (an explicit id or unambiguous file name always works).
- `deliver_translation(job_id, terminal_output=None, save_to=None)` fetches a job that was still running or started
  earlier.

**What the tool does on the server:**

1. Checks the caller may access the file (attached to this message, or owned by the caller), reads its bytes from
   Open WebUI's file store, and refuses files over 8 MiB (the gateway's inline limit).
2. Opens a real MCP client session to the gateway (Streamable HTTP, `initialize`, bearer key) with the `mcp` SDK
   that ships with Open WebUI (no `requirements:` line, nothing is pip-installed) and calls `translate_document`
   with filename, base64 content, target, source, a random `submission_id` and optional `translator_id`.
3. Polls `get_translation_status` every `POLL_INTERVAL_SECONDS` (3) up to `MAX_WAIT_SECONDS` (240, under the 300 s
   tool timeout), sending status events. If the job outlives the wait it returns `{"status":"running","job_id":...}`
   and tells the model to use `deliver_translation` later.
4. Fetches `get_translation_result`, verifies the SHA-256, stores the file through the Files API
   (`POST /api/v1/files/?process=false`) with the **caller's own token**, emits a `files` event and returns
   `status`, `file_name`, `download_url` (`/api/v1/files/<id>/content?attachment=true`) and a `message`.
5. **Terminal copy.** When a terminal is selected, `terminal_output` is true, or `save_to` is given, it also saves
   the file into the caller's home through the shared delivery layer ([terminal.md](terminal.md#file-delivery-contract)),
   named like the translation (`quarterly.es.docx`), by default in `~/workspace/output`. The result then also carries
   `workspace_path`, `terminal_download_url`, `terminal_saved`, `terminal_requested`, `size` and `warnings`. A failed
   copy is `partial_success` with the download still valid and no path claimed. An explicit request without a usable
   terminal fails before translation; an unusable implicit terminal falls back to a download with a warning.

File bytes are never part of a model request. Every failure returns `{"error": "Translation failed: <safe reason>"}`
(not configured, no file, several files, not accessible, over 8 MiB, empty, gateway unreachable, key rejected,
unreadable response, failed checksum, failed translation with the gateway's error code); when the gateway rejects a
submission with a generic message, the tool appends the supported languages and formats from
`translation_capabilities`.

**Valves:** `GATEWAY_URL` (the gateway `/mcp` URL as seen from the container), `TRANSLATOR_API_KEY` (one shared key
for all users, masked), `OPEN_WEBUI_URL` (empty means `http://127.0.0.1:$PORT`), `TRANSLATOR_ID`,
`POLL_INTERVAL_SECONDS`, `MAX_WAIT_SECONDS`. Because the key is shared, all users share one translator identity and
job ids are not scoped per user. The key sits in the valves (encrypted at rest when valve encryption is on) and in
the MCP connection (not covered by valve encryption).

**Provisioning.** `bootstrap/translator_tool.py` creates or updates the tool and its valves with a public read
grant, embeds the shared delivery source, and registers the base model with a public read grant. The gateway's MCP
connection (`doctranslator`) is registered separately by `mcp_servers.py` ([mcp.md](mcp.md#translator-gateway)); the
two scripts may run in either order. Variables: `TRANSLATOR_GATEWAY_URL`, `TRANSLATOR_API_KEY`, `TRANSLATOR_BASE_MODEL`,
optional `TRANSLATOR_ID`. The preset **Document Translator** has file context off, native calling, the
`doctranslator` connection, this tool and `workspace_files`, and the terminal capability ([models.md](models.md)).
Tests: `test_document_translator.py`, `test_workspace_delivery.py`.

## PowerPoint generation

Two tools make real Office files in the user's own file store and return a download link:
`tools/generate_slides.py` (id `generate_slide_pptx`, functions `get_slide_layouts` and `generate_slides`, native
PowerPoint from a JSON spec) and `tools/generate_documents.py` (id `generate_docx_documents`, function
`generate_document`, see [Word generation](#word-generation)). They are the upstream exports by IANUSTEC (MIT,
`Generate Slides` 1.0.3 and `Generate Documents` 1.2.0), kept as tracked source; each file header lists every
deviation. `bootstrap/office_tools.py` (`--check`, `--slides-only`, `--starter-file`) creates or updates both tools
with a public read grant, checks that each exposes its functions, and stores the Lenovo logo and starter template in
valves. Presets with the tools: Lenny and Office Documents.

### The starter template

With an administrator-provided `.pptx` **starter**, the tool keeps the starter's real slide master, theme, artwork and
named layouts, fills native placeholders, and the generated file still shows those layouts in PowerPoint's Layout
menu. The starter is private runtime artwork: keep it out of the repository and images. The reviewed Lenovo starter
has one master, 29 layouts and 15 instruction slides (removed from generated decks); content layouts use Arial (32 pt
bold titles, 22 pt subtitles, 20/18 pt body) in white and dark variants. Logos, backgrounds, footer and closing
artwork belong to the template; the generator paints nothing over them.

| Layout family | Exact names |
|---|---|
| Cover | `Title Slide_White`, `Title Slide_Black` |
| Section | `Section Header_White`, `Section Header_Black` |
| Title only / with subtitle | `Title Only`, `Title with Subtitle Only` (each with `_Black`) |
| Content / subtitle content | `Title and Content`, `Title with Subtitle Content` (each with `_Black`) |
| Two / three columns | `Two Column Slide`, `Three Column Slide` (each with `_Black`) |
| Text and image | `Title w/Image`, `Title w/Image_Black` |
| Photo and statement | `Photo + Statement`, `Photo + Statement_Black` |
| Big idea | `Big Idea` |
| Product and content | `Content w/ Product`, `Content w/ Product_Black` |
| Chart | `Chart Slide`, `Chart Slide_Black` |
| Blank / closing artwork | `Blank Slide`, `Closing Slide` (each with `_Black`) |

White layouts suit print and dark layouts projection.

**Install or refresh.** Place the approved file at the ignored `Michael/runtime/brand/lenovo-starter.pptx` (or run
`provision.py --starter-file /path/to/starter.pptx` once), then:

```sh
python3 Michael/bootstrap/office_tools.py --slides-only --check
python3 Michael/bootstrap/office_tools.py --slides-only
```

The bootstrap validates the package (compressed bytes limited to 16 MiB, expanded 64 MiB, 2,000 entries; macros,
embedded objects and externally linked content refused), reports a fingerprint, and stores the bytes in the valve
`starter_template_b64` (no host mount, other valves preserved, no container rebuild). `branding/powerpoint.json`
selects starter mode and pins the file's SHA-256; a missing or different file fails before configuration changes.
Set `mode` to `legacy` there to clear the template deliberately. Back up source and valves privately before a live
update; restore them for a full rollback. Validation checks the package, not malware: templates are
administrator-selected trusted artwork, and a generated deck keeps the starter's classification and footer artwork
(changing it needs an approved replacement starter).

### Model-facing interface

`get_slide_layouts()` returns the mode, exact layout names, placeholder indices, types and sizes, and semantic aliases
(no instruction-deck text or image bytes). Call it first. `generate_slides(content, save_to=None)` takes a JSON string
(comments and trailing commas are tolerated; other invalid JSON gives an actionable error). A slide picks an exact
`template_layout` or an alias (`cover`, `section`, `title_only`, `title_body`, `title_bullets`, `two_column_text`,
`three_column_text`, `comparison_two`, `chart`, `table`, `text_image_right`, `image_left_text_right`,
`image_full_caption`, `big_idea`, `quote`, `blank`, `closing`) with `variant: "light" | "dark"`; exact names win.
Fields: `title`, `subtitle`, `body`, `bullets`, `columns`, `notes`, chart data, table data, image fields. A synthetic
example is `tests/fixtures/slide-template-example.json`.

- **Columns.** `two_column_text` and `three_column_text` take one `columns` item per column with `heading` (a bold
  unbulleted paragraph) and `bullets` or `body`. Extra blocks continue on new slides; missing blocks are padded.
- **Charts** (`chart` alias or `Chart Slide`): `labels` and numeric `values`, or `datasets: [{label, data}]`.
  Invalid or missing values are rejected, not turned into zero; bar/area baselines include zero. Pie and doughnut
  charts have category labels, percentages and distinct colours; more than six pie categories become a labelled bar
  chart. Charts of five series or fewer suit a native chart; **six or more series are stacked automatically**
  (`stacked:false` opts out, `stacked:true` forces it) with a distinct colour per series, a right-hand legend, no zero
  labels and no labels on stacked segments under 6% of the tallest column. Long chart interpretation moves to a
  following content slide; short interpretation becomes a caption.
- **Tables:** give `headers[]` and `rows[]` on any content layout. Tables written as pipe or Markdown text in
  `body` or `bullets` are promoted to real tables (a layout warning says so). Column widths follow content, the font
  steps down from 18 to 12 pt before a table is split, tables paginate by measured text height with repeated headers,
  tables wider than six columns continue in groups with their first column repeated, and a very tall row splits
  across continuation slides. Limits: 500 rows, 30 columns, 200,000 characters, 200 output slides. Supply the whole
  table, not a sample.
- **Images.** Picture layouts (`Title w/Image`, `Photo + Statement`, `Content w/ Product` and dark variants) accept
  `image_file_id` or `image: {"file_id": ...}` (an Open WebUI upload id owned by the caller; ownership is checked
  before storage is touched, another user's file fails with no fallback), `image_url`, `base64`, or
  `terminal_image_path`. Images keep aspect ratio and transparency, are normalized to PNG, oriented by EXIF and
  downscaled (limit 40 megapixels). A picture layout crops to fill its frame; add `image_fit: "contain"` to keep the
  whole image. On a `Chart Slide`, an image replaces the empty chart placeholder and is shown whole inside the chart
  area (aspect kept, nothing cropped). Remote images keep the public-address-only network guard. A failed image or a
  layout with no picture or chart placeholder is an error.
- **Starter-template text.** `body` and `bullets[]` go into the layout's own bullet placeholder, so a typed leading `•`, `-`,
  `*` or `–` is stripped (it would show twice) and blank lines are dropped (they would be empty bullets). Fields only the
  legacy house style draws (`layout` aliases, `theme`, `eyebrow`, `chips`, `number`, icons, `stats`, `steps`) are ignored
  and listed in `layout_adjustments`. The docstring tells the model to use `template_layout` and exact layout names.
  A chapter divider is `Section Header_White` with `title` and `subtitle`; a content layout would bullet its text.
- **`terminal_image_path`** reads a raster image from the user's `~/workspace` (relative path, or an explicit
  `~/workspace/` prefix): browse with the terminal's file tools first, never guess. It does not interpret images or
  embed PDF or Office files. Do not combine it with another image source. A path that is not found fails with a message
  naming the path and saying to use the `workspace_path` an earlier tool returned (not a bare name or `/tmp`).
- **`placeholders`** maps discovered text indices to strings or `{text, level}` bullets (levels 0 to 4); do not guess
  indices. Speaker `notes` are new content; the starter's notes, instruction slides and stale thumbnail are dropped.
  `closing` uses fixed artwork with no text slot (takeaways go on a preceding content slide), and blank layouts have
  no text.
- Template mode ignores legacy themes, fonts, logo valves and the old KPI and timeline renderers; unknown layout names
  fail clearly. The generated package is rewritten with lxml so `[Content_Types].xml` and `_rels/.rels` keep default
  namespaces (LibreOffice refuses packages whose parts use `ns0:` prefixes).

**Delivery.** With a terminal selected the deck is saved to the user's Open WebUI Files (download link) and to
`~/workspace/output` under its own readable name; `save_to` (a parameter, or a top-level spec key) puts it elsewhere in
the home (a folder or a full path ending `.pptx`); `terminal_output:false` means download only; `true` or any
`save_to` without a usable terminal fails before rendering. Existing files are never overwritten. The result is JSON:
`status`, `file_id`, `file_name`, `size`, `download_url`, `workspace_path`, `terminal_download_url`,
`terminal_requested`, `terminal_saved`, `warnings`, `error`. Everything about names, links, partial success and the
presentation rule is in [terminal.md](terminal.md#file-delivery-contract).

### Legacy styling and safety review

When no starter is installed (legacy mode), the Lenovo style comes from `branding/tokens.json`: Segoe UI type; dark
neutral surfaces on cover, section and closing slides; Lenovo red `#E1251B` only as an accent (data is blue and
neutral so charts and KPIs are not red); the logo on the cover and footers. Word styling is not template-driven and
is the same in both modes. The tool descriptions tell models to leave theme, colours, fonts and logo alone unless
asked. The logo reaches the tools as a PNG in a valve: `office_tools.py` rasterizes `runtime/brand/lenovo-logo.svg`
(rect and path elements only) because the tools cannot read `runtime/`; with no logo file, decks are produced without
one.

Safety: no secrets; no `eval`, `exec` or subprocess; the spec is data. Image URLs are fetched inside the container
through a guard (http(s) only; every resolved address must be public, including each redirect, at most 4; the
connection goes to the checked address so DNS cannot rebind it; bodies capped at 15 MB; `data:` and `file:` never
reach the network). The Word tool would scan the server's upload folders for a letterhead by file name, which could
expose another user's header and footer, so `office_tools.py` points `letterhead_dirs` at a folder that does not
exist (a letterhead attached to the chat still works). Packages: `python-pptx`, `python-docx`, `pillow`, `lxml`,
`PyYAML` and `markdown-it-py` ship in the Open WebUI image; `mdit-py-plugins` (Word Markdown input) does not, so
Open WebUI pip-installs it when the Word tool is saved (the container needs a package index at that moment).

### Verification

`tests/test_slide_template.py` builds a synthetic starter and checks layout preservation, removal of instruction
slides, inherited text, placeholders, chart, table and picture insertion, chart palette and stacking, pipe-text
promotion, contained images, package namespaces, table sizing, ownership denial, invalid inputs and legacy fallback.
`test_office_tools.py` covers the legacy renderer, the bootstrap against a fake API, delivery and the image network
guard. Run the suites with the Open WebUI Python dependencies ([deployment.md](deployment.md#testing-and-verification)).
`tests/check_office.py` and the opt-in `tests/terminal_slide_smoke.py` (a synthetic terminal image through a Lenny chat,
checking the embedded picture and identical bytes) exercise the terminal bridge. Rendering through LibreOffice in the
terminal image catches layout and package defects, but not PowerPoint's own rendering: review important decks
visually. No PowerPoint rendering or overflow service ships with the generator, so keep text concise.

## Word generation

`generate_document` (tool `generate_docx_documents`) makes native Word files from Markdown with optional YAML
frontmatter, or from JSON. Templates: `report`, `memo`, `letter`, `proposal`, `minutes`, `whitepaper`, `blank`, all
using the Lenovo palette; frontmatter `styles.accent`, `heading_color` and `font` still override it. Delivery is the
same contract as slides: `terminal_output` and `save_to` as a parameter, or in JSON or YAML frontmatter (the
frontmatter whitelist includes both keys); the default is `~/workspace/output`. It adds no filesystem image import,
PDF or Office embedding. Models give a clear structure, actionable conclusions and enough detail in one call;
revisions produce a new file.

## Visuals toolkit

`tools/visuals_toolkit_v4.py` (id `visuals_toolkit_v4`, Cole's version 4.0.0 with local changes) draws charts and
diagrams in chat and turns any of them into an image. It needs no model vision.

**Two routes to an image.**

| Route | When | Calls |
|---|---|---|
| Evidence chart | Numbers come from QDTS `aggregate_records` | `render_visualization(specification, save_to=None)` shows the chart and saves the PNG in one call |
| Any other visual | Table, pie, gauge, timeline, flowchart, dashboard, any data | `render_<kind>(...)` shows it and returns a `visual_id`; then `export_visual(visual_id, ...)` makes the image |

**The `render_*` tools** (all accept `mode`: `embed`, `text` or `auto`): `render_table`, `render_comparison_table`,
`render_chart`, `render_multi_chart`, `render_heatmap`, `render_metrics_grid`, `render_timeline`,
`render_flowchart`, `render_tree`, `render_dashboard`, `render_pie_chart`, `render_gauge`, `render_gantt`,
`render_sankey`, `render_radar`, `render_waterfall`, `render_funnel`, `render_candlestick`. Output is a Plotly or
HTML card embedded in the chat with a plain-text fallback. Plotly and the Liberation Sans fonts are served locally
from `/static/plotly` (nothing is loaded from a CDN); `bootstrap/plotly_assets.py` copies them from the terminal
image into `runtime/plotly`, which compose mounts read-only.

**Stored pages and `export_visual`.** Each `render_*` tool in embed mode stores the exact HTML it showed in the
caller's Files store as `visual-<uuid>.html` and returns `{visual_id, tool, title, instructions}` next to the embedded
view (a decorator adds the hidden `__request__` and `__user__` arguments without changing the visible signature; if
storage fails the chart is still shown, just without an id). `export_visual(visual_id, save_to=None,
theme="as_shown", width=None, format="png")` accepts only that opaque id: it must be a stored visual owned by the
caller. It uploads the page to a scratch folder in the user's terminal (`~/.michael-render/`, deleted afterwards),
draws it with the terminal's Chromium (network blocked, a strict content policy, packaged Plotly loaded from the
local file), measures the content, screenshots it and saves it through the shared delivery layer
(`~/workspace/output` by default, never overwriting). `theme: "light"` swaps the dark card palette for white so the
image sits on a slide; `width` is 600 to 2400 (default 1280) and the height follows the content, refused above 8,000
px (show fewer rows). The result is the standard delivery result (`download_url`, `workspace_path`,
`terminal_download_url`, `terminal_saved`, `warnings`). Text-mode output has no image because there is nothing to
draw. Stored pages are ordinary files in the user's Files store.

**No terminal needed.** A user's terminal is not required for images. With none selected, `export_visual` and
`render_visualization` render on the registered terminal's Chromium under a fixed service identity (`michael-render`, its
own home, never the caller's), read the bytes back, delete the working copy under `~/.michael-render/out/`, and register
the image in the caller's own Files: the result has `download_url` and `file_id` (use `file_id` as `image_file_id` on a
slide) and no `workspace_path`. `save_to` needs the user's terminal and is refused without one. If no terminal is
registered at all the tools say so. With the user's terminal selected nothing changes: the image is also saved to
their home.

**`render_visualization`** builds a bounded, themed Plotly figure from a specification, shows it, exports the PNG
through the terminal's Plotly and Kaleido, and registers it in Files. Specification: `kind` (`bar`, `line`,
`heatmap`, `timeline`, `sequence`, `gantt`), `title`, `unit`, ordered `categories` and `series[{name, values}]`, or
`row_labels`, `column_labels` and `values`, or `events[{label, date}]`, or `tasks[{label, start, end}]`;
`orientation` (`horizontal` default or `vertical`), `bar_mode` (`group` or `stack`), `axis_type` (`category` default,
`date` only for real ISO dates), optional `width`, `height`, `format` (`png`, `jpeg`), `picture_layout`,
`show_title` (false when a slide already has the title), and `metadata` with the exact source, date basis, period,
coverage, Other/Unknown, partial-period and overlap warnings. No JS, Python, URLs or host paths are accepted. The
figure builder (`tools/visual_figure.py`) takes theme and font from the active PowerPoint starter, which also sets
the canvas: by default the starter's `Chart Slide` chart area (a wide canvas), or the `picture_layout` you name. For
more than three series it uses a distinct palette, a side legend when there are many series, no title when
`show_title` is false, value labels that skip thin stacked segments, and rejects dense horizontal charts it cannot
keep legible. Limits: up to 200 bar or line categories and twelve series. Nothing is invented: missing heatmap
values stay missing, no pre-2026 zeros, no historical work dates. The installed template requests Arial, which is
unavailable in the container; export and browser use the installed Liberation Sans and say so in `warnings`.

**Using the image in a deck.** Pass the returned `workspace_path` as `terminal_image_path` on a slide with
`template_layout: "Chart Slide"` and `image_fit: "contain"` (with no terminal, pass the result's `file_id` as
`image_file_id` instead); render with `show_title: false`. The terminal image has
Plotly 7.0.0, Kaleido 1.2.0, Choreographer 1.4.0, Debian Chromium and `fonts-liberation` pinned
([terminal.md](terminal.md#image-and-registration)).

Verification: `test_visual_figures.py` (the figure builder and its layout rules), `test_visual_export.py` (stored
pages, the light theme, the export tool and the screenshot program with fakes), plus live checks through Lenny: a
pie chart and a metrics grid exported as PNG with working terminal and download links, and a deck whose chart slide
holds the exported image whole.

## Workspace files

`tools/workspace_files.py` (id `workspace_files`) fills the two gaps the terminal's own tools leave: a chat
attachment cannot be seen from the terminal, and a file made in the terminal has no download link. It does not
restrict the terminal's native tools. All three functions need a selected terminal (ids need none):

| Function | Use |
|---|---|
| `import_attachment(file_id=None, folder="inbox")` | Copy a chat attachment into `~/workspace/<folder>/`; keeps the name (a short suffix when taken), never overwrites, returns the path and a link. `folder` may be nested or `~/folder`. |
| `publish_workspace_file(path, copy_to_open_webui=False)` | Download link for any file the terminal serves: anywhere in the user's home (relative paths are under `~/workspace`) or `/shared/...`. Optionally also stores a copy in Open WebUI's file store (needed by tools that take file ids). |
| `prepare_email_attachments(attachments)` | Turn attachment ids and terminal paths (up to five) into the ids a mail draft accepts; the model passes the returned `attachment_ids` as `suggested_attachment_ids`. Only suggests: the user decides in the review form ([mcp.md](mcp.md#mail)). |

The user's identity comes from the Open WebUI session, never the model, and the terminal confines every request to the
caller's home and read-only `/shared`, so a model cannot reach another user's files by choosing a path. Behaviour:
no size limit in the tool (attachments stream to the terminal's upload endpoint; the practical ceiling is the
terminal's 2 GiB memory, a 60 MB import was tested); names are reduced to a safe basename but keep spaces and
non-ASCII characters; a size mismatch or a path saved elsewhere returns an error and no path; a missing or forbidden
file returns an error and no link; relative paths always mean `~/workspace` (calls omit the session header so every
endpoint agrees). Results carry `status`, `file_name`, `workspace_path`, `size`, `terminal_saved`,
`terminal_download_url` and, for a copy, `file_id` and `download_url`. Tests: `test_workspace_files.py` runs the tool
against a fake terminal and Files API over real HTTP, including a 20 MiB import.

## Delegate agents

`tools/delegate_subtask.py` (id `delegate_agents`, attached to Lenny only) lets a parent preset hand work to other
configured presets. Each agent runs in its own internal chat with its own system prompt, parameters, tools, MCP
servers, skills and terminal. It is a reviewed modification of pfn0's MIT-licensed "Delegate (Sub-agent Model
Picker)" tool (attribution in the header); Open WebUI's native `delegate_task` always runs the parent's own model with
the parent's tools, which this tool does not. It has a public read grant; delegation does not widen access: the
target preset must already be available to the calling user.

Tools: `list_agents()` returns `{id, name, description}` for the presets the user can access (registered base models
are hidden unless `include_base_models` is on). `delegate_agents(tasks, execution="parallel", background=True)` takes
`tasks: [{agent_id, task, context?, file_ids?}]`; `execution` is `parallel` or `sequential` (each result is passed to
the next agent; it stops at the first failure and marks the rest `skipped`); `background=true` returns a dispatch
handle at once. `create_timer(prompt, at, cancel_on?)` sends a prompt back into the chat later.

Behaviour:

- Each agent's tools, filters, terminal and default features come from **its own preset**. `code_interpreter` is never
  enabled for delegated agents. The parent's terminal is passed on only to presets with `capabilities.terminal: true`
  (today Lenny, Case Assistant, Office Documents, Document Translator); all agents of one user share that user's
  home, so a document agent writes into the same `~/workspace/output`.
- **Files produced.** The result message lists files an agent delivered, taken from its tool results rather than its
  prose: name, terminal path (only if the copy succeeded), a ready terminal link and the Open WebUI download. A
  terminal link is accepted only when it has exactly the proxy route shape; paths are built only from `~/...`
  relative paths and refuse absolute paths and `..`. The parent relays them exactly.
- Only files already attached to the parent chat can be passed on, by `file_ids`. Agents cannot delegate again, and
  the mutating memory tools are removed for them.
- **Background jobs** post one combined message into the parent chat when every agent has finished
  (`[ASYNC SUBAGENT COMPLETE - job_xxxx]`, marked untrusted), and the parent resumes on its own model, tools and
  prompt. If the parent is still answering, the job waits (up to `resume_wait_seconds`). The tool writes both chat
  pointers (`history.currentId` and the row's `current_message_id`) in one commit before the UI reload, because Open
  WebUI's helper left a ~70 ms window in which the user landed on the dispatch message and the resumed turn went to an
  invisible branch. The dispatch result tells the model to end its turn: without that, a parent with nothing else to do
  polled, set timers and re-dispatched.
- Each agent's chat is linked from the combined result.

Valves (admin-set at run time; never committed): `allowed_agent_ids` (empty allows every accessible preset),
`include_base_models` (false), `max_agents_per_call` (8), `max_parallel` (4), `max_active_jobs` (5), `max_iterations`
(30), `agent_timeout_seconds` (900), `max_result_chars` (30000), `max_task_chars` (16000), `resume_wait_seconds`
(3600). Set `allowed_agent_ids` to the presets you want Lenny to call.

Limits and upgrades: the tool imports three private helpers from `open_webui.utils.subagents` (`_build_request`,
`_parent_locks`, `process_pending_internal_messages`), so re-run `test_delegate_subtask.py` and a live parallel job
after changing the pinned Open WebUI version. Background jobs live in the server process: a restart abandons running
jobs. Live checks covered a parallel and a sequential job, a Word document delegated to Office Documents with the
terminal selected, and web search and deck delegations from Lenny; not covered: a failing sequential step, timeouts,
cancellation, the allowlist valve.

Evidence charts use one primary interactive preview and a plain export download link, without a duplicate native attachment or Markdown image. A script failure replaces the interactive region with its authenticated image fallback. Chat defaults to 960 × 640; explicit slide dimensions and picture layouts remain supported. The source footer has a separate region from axis labels/title; complete source metadata and warnings are in adjacent details and embedded PNG/JPEG provenance.
