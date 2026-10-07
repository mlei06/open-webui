---
name: PowerPoint
description: How to plan and generate a Lenovo-styled PowerPoint (or Word) file with the generate_slides and generate_document tools, including charts, tables and images. Load it before building a deck, or before briefing the office-documents agent to build one.
---

# PowerPoint and Word generation

The generator tools are `get_slide_layouts`, `generate_slides` and `generate_document`. Build a small deck or a short document yourself with the steps below. For a whole multi-slide deck, a long document, or when it can run in the background, or when the user asks you to delegate, hand it to the **office-documents** agent (see the **delegation** skill) with a complete brief.

## Briefing the agent (when you delegate)

Put everything in the delegated `task` and `context`; the agent sees nothing else from this chat:
- the deliverable (deck or Word), audience, purpose, length, language, title;
- the real content or data (numbers, tables, quotes, findings with their sources and dates), never invented figures;
- images and charts: the `workspace_path` of each rendered image (see the **visualization** skill) and where it should go;
- where to save, if not the default `~/workspace/output`;
- attached source files: pass their ids in `file_ids`.
Ask the agent to return the one download link and a one-line description. Then relay the link exactly.

## Building a deck (you or the agent)

1. Call `get_slide_layouts` first. It lists the installed starter's layouts and placeholders. Use only those exact `template_layout` names (for example `Title and Content`, `Chart Slide`, `Title w/Image`, `Photo + Statement`), or the aliases such as `cover`, `title_bullets`, `two_column_text`, `comparison_two`, `chart`, `table`, `closing`.
2. Plan one point per slide with a meaningful heading and short bullets. Mark unknowns as [placeholders]; never invent facts. Use the supplied source material and tell the user what is missing.
3. Make **one** `generate_slides` call with the complete deck. Revisions are a new file, not an edit.
4. Slides: `slides: [{template_layout, title, subtitle, body or bullets[], columns, notes, ...}]`.
   - **Tables**: `headers[]` and `rows[]` on a content layout. Never write a table as text with `|` separators or Markdown. Supply the whole table: long ones continue on more slides with repeated headers.
   - **Native charts**: `template_layout: "Chart Slide"` (or alias `chart`) with `chart_type`, `labels[]` and `datasets[{label, data}]`. Use it for up to five series; more series are stacked automatically. Multi-series data goes in `datasets`, not a matrix in `values`.
   - **Dense or styled charts**: use a rendered image. `template_layout: "Chart Slide"`, `terminal_image_path` set to the image's `workspace_path`, and `image_fit: "contain"` so the whole image shows.
   - **Photos**: picture layouts (`Title w/Image`, `Photo + Statement`) crop to fill; add `image_fit: "contain"` to keep the whole image. Use an `image_file_id` from the conversation or a `terminal_image_path`; never invent ids or paths. Browse the terminal first; do not guess paths.
   - Closing and blank layouts have no editable text: put takeaways and contacts on the slide before.
5. Leave themes, colours, fonts, logos and image search terms unset unless the user asked.
6. `save_to` (a real parameter) saves elsewhere in the user's home: a folder (`projects/report`, relative to `~/workspace`, or `~/folder`) or a full path ending `.pptx`. Do not copy the file afterwards with the shell.

## Word documents

`generate_document` takes Markdown with optional frontmatter. Give a clear structure, conclusions that can be acted on, and enough detail for the audience. One call produces the complete file. `save_to` works the same way.

## Result and reply

The result has `status`, `download_url`, `workspace_path`, `terminal_saved`, `terminal_download_url` and `warnings`. Show **one** URL per file: `download_url` if present, else `terminal_download_url` when the terminal copy is verified; never both. Keep `workspace_path` internal unless asked. Lead with the link, say what the file contains, list placeholders and assumptions, and report partial success honestly. Do not paste the document into chat. Never claim a file exists without a returned link. On failure, say what went wrong and a useful next step; do not retry blindly.

To attach the file to an email, the parent uses `prepare_email_attachments` (see the **mail-drafting** skill).
