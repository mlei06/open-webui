# PowerPoint starter and layout library

The PowerPoint tool can generate presentations from an administrator-provided `.pptx` starter. It retains the starter's actual slide master, themes, artwork and named layouts, then fills native placeholders. The generated file still exposes those layouts through PowerPoint's slide-thumbnail **Layout** menu.

The starter is private runtime artwork, not source code. Keep it out of this public repository and container images. The implementation, bootstrap, synthetic tests and a synthetic [example spec](../tests/fixtures/slide-template-example.json) are tracked under `Michael/`.

## Reviewed Lenovo starter

The supplied starter has one master, 29 layouts and 15 instructional/sample slides. Its content layouts use Arial (32 pt bold titles, 22 pt subtitles and 20/18 pt body levels), with white and dark variants. Cover and Big Idea slides have larger display typography. Logos, backgrounds, footer/classification artwork and closing artwork are part of the template; the generator does not paint an extra logo or its old footer over them.

The 15 starter slides explain template use; they are removed from generated decks. The layout library is retained:

| Layout family | Exact names |
|---|---|
| Cover | `Title Slide_White`, `Title Slide_Black` |
| Section | `Section Header_White`, `Section Header_Black` |
| Title | `Title Only`, `Title Only_Black` |
| Title/subtitle | `Title with Subtitle Only`, `Title with Subtitle Only_Black` |
| Content | `Title and Content`, `Title and Content_Black` |
| Subtitle/content | `Title with Subtitle Content`, `Title with Subtitle Content_Black` |
| Two columns | `Two Column Slide`, `Two Column Slide_Black` |
| Three columns | `Three Column Slide`, `Three Column Slide_Black` |
| Text and image | `Title w/Image`, `Title w/Image_Black` |
| Photo and statement | `Photo + Statement`, `Photo + Statement_Black` |
| Big idea | `Big Idea` |
| Product and content | `Content w/ Product`, `Content w/ Product_Black` |
| Chart | `Chart Slide`, `Chart Slide_Black` |
| Blank | `Blank Slide`, `Blank Slide_Black` |
| Closing artwork | `Closing Slide`, `Closing Slide_Black` |

The original starter's instruction slides recommend white layouts for print, dark layouts as a projected alternative, short bullet lists, and using destination theme when moving content between decks. The reviewed file has no layouts after `Closing Slide_Black`; no legacy layout cleanup was needed.

## Install or refresh

Place the approved starter at the ignored path `Michael/runtime/brand/lenovo-starter.pptx`. For a Windows Downloads file accessible through WSL, copy it locally to that path; do not put the Windows path in a model prompt or tool call.

```sh
python3 Michael/bootstrap/office_tools.py --slides-only --check
python3 Michael/bootstrap/office_tools.py --slides-only
python3 Michael/bootstrap/office_tools.py --slides-only --check
```

The bootstrap validates the package, reports a fingerprint, updates only the PowerPoint workspace tool when `--slides-only` is selected, and stores the template bytes in the administrator valve `starter_template_b64`. No host-directory mount is needed. It preserves other valves and checks that `generate_slides` and `get_slide_layouts` are exposed. Existing Lenny and Office Documents attachments point to the same tool ID, `generate_slide_pptx`; no preset reattachment is required.

The declaration in `branding/powerpoint.json` selects starter mode and pins the approved file SHA-256. A missing or different template fails before configuration changes. To stage the approved private asset and configure the stack, run `python3 Michael/bootstrap/provision.py --starter-file /path/to/starter.pptx`; subsequent runs need only `provision.py`. For an intentional legacy deployment, set `mode` to `legacy` in that declaration and reconcile; this explicitly clears the installed template valve. Restore previous source/valves from a private backup for a full rollback.

Back up existing source and valves privately before a live update. Template bytes and exports of valves belong in ignored runtime storage. Authentication follows the existing bootstrap conventions. No container rebuild is required for tool source or valve updates.

## Model-facing interface

`get_slide_layouts()` returns the current mode, exact names, placeholder indices/types and dimensions, plus semantic aliases. It does not return the instructional deck's text or image bytes. Call it before generating so layout selection follows what is actually installed.

`generate_slides(content)` accepts a JSON string. In template mode, a slide may select an exact `template_layout`, or a semantic `layout` alias with `variant: "light" | "dark"`. Exact names take precedence. Normal fields include `title`, `subtitle`, `body`, `bullets`, `columns`, `notes`, chart data and table data. The [example](../tests/fixtures/slide-template-example.json) uses only synthetic content.

- `cover` combines its subtitle and author/date into the starter's single byline slot.
- `two_column_text` and `three_column_text` take one item per column in `columns`, each with `heading` and `bullets` or `body`.
- `chart` produces an editable chart in the chart placeholder. Supply `labels` and numeric `values`, or `datasets: [{"label": "Series", "data": [...]}]`. Invalid/missing values are rejected, not converted to zero. Column/bar/area baselines include zero. Labels and gridlines are adapted for light/dark readability; data fills use the template's blue/purple scheme colours.
- `table` inserts an editable table in a content placeholder. Supply complete `headers` and `rows`. Tables automatically paginate by measured text height, repeat headers, and allocate wider columns to longer content. Tables wider than six columns continue in groups with their first key column repeated. A single exceptionally tall row is split across continuation slides. Limits are 500 rows, 30 columns, 200,000 table characters and 200 output slides.
- Picture layouts accept `image_file_id: "<Open WebUI upload ID>"` or `image: {"file_id": "<ID>"}` (`attachment_id` is also accepted inside `image`). The model can use IDs from uploaded attachments; it must never invent an ID or pass a filesystem path. Lookup checks the calling user's ownership before resolving the stored object through Open WebUI's storage provider. Missing files, another user's uploads (including when the caller is an admin), oversized files and non-image bytes fail without a URL fallback. Only caller-owned uploads are supported; shared knowledge-base images need a separate authorized attachment workflow.
- All six native image layouts are supported: `Title w/Image`, `Photo + Statement`, and `Content w/ Product`, each with its dark variant. The picture stays in its native placeholder. Images retain their aspect ratio and transparency until PowerPoint applies the placeholder's centered fill crop; cropping remains editable. Raster formats supported by Pillow are normalized to PNG, oriented using EXIF and downscaled. Images are limited to 40 megapixels and the existing image byte cap.
- `image_url` and `base64` remain supported. Remote images retain the public-address-only network guard. Without an image the placeholder remains empty; a requested image that fails to load or a selected layout with no picture placeholder produces an error.
- `placeholders` optionally maps discovered text indices to strings or bullet arrays. Bullet objects may use `{"text": "Supporting point", "level": 1}` (levels 0–4). Text inherits the actual layout's fonts, sizing and theme. Do not guess indices.
- `closing` uses the starter's fixed closing artwork. It has no editable text slot. Supplied takeaways/contact text is automatically preserved on a preceding content slide. Blank layouts likewise have no text placeholders.
- Speaker `notes` are optional new content. The source deck's notes and instructional slides are not carried over; its stale preview thumbnail is removed and core author/title metadata is reset for the generated deck.

Template mode does not apply legacy custom themes, fonts, logo valves or decorative layouts such as the old KPI/timeline renderers. Use the installed text/column/chart layouts to express those ideas, or have an administrator explicitly select legacy mode. Unknown layout names fail clearly rather than silently changing design. `get_slide_layouts` reports all layouts even when several share the same semantic purpose.

## Validation and limits

Example using an uploaded image:

```json
{"slides": [{"template_layout": "Title w/Image", "title": "Product overview", "bullets": ["One clear benefit"], "image_file_id": "actual-upload-id-from-chat"}]}
```

The same ID can populate multiple slides. A future attachment picker can pass this field directly; no model prompt changes or host file access are needed.

`tests/test_slide_template.py` creates an entirely synthetic starter and exercises preservation of layout relationships/dimensions, removal of instructional slides/notes, inherited editable text, nested bullets, exact placeholder targeting, native chart/table/picture insertion, attachment-ID generation, ownership denial before storage access, invalid/oversized images, invalid specs/packages and legacy fallback. Existing `test_office_tools.py` still covers the old renderer, file delivery, bootstrap and image-network restrictions. Run these suites and `tests/test_document_delivery.py` with the Open WebUI Python dependencies.

For the real private starter, generate a representative deck and a layout-library deck, then open them in PowerPoint. Check light/dark contrast, text overflow, picture crops, chart editability, and that all layouts remain in the Layout menu. Reopening with python-pptx verifies package structure but does not prove PowerPoint rendering. Font substitution on a recipient's machine can still change wrapping. The runtime generator does not include a PowerPoint rendering/overflow service: keep text concise and review important deliverables visually.

Validation on 2026-10-06: 48 automated tests passed and one optional logo-asset test was skipped. Microsoft PowerPoint opened/rendered a 29-layout gallery and an eight-slide synthetic example with no detected text-bound overflow. A live model discovered the layouts and generated a six-slide deck from a real synthetic upload ID, covering all six image layouts; that deck also opened/rendered in PowerPoint with no detected text-bound overflow. The local review gallery and decks are ignored runtime artifacts, not committed fixtures.

Starter validation limits compressed bytes to 16 MiB, expanded contents to 64 MiB and entry count to 2,000; macro/embedded-object packages and externally linked content are refused (ordinary hyperlink relationships are allowed). Templates are administrator-selected trusted artwork, not arbitrary model uploads. This validation is not a document-malware scanner. Generated decks retain the starter's classification/footer artwork; changing that classification requires an approved replacement starter, not a prompt instruction.

## Open Terminal images and delivery

Select an administrator-registered Open Terminal in the chat. The PowerPoint tool
uses Open WebUI's connection access checks, user identity and chat context; it does
not use an administrator fallback or accept a terminal URL/key from the model.
The selected terminal must support the native `/execute` API and Python.

```json
{
  "title": "Workspace example",
  "terminal_output": true,
  "slides": [{
    "template_layout": "Title w/Image",
    "title": "Our product",
    "bullets": ["User-provided description"],
    "terminal_image_path": "assets/product.png"
  }]
}
```

Paths are relative to the caller's `~/workspace` (the explicit `~/workspace/`
prefix is also accepted). Browse using native terminal file tools before choosing
files. Supported raster images are transferred by the server, validated, and
inserted as native picture placeholders. No attachment to the current message or
model vision is required. This does not interpret images or embed PDF/Office
files as objects; use text extraction or convert pages to images separately.

When a terminal is selected, the default is to save the generated PPTX both to
Open WebUI (download link) and to a unique `~/workspace/output/presentation-*.pptx`.
`terminal_output: false` selects download-only delivery. `true` without an
accessible selected terminal fails before rendering. Existing files are never
overwritten. The tool returns a JSON string containing `status`, `file_id`, `file_name`,
`download_url`, `workspace_path`, `terminal_requested`, `terminal_saved`,
`warnings` and `error`.
It emits the exact Files API URL as a native file attachment as well as a link.
Full success is reported after the requested terminal copy finishes.
If terminal transfer fails, `status` is `partial_success`, the authenticated
WebUI download remains available, and no workspace path is claimed.
Files API registration failures return `status: error`; no shared cache copy or
unowned download link is created.
The download URL is a browser reference; `workspace_path` is a terminal reference.
Preserve the URL exactly, including its leading slash.

The shared source `tools/office_delivery.py` owns transfer and delivery metadata.
`bootstrap/office_tools.py` bundles it into each standalone office tool before
publishing the source; the running tool requires no additional runtime module.
Manual tool export/upload must use that bundled source.
The same delivery contract applies to `generate_document`: set `terminal_output`
in JSON or YAML frontmatter; selected-terminal output defaults to
`~/workspace/output/document-*.docx`.
DOCX continues accepting its existing Markdown/JSON content; this does not add
filesystem image import, general PDF/Office embedding or conversion to DOCX.

The bridge sends a fixed Python file-transfer program through the authenticated
terminal execution API. Model paths are encoded as data, not shell code. Reads and
writes reject traversal, symlink components, non-regular files and ownership
mismatches. Transfers use bounded 48 KiB chunks and SHA-256 checks; output is
published only after checksum validation, with temporary files removed on handled
failures. Abrupt process/container termination may leave `.part` files. Image
reads retain the 15 MiB / 40-megapixel limits. Transfer commands/output may be
visible in the user's terminal process history; file bytes are not returned to
the model context. Large files require more API calls.

Reproduce unit/package checks with `python3 Michael/tests/check_office.py` and the
opt-in model integration with `python3 Michael/tests/terminal_slide_smoke.py`.
The latter creates a synthetic terminal image and a Lenny chat, then checks that
the image is embedded and the terminal/download PPTX bytes are identical. It
leaves those synthetic artifacts for inspection. No `/tmp` helper is required.

Terminal bridge acceptance: the live Lenny smoke test completed on 2026-10-06 with an existing synthetic workspace image and no chat image attachment. The generated slide had a native picture, and SHA-256 matched between the Open WebUI download and terminal output. Unit/package checks passed 50 tests with one optional logo-asset skip.

## Recovery and layout checks

The generator tolerates JSON comments and trailing commas without evaluating code
or changing quoted strings. Other invalid JSON still produces an actionable error.
Column layouts continue extra blocks onto more slides and pad missing blocks;
long section descriptions use a full content layout. Table measurement uses
conservative font widths, explicit wrapping, row heights and a bottom margin.
Continuation slides retain native layouts and sequential page numbers. Adjustments
are returned with the download link instead of asking the model to discard rows.

Pie/doughnut charts have category labels, percentages and distinct segment colours.
More than six categories become a horizontal bar chart with category/value labels.
Long/list-valued chart interpretation is preserved on a following content slide;
short interpretation uses a caption without losing inherited chart geometry.
Missing/non-finite chart values, ambiguous matrices without series names, malformed
table rows and invalid file permissions remain errors. Error messages identify the
input slide when planning fails. The tool asks for one corrective retry, not an
unbounded loop or invented data.

Real-deck regression: five failed calls preceded a 20-slide deck. The successful
input now creates 25 slides, preserving table content and chart interpretation.
Microsoft PowerPoint rendered that deck with no detected text-bound overflow or
shapes extending below the page. The private case deck and chat logs remain ignored
runtime evidence; committed tests use synthetic text. Rendering checks do not
validate analytical claims, replica freshness or completeness of case retrieval.

Plotly charts can now export a verified PNG to the selected user’s Terminal output and authenticated Files store while keeping the interactive chat view. See [VISUALS.md](VISUALS.md) for theme/font ownership, installation and tool arguments.
