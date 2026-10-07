# Open Terminal

Open Terminal gives a chat a persistent shell, Python runtime and file workspace. Michael uses Open WebUI's **native
terminal integration**, which supplies the terminal pane and file browser. It is registered separately from MCP and
OpenAPI tool servers; do not add it to `mcp/mcp.json` ([mcp.md](mcp.md)). Upstream docs:
<https://docs.openwebui.com/features/open-terminal/>.

This file also owns the **file delivery contract**: how every tool saves files into the user's home and gives back
download links.

Contents: [Deployment contract](#deployment-contract) · [Start and register](#start-and-register) · [Use and access](#use-and-access) ·
[Operations](#operations-and-troubleshooting) · [Image and registration](#image-and-registration) ·
[Confinement and the shared area](#confinement-and-the-shared-area) · [Workspace](#development-media-and-document-workspace) ·
[File delivery contract](#file-delivery-contract) · [Verification](#verification)

## Deployment contract

| Setting | Michael configuration |
|---|---|
| Image | `ghcr.io/open-webui/open-terminal:0.14.0` pinned by digest, extended by `terminal/Dockerfile` (see [Image and registration](#image-and-registration)) |
| Internal endpoint | `http://open-terminal:8000` |
| Authentication | Private `OPEN_TERMINAL_API_KEY`, bearer |
| Persistence | `terminal-data` named volume at `/home`; `terminal-shared` at `/shared`, read-only |
| Users | Multi-user mode; Open WebUI forwards the signed-in user's id; each user gets an OS account and home |
| Access | Every user (public read grant), set with `bootstrap/open_terminal.py --access all`, which refuses to share a terminal whose file API is not confined. A new connection starts admin-only. |
| Presets | `lenny`, `case-assistant`, `office-documents` and `document-translator` have the terminal capability; users select the terminal in each chat. A delegated agent receives its parent's terminal only when its preset enables it. |
| Limits | 2 CPUs, 2 GiB memory, 512 processes (container-wide; no disk quota, so monitor volume growth) |
| Networking | Dedicated bridge shared with Open WebUI; no published host port; outbound networking available |

Per-user homes share a container, kernel, resources and network: this is workspace separation for trusted users,
not isolation against hostile users. There are no Docker socket, host filesystem, case-index or application-data
mounts. The image's sudo-based user provisioning stays enabled. Keep `.env`, configuration backups and volume
contents private, and do not put provider credentials or real case exports in the workspace. Upstream:
[multi-user](https://docs.openwebui.com/features/open-terminal/advanced/multi-user/),
[security](https://docs.openwebui.com/features/open-terminal/advanced/security/).

## Start and register

```sh
python3 Michael/bootstrap/init_env.py
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-deps open-terminal
python3 Michael/bootstrap/open_terminal.py
python3 Michael/bootstrap/open_terminal.py --check
```

The bootstrap uses the usual admin credentials. It verifies reachability, backs up the previous connection
configuration to ignored `runtime/` (mode 0600), preserves other connections and existing grants, and checks the
saved result; `--check` verifies without changing and exits nonzero when reconciliation is needed. Registration alone
does not change model capabilities: those come from `presets.json` ([models.md](models.md)). Native function calling
and a tool-capable model are required.

Adding the service to an existing deployment needs Open WebUI recreated to join the network, preserving its image,
stable `WEBUI_SECRET_KEY` and external volume: `up -d --no-deps --no-build --pull never --force-recreate open-webui`.
On a fresh deployment start Open WebUI before registering. Never use `down -v`.

## Use and access

Select **Open Terminal** from the chat's terminal selector (explicit per chat) in Lenny, Case Assistant, Office
Documents or Document Translator. The integration supplies its own tool instructions. Uploaded chat files keep Open
WebUI's default upload handling; terminal files are a separate store. The connection's `chat_uploads: filesystem`
option is deliberately **not** used: it would send attachments only to the terminal and bypass the file store that the
translator and delegation read. Access is the connection's permission under Admin Settings > Integrations > Open
Terminal; `--access` sets it (default `keep`) and re-running `--access all` adds the public grant back after you
narrowed it. Model capability alone does not grant connection access.

## Operations and troubleshooting

- Run `bootstrap/open_terminal.py --check` after restarts and configuration changes; check
  `docker compose ... ps open-terminal` and bounded logs. `/health` is liveness; verification also uses `/api/config`
  through Open WebUI.
- A missing terminal selector means the model's terminal capability is off, the connection is not registered, or the
  user lacks access; reload the chat after changing the model.
- Authentication failures: `.env` and the saved connection must hold the same key; after rotating it recreate the
  terminal and re-run registration.
- Persist files in the user's home. Packages installed outside `/home` disappear on recreation; use a reviewed derived
  image for persistent additions. Back up the home volume privately and Open WebUI's database before deployments.
  An execution timeout controls how long output is awaited, not how long a process can run.
- **Upgrade deliberately:** review the upstream release, pull a chosen version, record its digest in compose,
  recreate only the terminal, verify through Open WebUI (re-run the confinement tests and the two-user probe).
- **Rollback:** disable the native connection in Admin Settings and stop `open-terminal` (keep the volume); the
  registration script re-enables its managed connection if run again. For a full rollback restore the private
  `terminal-connections-before-*.json` through `POST /api/v1/configs/terminal_servers` after checking for newer admin
  edits. Restoring configuration does not require replacing the chat database.
- Known gap: a new user's home has no workspace scaffold (`inbox/`, `assets/`, the guide) until
  `bootstrap/terminal_workspace.py` runs as that user; the upload endpoint creates `output/` itself, so delivery works
  regardless and agents create other folders on demand. Copying the scaffold into the image's `/etc/skel` would
  fix it for every new user.

## Image and registration

`terminal/Dockerfile` extends the pinned base with Chromium (Debian `154.0.8037.92-1~deb13u1`), `fonts-liberation`,
Plotly 7.0.0, Kaleido 1.2.0 and Choreographer 1.4.0 (`BROWSER_PATH=/usr/bin/chromium`), and adds the confinement patch
and launcher. Same service, non-root user, multi-user identity, `/home` volume and limits; no new service. The image
is `michael-open-terminal:plotly-7.0.0-kaleido-1.2.0`. Chromium and Plotly serve three jobs: chart export from a
specification (`render_visualization`), screenshots of stored visual pages (`export_visual`), and LibreOffice or
Chromium based checks of generated files ([tools.md](tools.md#visuals-toolkit)). Package changes need an intentional
pin refresh and an export check; do not silently switch to a remote renderer.

```sh
mkdir -p Michael/runtime/plotly
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml build open-terminal
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-deps open-terminal
python3 Michael/bootstrap/plotly_assets.py
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-deps open-webui
```

`plotly_assets.py` copies the installed `plotly.min.js`, the regular and bold Liberation Sans fonts and the font
licence into the generated `runtime/plotly`, which compose mounts read-only at Open WebUI's `/static/plotly`, so the
browser and the terminal export use the same packaged renderer. Rebuilding the terminal ends running processes; home
files persist in `terminal-data`.

## Confinement and the shared area

Sharing the terminal with all users is controlled by the connection's grants, not the image:

```sh
python3 Michael/bootstrap/open_terminal.py --access all --check   # probes, changes nothing
python3 Michael/bootstrap/open_terminal.py --access all           # share with every user
python3 Michael/bootstrap/open_terminal.py --access admin         # take it back
```

`--access all` first asks the terminal's file API for `/proc/1/environ` through Open WebUI and refuses unless that is
denied, so a plain upstream image cannot be shared by mistake. Other grants are kept; `--access keep` never changes them.

**The problem.** Open Terminal's multi-user mode runs *commands* as each user's OS account, but its file API
(`/files/view`, read, write, upload, archive, and therefore Open WebUI's file browser and
`/api/v1/terminals/<id>/files/view` links) runs as one server account that belongs to every user's group. Upstream's only
cross-user protection is a lexical check against `/home/<other user>/`. Measured on 0.14.0 with two users:

| Request by one user | Unpatched 0.14.0 | With confinement |
|---|---|---|
| Another user's file, direct path | denied | denied |
| Another user's file through a symlink in their own home | **served** | denied |
| `/etc/passwd`, directly or through a symlink | **served** | denied |
| `/proc/1/environ` (contains the terminal API key) | **served** | denied |
| Own workspace files | served | served |
| `/shared` files | served | served |
| File-API write into `/shared` | refused | refused |
| Shell write into `/shared` (read-only mount) | refused | refused |

**The fix.** `terminal/confine/michael_confine.py` replaces the check with an allowlist on the **resolved** path: reads
may touch the caller's home or `/shared`; writes only the caller's home; everything else, including other homes and
symlinks leading there, is denied. Reads are re-verified on the open file descriptor, so a link swapped between check
and read cannot redirect it. Single-user mode is unchanged. The Dockerfile puts a launcher (`/opt/michael/bin/open-terminal`)
first on `PATH`, so the base entrypoint's `exec open-terminal` loads the patch; the launcher validates the upstream
class first and raises if it changed, so the container fails to start rather than run unconfined. Re-run the tests and
the two-user probe after any change to the pinned image. Residual risk: write checks resolve the path before opening and
upstream then `chown`s by path, so a user racing symlink swaps could attempt to redirect a write: this is separation for
trusted users, not a defence against a hostile one. Commands are unaffected and stay inside the user's OS permissions.

**Shared read-only area.** `terminal-shared` is mounted at `/shared` read-only, so no one can write there, including
through the file API. Users and agents read in place or copy (`cp -a /shared/templates ~/workspace/inbox/`). An
administrator fills it through a separate service with no network, run only on request:

```sh
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml --profile admin run --rm --no-deps \
  -v "$PWD/shared-files:/src:ro" terminal-shared-writer 'cp -a /src/. /shared/ && chmod -R a+rX,go-w /shared'
```

Never put credentials or real case exports in the shared area.

**Serving files safely.** The file endpoints answer from Open WebUI's own origin and send a file's real content type, so
a link to an `.html` or `.svg` could run its script as the signed-in user (Open WebUI's own file store forces those
types to download). `HardenDownloads` in `michael_confine.py` sends `nosniff` on every served file; gives HTML, SVG,
XML and script `Content-Security-Policy: sandbox allow-scripts` (an opaque origin with no access to the app or session)
on both `/files/view` and `/files/serve/`; and forces those types to download on `/files/view`, which links use. The
file browser's HTML preview (an iframe on `/files/serve/`) keeps working. The rule depends only on path and content
type, because Open WebUI's proxy does not forward `Sec-Fetch-Dest`.

**Deliverable permissions.** The file API runs as the server account, a member of each user's private group only, so a
served file must be group-readable and its folders group-enterable. Files and folders the upload endpoint creates are
(`-rw-rw-r--`, `drwxrws---`). Programs that write as the user set modes explicitly: the chart renderer and screenshot
program write `0640` files and `2770` folders regardless of umask. An earlier bridge published `0600` and `0700`, so
links failed with a permission error; saving through the upload endpoint removed that class of bug. Any new tool that
writes files itself must do the same.

## Development, media and document workspace

The pinned full image supplies Python, Node/npm, Git, FFmpeg, ImageMagick, LibreOffice, Pandoc, python-pptx,
python-docx, Pillow, pandas, openpyxl, FastAPI and Uvicorn. `python3 Michael/bootstrap/terminal_workspace.py`
scaffolds `~/workspace` for the **authenticated account** through its native terminal proxy (creates missing files
only; re-running preserves user edits; to prepare another user's home authenticate as that user; never copy an admin
home). Templates live in `terminal/workspace/`; the canonical workspace guide there covers folders (`inbox` for
user-supplied files, `projects`, `assets`, `output` for deliverables), project environments, web previews, converting
source files to Word or PowerPoint, and image and video commands, and `environment.json` records the installed
versions. The `terminal-workspace` skill ([skills.md](skills.md)) tells models to read it. For web apps Open WebUI
detects listening ports and proxies them into its native preview (no published host port); servers stop on container
recreation, so this is a development workspace, not hosting; never serve the whole home or the inbox. The starter
source-packet helper handles text, Markdown and PNG/JPEG; richer PDF, Office and spreadsheet workflows need extraction
and authored layout. The branded generators remain the route for Lenovo deliverables. The terminal has no generative
image or video models.

## File delivery contract

Every tool that produces a file, or moves one between a chat and the terminal, uses one shared module,
`tools/workspace_delivery.py`, bundled into each standalone tool ([tools.md](tools.md#managed-extensions)).
`tests/test_delivery_contract.py` fails if a tool defines its own terminal upload, naming, path or link code. Add
nothing of your own for saving, naming, links or results.

### What a user and a model can rely on

| Behaviour | Rule |
|---|---|
| Default destination | `~/workspace/output` in the signed-in user's own home |
| Choosing a destination | `save_to`: a folder, or a full file path ending in the file's extension, anywhere in the user's home |
| Relative paths | Under `~/workspace` (`projects/report` is `~/workspace/projects/report`); `~/folder` is the home itself; `workspace/...` is accepted as written |
| Absolute paths | Never for writing; `/shared/...` can be read and linked |
| Names | Each tool's own readable name; path separators and control characters removed; spaces and non-ASCII kept |
| Existing files | **Never overwritten**: a taken name gets a short random suffix (`report-1a2b3c4d.docx`) and the result says where it went |
| Size | No limit in the tools; the upload endpoint streams. The practical ceiling is the terminal's 2 GiB memory |
| Links | `terminal_download_url`: `/api/v1/terminals/<id>/files/view?path=<path>`, served only to the file's owner (and `/shared` to everyone); the user's identity comes from the Open WebUI session, never the model, and a request without a session returns 401 |
| Without a terminal | A tool that can still deliver (Word, PowerPoint, translator) returns its Open WebUI download alone; an explicit `save_to` without a usable terminal fails before any work |

`save_to` is a real parameter on `generate_document`, `generate_slides`, `render_visualization`, `export_visual`,
`translate_attachment` and `deliver_translation` (generators also accept it as a spec or frontmatter key; the
parameter wins). It must be a parameter, not only a spec key: models mostly miss options buried in a long
description.

### The result every tool returns

`status` (`success`, `partial_success`, `error`), `file_name`, `size`, `file_id` and `download_url` (the Open WebUI
copy, when there is one), `workspace_path` (`~/...`, only when the terminal copy is confirmed), `terminal_download_url`
(only with `workspace_path`), `terminal_requested`, `terminal_saved`, `warnings`, `error`, `message` and
`instructions` (tell the model to give every link exactly as written). A failed terminal copy is `partial_success`,
keeps the download and claims no path. A Files API registration failure is an `error` with no unowned link and no
shared cache copy. Delegated agents' result messages list files under "Files produced" ([tools.md](tools.md#delegate-agents)).

### How a file travels

`_terminal_save(context, data, filename, save_to=, extension=, content_type=)`:

1. `_destination` picks the folder and name; a bad path fails here, before anything is sent.
2. `_terminal_names` lists the folder and `_unique` avoids a clash.
3. The file goes through the terminal's own `/files/upload` endpoint (bytes, or a `pathlib.Path` streamed from disk) as
   the signed-in user: identity headers come from Open WebUI's request, never from the model. The form is sent with
   `quote_fields=False`, otherwise `a b.pdf` would be stored as `a%20b.pdf`.
4. The returned size and location are checked; otherwise nothing is claimed.

Calls omit the `X-Session-Id` header: with it the terminal resolves relative paths in `/files/list` against the chat
shell's current directory while upload and view use the home. Tools that must run code in the terminal as the user
(`_terminal_plotly`, `_terminal_screenshot`, reading a workspace image with `_terminal_image`) use fixed programs with
directory descriptors and `O_NOFOLLOW`; they write `0640` files and `2770` directories and never overwrite. Model paths
are data, never shell code; reads reject traversal, symlink components, non-regular files and ownership mismatches;
image reads keep the 15 MiB and 40-megapixel limits. Those programs' commands may appear in the user's terminal
process history; file bytes never return to the model. The screenshot program receives only a page name: the page is
uploaded to `~/.michael-render/` first because a page can exceed a command line, and is deleted afterwards.

### Email attachments

The mail service accepts only plain Open WebUI file ids as suggested attachments (at most five; it rejects paths and
`terminal:` references), and the review form uploads whichever ticked files the user owns on the user's click.
`workspace_files.prepare_email_attachments` turns attachment ids and terminal paths into ids (terminal files are copied
into Open WebUI's file store for the user, streamed, any size) and the model passes them as `suggested_attachment_ids`;
the user decides in the form ([mcp.md](mcp.md#mail), [tools.md](tools.md#workspace-files)).

### Presenting downloads

Keep both storage destinations and the structured fields for internal reuse. In the final answer show **one URL per
artifact**: prefer the registered Files `download_url`; use `terminal_download_url` only when no Files URL exists and
the terminal copy is verified; never both for the same artifact. Preserve the leading slash exactly and disclose partial
failures. Keep `workspace_path` internal for reuse (for example `terminal_image_path`) and show it only for an explicit
save, open, edit or reuse request. When neither URL exists, explain the failure instead of inventing a link. Files
success: `[Download report](/api/v1/files/<id>/content)`; terminal-only fallback:
`[Download report](/api/v1/terminals/<terminal>/files/view?path=<encoded-file>)`.

### Writing a tool that makes files

1. `from workspace_delivery import _terminal_context, _terminal_save, _terminal_download_url, _office_result` (the
   bundler replaces the line with the module).
2. Add `save_to: Optional[str] = None` and a `:param save_to:` line.
3. If a terminal is selected or `save_to` is given, resolve `_terminal_context` before doing any work.
4. Save with `_terminal_save`; return the standard result with `terminal_id=` so the link is included.
5. Register the tool in `extensions.json` (public read grant) and attach it to the presets that need it.
6. Test with `tests/test_workspace_delivery.py` (a fake terminal over real HTTP) and `tests/test_delivery_contract.py`.

## Verification

Unit tests: `test_open_terminal.py` (admin-only defaults, required authentication, grant preservation, duplicate ids,
concurrent edits, verification and read-back failures), `test_terminal_confine.py` (the confinement against the real
upstream file layer, real temporary directories and symlinks; needs `--with open-terminal==0.14.0`),
`test_workspace_delivery.py`, `test_workspace_files.py`, `test_delivery_contract.py`. A throwaway container with two
real OS users and a read-only `/shared` produced the confinement table above, and an unpatched twin leaked as shown.

Live acceptance through the authenticated Open WebUI proxy (2026-10-07): own files by absolute and home-relative path
served; `/etc/passwd` and `/proc/1/environ` denied (403); `/shared` read and download served; file-API write, mkdir
and delete in `/shared` refused; shell write in `/shared` read-only; copy from `/shared` into the workspace works; a
synthetic second user was denied the first user's files directly and through a symlink and could read but not write
`/shared`; HTML and SVG download sandboxed while text, PDF and Word are unchanged. End to end: Lenny, with the terminal
selected, delegated a Word document and a translation to the specialist presets as a non-admin user; each file landed
in that user's own `~/workspace/output` with the right mode, the terminal link and the Open WebUI link downloaded
identical bytes (401 without a session), the translated text was really translated, and the admin's session on the
same link found nothing in the admin's own home. Lenny also saved a deck to a requested folder in one call, avoided
overwrites with a suffix, exported charts, translated into `~/Documents/translations`, published files by relative path
and drafted an email with one attachment id and one terminal path. Not tested: a browser click-through, hostile-user
isolation, the review form click-through, fresh-volume installation.
