# Knowledge bases

Open WebUI knowledge bases hold documents that models search with the built-in knowledge tool. Michael seeds one
base, **SOPs**, from committed Markdown and ships a tool for maintaining knowledge bases from chat. Lenny, Case
Assistant and the PATH assistant have **no** knowledge bases (explicit empty overrides): people, roles and cases come
from QDTS, not from SOP text.

## The SOPs base

`knowledge/manifest.json` declares each base (`id`, `name`, `description`, `files`); `knowledge/` holds the committed
Markdown. The first base, **SOPs** (id `sops`), holds `sops/path-sop.md` (PATH, the package arrival tracking system:
who to ask, setup, pickup, check-in) and `sops/ai-video-workflow.md` (the AI video creation workflow: HyperFrames,
ComfyUI, ElevenLabs). Both were converted from source PowerPoint decks with `bootstrap/pptx_to_markdown.py`: every
slide's text, tables and speaker notes with link targets; images and videos are left out and the large `.pptx` files
are not committed. Each file starts with an overview naming the owner and who to ask. **Knowledge base text is public
in this repository**, so review a deck before converting it (the PATH deck contained internal network addresses).

Presets that receive it: Document Translator, Web Searcher, Office Agent, Knowledge Base Manager and Office Documents
(from the top-level `knowledge_bases` in `models/presets.json`). With a base attached, `desired_model()` also turns on
the built-in knowledge tool, which is how a native-calling model searches it. A knowledge base a user attached to a
preset in the app is kept; one this repository provisions and a preset no longer declares is detached
([models.md](models.md#declaration-format)).

## Provisioning

`bootstrap/knowledge_bases.py` creates each base (matched by exact name) with **public read and write grants** (every
user can search it and add or edit files; an existing base gets whichever grant is missing; other grants are kept) and
uploads every seed file that is missing, waiting until Open WebUI has extracted and indexed it. It never deletes
anything and leaves users' own files, folders and edits alone: a seed file whose live text differs from the committed
copy is reported as a NOTE and overwritten only with `--update`. The base name is its identity: if it is renamed in the
app, rename it in the manifest too, or the next run creates a new one.

```sh
python3 Michael/bootstrap/knowledge_bases.py --check    # exit 1 if a base or seed file is missing
python3 Michael/bootstrap/knowledge_bases.py            # create what is missing
python3 Michael/bootstrap/knowledge_bases.py --update   # also overwrite files edited in the app with the seed
python3 Michael/bootstrap/presets.py                    # attach the base to the presets that declare it
```

To add a seed document: put the Markdown under `knowledge/`, list it in `manifest.json`, run the script. Users can also
drop a file into a chat with the **Knowledge Base Manager** preset and ask it to create an entry; those entries live in
the app's volume and are not committed.

## Knowledge Base Manager tool

`tools/knowledge_base_manager.json` is an Open WebUI tool export, imported by `bootstrap/kb_manager_tool.py` (compared
by source, so re-runs change nothing) and readable by every user. It calls Open WebUI's own API at
`http://127.0.0.1:8080` (inside the container) with the signed-in user's own token, so it can do only what that user may
do; it has no valves, stores no secrets and logs nothing. It is attached to the **Knowledge Base Manager** preset only.

**Deletions are confirmed by the user, not the model.** Every delete (a file by id or path, a folder, a knowledge base)
needs the model's `confirm=true` (and `allow_nonempty=true` for a non-empty base) and then the tool asks the signed-in
user through Open WebUI's confirmation dialog in the chat (`__event_call__`, type `confirmation`). Only the user's click
on Confirm deletes anything; Cancel, a closed tab, a timeout or a call with no chat window (an API client) all end in
"Deletion cancelled", and the model cannot answer the dialog. Deleting a file removes it from every base that uses it.
`upsert` and `update` replace file content without a dialog (the prompt makes the model ask first). New bases are
private to the user. The seed base is readable and **editable** by every user, so a non-admin can change it through the
tool and everyone sees the change.

Prompt contract for the preset: read the full source, preserve its facts, resolve the target and check duplicates,
create explicitly requested content without redundant confirmation, identify the exact base, path, file and loss
before a replacement and obtain explicit approval, delete only the exact requested target, warn that deleting a file
affects every base using it, and report changes and confirmed indexing status.

## Verification

`tests/test_knowledge_bases.py` holds the unit tests (manifest handling, grants, seed upload, `--update`).
`tests/stack_e2e.py` proves the confirmation dialog on a throwaway stack (declined: nothing deleted; confirmed:
deleted), and `tests/knowledge_e2e.py` exercises the base end to end.
