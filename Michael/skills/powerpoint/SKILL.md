---
name: PowerPoint
description: How to plan and generate a Lenovo-styled PowerPoint (or Word) file with the generate_slides and generate_document tools, including charts, tables and images, how to edit an existing deck in the terminal, and how to show a deck inline. Load it before building, editing or showing a deck, or before briefing the office-documents agent to build one.
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
3. Make **one** `generate_slides` call with the complete deck. Call `get_slide_layouts` again for every new deck, even if you called it earlier in the chat, and use `template_layout` with an exact name from it on EVERY slide. Do not use the `layout` aliases (`cover`, `title_body`, `title_bullets`, `section`), `theme`, `eyebrow`, `chips`, `number` or icons: the starter template does not draw them, and the result lists them under `layout_adjustments`. To change a deck that already exists, edit that file (see "Editing an existing deck") instead of regenerating it.
4. Slides: `slides: [{template_layout, title, subtitle, body or bullets[], columns, notes, ...}]`.
   - **Text**: write `body`/`bullets[]` as plain text. The layout draws the bullet, so never type `•`, `-` or `*` (they would show twice) and never use blank lines (they are removed). A `bullets[]` entry is one point; `body` splits on newlines into the same bulleted list. For a heading inside a slide use `columns[{heading, bullets}]` (the heading is not bulleted), not a first line of body text.
   - **Chapter dividers**: `template_layout: "Section Header_White"` with `title` and `subtitle`. Never use a content layout for a divider: its text becomes a bullet.
   - **Tables**: `headers[]` and `rows[]` on a content layout. Never write a table as text with `|` separators or Markdown. Supply the whole table: long ones continue on more slides with repeated headers.
   - **Native charts**: `template_layout: "Chart Slide"` (or alias `chart`) with `chart_type`, `labels[]` and `datasets[{label, data}]`. Use it for up to five series; more series are stacked automatically. Multi-series data goes in `datasets`, not a matrix in `values`.
   - Prefer a native chart for a plain bar, line or pie: it needs no image. Use a rendered image only for what a native chart cannot show.
   - **Dense or styled charts**: use a rendered image. `template_layout: "Chart Slide"`, `terminal_image_path` set to the image's `workspace_path`, and `image_fit: "contain"` so the whole image shows. The path must be a `workspace_path` that `export_visual` or `publish_workspace_file` returned, never a bare file name or a `/tmp` path. Create a NEW deck with `generate_slides` only: never build one from scratch with python-pptx after an error; read the error, fix the input once, and otherwise tell the user.
   - **Photos**: picture layouts (`Title w/Image`, `Photo + Statement`) crop to fill; add `image_fit: "contain"` to keep the whole image. Use an `image_file_id` from the conversation or a `terminal_image_path`; never invent ids or paths. Browse the terminal first; do not guess paths.
   - Closing and blank layouts have no editable text: put takeaways and contacts on the slide before.
5. Leave themes, colours, fonts, logos and image search terms unset unless the user asked.
6. `save_to` (a real parameter) saves elsewhere in the user's home: a folder (`projects/report`, relative to `~/workspace`, or `~/folder`) or a full path ending `.pptx`. Do not copy the file afterwards with the shell.

## Editing an existing deck

You CAN edit a deck you made: every deck `generate_slides` creates is saved in the user's terminal, at the `workspace_path` of its result. Never say you cannot edit a deck and never regenerate the whole deck just to add, change, move or delete a slide: that loses the user's earlier fixes. Edit the file in the terminal with python-pptx (`run_command`; it is installed), which keeps the Lenovo template, fonts and layouts because they live in the file.

- Use the **exact `workspace_path` of the latest result** for that deck. A repeated name gets a new suffix, so a guessed name opens an older version. If the user uploaded a deck, find it with the terminal's file tools.
- Always save to a NEW name (for example `-v2`), never over the original, then show it (below).
- Add a slide from the deck's own layouts (`prs.slide_layouts`), never a blank python-pptx layout or copied shapes; put text in the placeholders and charts in the large placeholder's box.

```python
import os
from pptx import Presentation
from PIL import Image

path = os.path.expanduser('~/workspace/output/deck.pptx')    # the exact latest workspace_path
prs = Presentation(path)
ids = prs.slides._sldIdLst
for i, s in enumerate(prs.slides, 1):                         # 1. look first
    print(i, s.slide_layout.name, s.shapes.title.text if s.shapes.title else '')

s = prs.slides[1]                                             # 2. change text (0-based index)
s.shapes.title.text = 'New title'
next(p for p in s.placeholders if p.placeholder_format.idx == 1).text_frame.text = 'First point\nSecond point'

layout = next(l for l in prs.slide_layouts if l.name == 'Chart Slide')   # 3. add a chart-image slide
new = prs.slides.add_slide(layout)
new.shapes.title.text = 'Weekly note activity'
box = max((p for p in new.placeholders if p.placeholder_format.idx != 0), key=lambda p: p.width * p.height)
image = os.path.expanduser('~/workspace/output/chart.png')    # a workspace_path from export_visual
w, h = Image.open(image).size
k = min(box.width / w, box.height / h)
new.shapes.add_picture(image, box.left + (box.width - int(w * k)) // 2, box.top + (box.height - int(h * k)) // 2, int(w * k), int(h * k))
box._element.getparent().remove(box._element)

el = list(ids)[len(ids) - 1]                                  # 4. move the last slide to position 3 (0-based 2)
ids.remove(el); ids.insert(2, el)

rid = ids[0].rId                                              # 5. delete a slide (here the first)
prs.part.drop_rel(rid); ids.remove(ids[0])

out = path.replace('.pptx', '-v2.pptx')
prs.save(out); print(out)
```

Do only the steps the user asked for. Then check the result (print the slide titles again) before you show it.

## Showing a deck

After you generate OR edit any deck or Word document, call the terminal's `display_file` with `path` set to the file's `workspace_path`, `inline: true` and, for a deck, `page` set to the first slide you changed (1-based). It shows the slides in the chat, so the user sees the result without opening the file. Do it every time, then give the download link as below, and do not display the same file twice.

## Word documents

`generate_document` takes Markdown with optional frontmatter. Give a clear structure, conclusions that can be acted on, and enough detail for the audience. One call produces the complete file. `save_to` works the same way.

## Result and reply

The result has `status`, `download_url`, `workspace_path`, `terminal_saved`, `terminal_download_url` and `warnings`. Show **one** URL per file: `download_url` if present, else `terminal_download_url` when the terminal copy is verified; never both. Keep `workspace_path` internal unless asked. Lead with the link, say what the file contains, list placeholders and assumptions, and report partial success honestly. Do not paste the document into chat. Never claim a file exists without a returned link. On failure, say what went wrong and a useful next step; do not retry blindly.

To attach the file to an email, the parent uses `prepare_email_attachments` (see the **mail-drafting** skill).
