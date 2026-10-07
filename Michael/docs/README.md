# Michael: internal Open WebUI stack

Michael is a reproducible Docker Compose deployment of a lightly modified Open WebUI fork, plus the idempotent
bootstrap scripts that configure it. This directory is the only place for documentation. There is one file per
Open WebUI feature, so a question about tools, MCP servers, filters, the terminal, skills and so on has exactly one
place to look.

The repository is **public**. Never commit internal API keys, private endpoints, CA material, real documents,
service URLs, tokens, databases or user exports. See [deployment.md](deployment.md#principles-and-policy).

## Where to look

| Question about | File |
|---|---|
| Compose services, `.env`, provisioning order, restart, upgrade, TLS, tests, security policy | [deployment.md](deployment.md) |
| Model presets (Lenny, Case Assistant, ...), system prompts, base model, icons, web search | [models.md](models.md) |
| Workspace tools: PowerPoint and Word generators, visuals toolkit, translator tool, delegation, workspace files, managed extensions | [tools.md](tools.md) |
| MCP and OpenAPI tool servers: `mcp.json`, translator gateway, employee directory, mail, QDTS cases, PATH | [mcp.md](mcp.md) |
| Functions: filters (user context), actions (review and send email), event functions (audit log), interface functions | [functions.md](functions.md) |
| Open Terminal: setup, per-user homes, confinement, shared area, file delivery contract and download links | [terminal.md](terminal.md) |
| Skills: the task guides models load on demand | [skills.md](skills.md) |
| Knowledge bases (SOPs) and the Knowledge Base Manager tool (attached to no preset) | [knowledge.md](knowledge.md) |
| Accounts, roles, who can see what, access audit | [accounts.md](accounts.md) |
| Lenovo theme, logo, fonts, contrast checks, rollback | [branding.md](branding.md) |

## Repository layout

| Path | Purpose |
|---|---|
| `docs/` | All documentation (this directory) |
| `docker-compose.yaml`, `.env.example` | The stack and its placeholder-only configuration template (`.env` and `runtime/` are ignored) |
| `Dockerfile.qdts-backend` | Overlay that adds the MCP argument guard to the Open WebUI backend image |
| `bootstrap/` | Idempotent provisioning; `provision.py` is the one entry point |
| `mcp/` | `mcp.json` (declared tool servers) and `mcp.schema.json` |
| `models/` | `presets.json` (presets) and `user-context.json` (which account fields each model receives) |
| `prompts/` | One system prompt per preset (runtime content, not documentation) |
| `skills/` | One `SKILL.md` per skill (runtime content) |
| `tools/` | Reviewed workspace tool sources; `workspace_delivery.py` and `visual_figure.py` are shared libraries bundled into the tools |
| `functions/` | Filters, the action, event functions |
| `knowledge/` | `manifest.json` and the seed Markdown under `sops/` (runtime content) |
| `branding/` | Theme tokens, CSS, preset icons, PowerPoint starter declaration |
| `terminal/` | Open Terminal image (`Dockerfile`), the confinement patch (`confine/`), the workspace template that is copied into user homes (`workspace/`) |
| `services/` | Local build contexts for sibling services (ignored by git) |
| `tests/` | Unit tests, throwaway-stack end-to-end checks, synthetic fixtures only |
| `runtime/` | Ignored certificates, secrets, backups, local data |

Markdown files outside `docs/` are product content, not documentation: the system prompts in `prompts/`, the skills
in `skills/`, the seed knowledge in `knowledge/sops/`, and the workspace guide in `terminal/workspace/` that is
installed into each user's terminal home.

## Quick start

From the repository root, with the private `Michael/.env` in place (copy `.env.example`):

```sh
python3 Michael/bootstrap/provision.py --init-env
docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build
python3 Michael/bootstrap/provision.py
```

`provision.py` runs every bootstrap script in dependency order, is idempotent, and ends with a read-only access
audit. `--check` changes nothing and exits 1 when anything differs. Details, options, variables and troubleshooting
are in [deployment.md](deployment.md).

## Conventions shared by every bootstrap script

- **Authenticated and idempotent.** They talk to Open WebUI's admin API with the credentials in `.env`, compare
  before writing, and change nothing on a second run. `--check` is read-only and exits 1 on drift.
- **Output.** `[PASS]`, `[FAIL]`, `[NOTE]` (an environment limit, not a fault) and `[DRIFT]` lines, then
  `RESULT: PASS` or `FAIL`. Secrets are never printed.
- **Only declared settings are managed.** Resources a user or admin created in the app (their own knowledge
  bases, tools, skills, extra connections, edited fields the script does not declare) are kept.
- **Database-backed configuration needs provisioning.** Mounting a prompt, tool or skill file proves nothing:
  Open WebUI loads presets, tools, functions, skills and connections from its database, so a change in the
  repository takes effect only after the matching bootstrap script has run.
- **Standard library only**, except where a script bundles a tool source.

## How the pieces fit

```
user ── Open WebUI ──┬─ presets (models.md) ── system prompt + skills (skills.md)
                     ├─ workspace tools (tools.md) ── shared delivery layer ── Open Terminal (terminal.md)
                     ├─ tool servers (mcp.md): translator gateway, employee directory, mail, QDTS cases, PATH
                     ├─ functions (functions.md): user context filter, review-and-send action, audit log
                     └─ knowledge bases (knowledge.md)
```

Lenny is the all-purpose preset. It queries QDTS, draws charts, drafts mail and reads PATH itself, has the
translation, PowerPoint, Word and web search tools, and can hand tool-heavy or background jobs to specialist
presets (`web-searcher`, `document-translator`, `office-documents`) through `delegate_agents`. Every file a tool
produces lands in the signed-in user's own terminal home and comes back with a download link.
