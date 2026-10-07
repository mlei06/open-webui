# Skills

System prompts hold who an assistant is, who the user is, how to answer and the few rules that must never
slip. How to use each tool, and how to plan a multi-step answer, lives in **skills**: Markdown guides that a
model loads on demand with Open WebUI's `view_skill` tool.

## What is installed

| Skill id | For | Who works from it |
|---|---|---|
| `qdts` | What QDTS holds, the eight tools, filter and aggregation rules, simple lookups and thirteen worked multi-step examples, anti-patterns | Lenny, Case Assistant |
| `visualization` | `render_visualization` for evidence charts; any other `render_*` tool followed by `export_visual`; putting an image in a deck | Lenny, Case Assistant |
| `delegation` | `list_agents`, `delegate_agents`, writing a self-contained brief, ending the turn after dispatch, handling results | Lenny |
| `web-search` | Parent: when to delegate and how to write a safe query. Agent: `search_web`, `fetch_url`, sourcing | Lenny briefs, Web Searcher works |
| `document-translation` | Parent: the hand-over. Agent: `translate_attachment`, `deliver_translation`, status, cancel | Lenny briefs, Document Translator works |
| `powerpoint` | Parent: the brief. Agent: `get_slide_layouts`, `generate_slides`, charts, tables, images, Word | Lenny briefs, Office Documents works |
| `mail-drafting` | Drafts, recipients, attachments through `prepare_email_attachments`, the review-form rule | Lenny |
| `path-mailroom` | The nine read-only PATH tools and what the records mean | Lenny |
| `terminal-workspace` | Workspace layout, download links, command rules | Lenny, Case Assistant |

`skills/<id>/SKILL.md` holds each one: a frontmatter block (`name`, `description`) and the instructions.
The description is what a model sees in its manifest, so it says when to load the skill.

## How the models see them

Open WebUI lists **every skill the user may read** to every model that has built-in tools; a model's own
skill list is used only when built-in tools are off. A prompt therefore cannot hide a skill from one preset.
Each preset's system prompt names the skills to load and for what, and `view_skill` loads the full text
on demand, so a model only pays for the guides it uses. Skills have a public read grant; never put a secret in one.

## Lenny's shape

Lenny's prompt is its role, the `<user_context>` block, the answer style, the skill list with when to use each,
one paragraph on delegating, and three rules (untrusted data, nothing internal outside, mail is only sent by the
user). Lenny keeps every tool: QDTS, charts, web search, translation, deck and Word generation, mail, PATH and
terminal files. It does short, simple jobs itself and delegates to `web-searcher`, `document-translator` or
`office-documents` when a job is tool-heavy (many searches, a large document, a whole deck), when it can run in
the background, or whenever the user asks it to. Case Assistant's prompt is its role, answer pattern and pointers to `qdts`, `visualization` and
`terminal-workspace`. The specialist agents keep their own prompts plus one pointer to their skill.

## Install and change

`python3 Michael/bootstrap/skills.py` (also `provision.py --only skills`) creates or updates every skill and
read-backs the result; `--check` reads only. Skills that are not declared (a user's own) are never touched; list
ids in `skills/retired.json` to remove old ones. Edit the Markdown, run the installer, no restart is needed.
`tests/test_skills.py` checks the declarations, the installer against a fake server, that the prompts stay
minimal (no tool names, within a size limit) and that every skill a prompt names exists.

## Verified live

Through Lenny on 2026-10-07: a question about wifi cases on ThinkPads made it load `qdts` and answer with one
aggregate call and one entity read; a quick web question was searched directly with a citation; the same question
with an explicit "delegate this to the web searcher" made it load `delegation`, delegate once, end its turn and resume
with a cited answer; a deck request made it load `qdts` and `powerpoint`, aggregate, render the chart and delegate to
`office-documents` with the image path (the deck had the chart on a `Chart Slide` and a real table).

Findings: with nothing else to do after dispatching, a parent model polled, set timers and re-dispatched until the
dispatch result and the delegation skill said plainly to end the turn. Models do not always load a skill before the
first call (in the deck run Lenny skipped `visualization` and `delegation`); the prompt wording is explicit but not
enforceable, and behaviour should be spot-checked after prompt changes. A test harness that bypasses the UI must send
`features.web_search` or the model has no search tool.
