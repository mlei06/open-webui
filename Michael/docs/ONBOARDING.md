# Internal-stack onboarding

Status: Project onboarded; deployment not implemented or tested.

## Objective

Coworkers should clone the fork, supply approved secrets and CA material, then build/run a project-specific Compose stack that provisions the same service versions, model presets, tools, and MCP connections. User accounts, chats, uploaded documents, credentials, and database state remain per deployment.

The desired command is `docker compose --env-file Michael/.env -f Michael/compose.yaml up --build -d`; that file does not exist yet. Do not confuse the upstream Compose file (which includes Ollama) with the proposed internal stack.

## Repository

Fork: https://github.com/mlei06/open-webui (public, already existed when onboarding began).

Local checkout: `/home/mlei4/projects/open-webui`.

Remotes: `origin` is the user's fork; `upstream` is Open WebUI. Initial inspected checkout: `8bd8b4fac`. An existing fork may lag upstream; select and pin a tested baseline deliberately rather than resetting the fork or blindly merging latest upstream.

Keep stack-specific changes under `Michael/`. Keep upstream source patches small and independently documented. No onboarding changes have been pushed automatically.

## Proposed components

| Component | Responsibility |
|---|---|
| Open WebUI | Chat, uploads, identity, access-controlled model presets and tools |
| Bootstrap/reconciler | Provision managed configuration through verified authenticated interfaces |
| Document translator MCP | Submit durable translation jobs, inspect progress, cancel, retrieve artifacts |
| Translator workers/storage | Process office formats while preserving output structure; isolate untrusted input |
| SearXNG | Optional search service with explicit engine/network policy |
| Open Terminal | Optional tightly sandboxed execution; verify integration contract and image source |
| Stdio MCP bridge | Supervise stdio servers and expose a supported network interface |

Local network MCP uses Streamable HTTP where possible. Remote MCP connections are runtime-configured and authenticated. Backend URLs use Compose service DNS rather than browser localhost. Do not expose internal services on host ports unnecessarily.

## Verified source observations

- `docker-compose.yaml` builds Open WebUI and includes Ollama by default; not the target stack.
- `backend/open_webui/config.py` reads OpenAI connection environment variables and JSON `TOOL_SERVER_CONNECTIONS`.
- Configuration includes `ENABLE_PERSISTENT_CONFIG`; verify DB/environment precedence before choosing bootstrap policy.
- `backend/open_webui/utils/mcp/client.py` imports the Streamable HTTP client.
- `backend/open_webui/routers/tools.py` recognizes MCP tool-server connections.
- `backend/open_webui/routers/models.py` exposes authenticated model create/import/export endpoints. Exact schemas and permission behavior need review before provisioning.
- `backend/open_webui/env.py` supports `AIOHTTP_CLIENT_SSL_CERT_FILE`. Other Python/client runtimes may require separate trust configuration.
- License contains branding restrictions and notice-retention obligations; preserve branding and obtain legal review before internal redistribution/custom branding.

These observations establish integration seams, not a working deployment.

## Declarative configuration

Proposed files:

```
Michael/
  docs/                      # internal design and operating documentation
  mcp/mcp.json               # project-owned MCP inventory, not auto-imported
  models/                    # managed model/agent preset definitions
  services/                  # local service build contexts
  compose.yaml
  .env.example               # placeholder-only; no real internal URLs/secrets
  manifests/                 # managed presets, connections, access policy
  prompts/                   # versioned specialized prompts
  tools/                     # reviewed executable integrations, if necessary
  bootstrap/                 # idempotent reconciliation implementation
  tests/                     # synthetic integration fixtures
  runtime/                   # ignored private CA/secrets; never tracked
```

Pin source revision and container images. Bootstrap uses stable managed IDs, waits for application readiness, authenticates securely, creates/updates only managed resources, and reports drift. Repeated startup must not create duplicates or overwrite chats/user presets. Document whether UI edits to managed resources are reconciled or retained.

Do not rely on exporting a personal database to reproduce configuration. Do not assume mounted tools/prompts are automatically imported. Avoid arbitrary database writes and application auth bypass for seeding.

## Model provider and TLS

Use the same private OpenAI-compatible provider and credential source as Pi, supplied locally outside Git. The private endpoint and CA path can be read from the user's existing configuration during runtime setup without publishing them.

Mount the approved corporate CA bundle read-only. Configure the Open WebUI HTTP client's CA file and verify trust for bootstrap, MCP clients, and translator workers separately. Where a runtime needs a combined public/corporate trust store, build it without dropping public roots. Never set global insecure TLS flags.

List models dynamically for discovery, but pin agent base-model IDs in managed manifests after capability tests. Native tool calling, streaming, reasoning-field handling, uploads, and context limits need end-to-end testing; successful `/models` is not enough.

Secrets should be injected from a private env file or supported secret mechanism. Ensure they are absent from tracked files, image layers, manifest exports, and logs. Do not assume every env option supports a `_FILE` variant without source verification.

## Document translator workflow

1. User selects Document Translator and uploads one or more supported files.
2. The agent requests target language and any necessary format preferences.
3. A trusted integration validates user ownership and maps uploads to scoped file references accessible to the translator service.
4. Submit one batch/job with explicit files and target language; return durable job IDs promptly.
5. Jobs progress independently of chat requests; tools expose status, cancellation, and per-file errors.
6. Completed artifacts appear as authorized downloadable files, with partial successes supported.

Open WebUI upload IDs are not automatically accessible inside an MCP container. Choose a deliberate file-transfer/storage contract. Prefer scoped authenticated transfer or a gateway to broad shared filesystem mounts. Never ask the model to invent host paths.

Specify supported formats, archive/size limits, retention, retry/idempotency, preservation of layout, and handling of macros/password-protected files. Use synthetic fixtures. Treat document text as untrusted input, not instructions granting tool permissions.

The initial 'agent' is a managed Open WebUI model preset with system prompt, base model, and scoped tools—not an assumed autonomous background-agent runtime.

## Milestones

1. Audit fork/upstream differences, license, local Docker capabilities, and current application schemas.
2. Build minimal Compose deployment from the fork with private provider/TLS and persistent app storage.
3. Implement/test authenticated managed-resource bootstrap on clean volumes and restarts.
4. Add translator MCP plus a secure upload/artifact contract and sample agent preset.
5. Add SearXNG, sandboxed terminal, and stdio bridges as optional profiles.
6. Test clean installation, restart, upgrade/rollback, access control, failure recovery, and coworker setup instructions.

## Acceptance criteria

- Fresh checkout + approved private configuration produces the same managed presets/connections without manual UI setup (initial admin identity bootstrap must be explicitly documented).
- Provider requests succeed with certificate verification enabled.
- Repeated startup is idempotent and preserves user state.
- Translator handles multiple files and produces authorized downloadable outputs, including partial failure reporting.
- Unauthorized users cannot access another user's files/jobs or unrestricted terminal tools.
- Required MCP services survive restarts; stdio supervision and HTTP health checks are verified.
- No secrets or real user data appear in repository/history, image layers, or logs.
- Versions are pinned and upgrade reconciliation is tested.

## Decisions to resolve

- Approved publication boundary: public upstream fork versus private company deployment repository.
- Admin/account provisioning and eventual company SSO.
- Exact translator MCP source, tool schema, supported formats, and job storage.
- Terminal service image/protocol and sandbox policy.
- Managed-resource reconciliation and backup policy.
- Coworker host support (Docker Desktop/WSL/Linux), resource requirements, and network/VPN access.
