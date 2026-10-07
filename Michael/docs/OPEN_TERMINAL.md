# Open Terminal

[Integration index](MCP.md) · [Deployment](../README.md)

Open Terminal gives a chat a persistent shell, Python runtime and file workspace.
Michael uses Open WebUI's **native terminal integration**, which supplies the terminal
pane and file browser. It is registered separately from MCP/OpenAPI tool servers;
do not add it to `mcp/mcp.json`. See upstream [connecting instructions](https://docs.openwebui.com/features/open-terminal/setup/connecting/).

## Deployment contract

| Setting | Michael configuration |
|---|---|
| Image | `ghcr.io/open-webui/open-terminal:0.14.0`, pinned by digest in Compose |
| Internal endpoint | `http://open-terminal:8000` |
| Authentication | Private `OPEN_TERMINAL_API_KEY`, bearer authentication |
| Persistence | `terminal-data` named volume mounted at `/home` |
| Users | Multi-user mode; Open WebUI forwards the signed-in user's ID |
| Initial access | Admin only (`config.access_grants: []`) |
| Model | Lenny has the terminal capability; select the terminal in chat |
| Limits | 2 CPUs, 2 GiB memory, 512 processes |
| Networking | Dedicated bridge shared with Open WebUI; no published host port |

The full image supports per-user home directories. They share a container/kernel,
resources and network; this is workspace separation for trusted users, not isolation
against hostile users. Outbound networking remains available. There are no Docker
socket, host filesystem, case-index or application-data mounts. The image's sudo-based
user provisioning remains enabled. See upstream [multi-user behavior](https://docs.openwebui.com/features/open-terminal/advanced/multi-user/)
and [security guidance](https://docs.openwebui.com/features/open-terminal/advanced/security/).

The terminal key comes from the private Compose environment and is stored in Open
WebUI's admin-managed connection. WebUI also loads the stack environment file. Keep `.env`, configuration backups and volume contents private.
Do not put provider credentials or real case exports in the terminal workspace by default.
The Case Assistant retains its read-only QDTS tools and has no terminal capability.

## Start and register

From the repository root, initialize missing keys, start the service, then reconcile
the dedicated connection through the authenticated admin API:

```sh
python3 Michael/bootstrap/init_env.py
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-deps open-terminal
python3 Michael/bootstrap/open_terminal.py
python3 Michael/bootstrap/open_terminal.py --check
```

The bootstrap uses the existing admin authentication variables described in
[the deployment guide](../README.md). It verifies reachability, backs up the previous
connection configuration to ignored `runtime/` with mode 0600, preserves other
connections and existing access grants, and checks the saved result. `--check` verifies
without changing settings and exits nonzero when reconciliation is needed.

Use the existing preset provisioning workflow in [SYSTEM_PROMPTS.md](SYSTEM_PROMPTS.md)
to apply Lenny's source capability on new installations. Registration alone does not
change model capabilities. The live rollout updates only Lenny's terminal capability.
Native function calling and a tool-capable model are required.

When adding this service to an existing deployment, recreate Open WebUI to attach the
new network. Preserve its image, stable `WEBUI_SECRET_KEY` and existing external data
volume; do not pull a new WebUI image as part of this operation:

```sh
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-deps --no-build --pull never --force-recreate open-webui
```

For a fresh deployment, start Open WebUI before registering the terminal. For an
existing deployment, attach the network before running the registration script.
Never use `down -v` to relaunch.

## Use and access

As an admin, open Lenny, select **Open Terminal** from the chat's terminal selector,
and request a small shell/Python task. Use the terminal pane/file browser to inspect
the workspace. The integration supplies its own tool instructions; terminal selection
is explicit per chat. Other specialists remain unchanged. Uploaded chat files retain
Open WebUI's default upload handling; terminal workspace files are a separate store.

To expand access, edit the connection's permissions under Admin Settings → Integrations
→ Open Terminal. Grant only the intended users/groups. The bootstrap preserves these
grants on subsequent runs. Model capability alone does not grant connection access.

## Operations and troubleshooting

- Run `bootstrap/open_terminal.py --check` after restarts and configuration changes.
- Check `docker compose --env-file Michael/.env -f Michael/docker-compose.yaml ps open-terminal`
  and bounded service logs. `/health` is a liveness check; authenticated verification
  additionally uses `/api/config` through Open WebUI.
- A missing terminal selector can mean the model's terminal capability is disabled,
  the connection isn't registered, or the signed-in user lacks access. Reload the chat
  after changing the model configuration.
- Authentication failures: confirm `.env` and the saved connection use the same key.
  After deliberate key rotation, recreate the terminal and rerun registration.
- The CPU/memory/process limits are container-wide. There is no disk quota in this
  Compose configuration; monitor volume growth. An execution timeout controls how
  long output is awaited, not necessarily how long a process can run.
- Persist files in the user's home. Package installations outside `/home` may disappear
  on container recreation; use a reviewed derived image for persistent extra packages.
- Back up the named home volume privately. Back up Open WebUI's database before a
  deployment. Connection backups contain bearer credentials and must stay private.

See upstream [configuration options](https://docs.openwebui.com/features/open-terminal/advanced/configuration/)
and [Docker installation](https://docs.openwebui.com/features/open-terminal/setup/installation/).
Version selection was checked against [v0.14.0](https://github.com/open-webui/open-terminal/releases/tag/v0.14.0)
on 2026-10-06. Upgrade deliberately: review the release, pull a chosen version, record
its digest in Compose, recreate only the terminal, and verify through Open WebUI.

## Rollback

Disable the native connection in Admin Settings and stop `open-terminal`; keep its
volume. Restore the previous Lenny capability if necessary. The registration script
will re-enable its managed connection if run again. For a complete configuration
rollback, restore the private `terminal-connections-before-*.json` using the authenticated
`POST /api/v1/configs/terminal_servers` API after checking for newer admin edits.
Restoring connection configuration does not require replacing the chat database.

## Validation

Synthetic bootstrap tests cover admin-only defaults, required authentication,
existing connection/grant preservation, duplicate IDs, concurrent edits, verification
failure and read-back. Run:

```sh
python3 -m unittest discover -s Michael/tests -p 'test_open_terminal.py'
```

Live acceptance: healthy container, authenticated native verification, denied
unauthenticated API access, admin proxy command execution, and successful registration
read-back after Open WebUI recreation. These checks do not establish hostile-user
isolation or guarantee a particular model will use shell tools correctly.

Validated on 2026-10-06 against the running Open WebUI 0.11.4: all 7 terminal
bootstrap tests and 39 preset tests passed; the terminal was healthy, unauthenticated
command requests returned 401, and a Python command completed through the authenticated
WebUI proxy in a per-user home. Registration was idempotent. WebUI was recreated with
its existing image, external data volume, encryption key and CA bundle. Existing
Lenny/Case Assistant prompts and other running service containers were preserved.
A private SQLite backup was taken before recreation. A model-generated terminal
conversation and a fresh-volume installation were not tested.

## Development, media and document workspace

The pinned full image already supplies the required baseline: Python, Node/npm,
Git, FFmpeg, ImageMagick, LibreOffice, Pandoc, python-pptx, python-docx, Pillow,
pandas, openpyxl, FastAPI and Uvicorn. No runtime-wide package upgrades are needed.

Run `python3 Michael/bootstrap/terminal_workspace.py` to scaffold `~/workspace`
for the **authenticated account**, through its native terminal proxy. It creates
missing files only; existing files are preserved. To prepare another user's home,
authenticate as that user after granting terminal access. Never copy an admin home
or assume all users share the same workspace. Re-running preserves user edits;
review template changes before applying them to existing workspaces.

The canonical [workspace guide](../terminal/workspace/README.md) covers folders,
project environments, web previews, source-file-to-Word/PowerPoint generation and
image/video commands. Templates live in `terminal/workspace/`; Lenny is instructed
to read the installed guide when using a selected terminal. `environment.json`
records the available versions at initial setup. Project packages persist under
the user's home; system additions should go in a reviewed derived image.

Upload source files through the terminal file browser into `workspace/inbox`.
The starter source-packet helper supports text/Markdown and PNG/JPEG, while richer
PDF/Office/spreadsheet workflows need extraction and authored layout. Existing
Lenovo document generators remain available for branded deliverables. Terminal
editing uses local libraries and does not supply generative image/video models.

For web apps, Open WebUI detects listening ports and proxies them into its native
preview. No new published host port is needed. See the upstream
[web preview guide](https://docs.openwebui.com/features/open-terminal/use-cases/web-development/).
Servers stop on container recreation; this is a development workspace rather than
a production hosting service. Never serve the whole home or source-file inbox.

Workspace acceptance on 2026-10-06: the authenticated user's home was scaffolded,
a second run created no additional files, and required base-image libraries/tools
were verified. Synthetic inputs produced readable DOCX/PPTX files, a resized PNG,
an H.264 video and a LibreOffice PDF export; an existing delivery directory was
protected from overwrite. The starter app served HTML and its health API through
the authenticated WebUI preview proxy on port 3000. Lenny's live prompt matches
the workspace instructions and all 39 preset tests pass. Browser rendering and
a model-driven end-to-end chat were not tested. No system packages, Compose
services or container images were changed for this workspace setup.

## Branded PowerPoint bridge

The managed PowerPoint generator can read raster images from the caller's
`~/workspace` and save generated Lenovo decks to `~/workspace/output`. Select the
terminal in the chat, browse its native file tools, then use `terminal_image_path`
in the slide specification. Image interpretation is not required; provide a
filename or description when the model cannot see the picture. See
[PowerPoint terminal transfer](POWERPOINT_TEMPLATE.md#open-terminal-images-and-delivery)
for schema, permissions, delivery behavior and reproducible tests.

Both managed office generators save selected-terminal deliverables to `output/`
and register caller-owned WebUI downloads.
For the shared delivery schema and partial-failure behavior, see the
[office delivery contract](POWERPOINT_TEMPLATE.md#open-terminal-images-and-delivery).
The Compose WebUI service mounts `backend/open_webui/main.py` read-only to preserve
the narrow authenticated chat-file compatibility redirect across recreation.
It accepts only canonical UUID file IDs under `/c/api/v1/files/<id>/content` and
redirects to the existing Files API, which retains its normal ownership checks.
Review this mount against the image's matching backend baseline during upgrades.

Plotly charts can now export a verified PNG to the selected user’s Terminal output and authenticated Files store while keeping the interactive chat view. See [VISUALS.md](VISUALS.md) for theme/font ownership, installation and tool arguments.
