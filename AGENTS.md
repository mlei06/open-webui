# Internal Open WebUI stack

## Project intent

Maintain a minimally divergent Open WebUI fork and a reproducible Docker Compose stack for coworkers. Prefer deployment/bootstrap integrations over modifying upstream application internals. Preserve upstream license notices and branding requirements.

- Fork: https://github.com/mlei06/open-webui
- Upstream: https://github.com/open-webui/open-webui
- Primary model provider: an internally hosted OpenAI-compatible endpoint (configuration supplied privately).
- Planned services: Open WebUI, SearXNG, Open Terminal, local MCP services, and bridges for stdio-only MCP services.
- Planned specialized model presets/agents: document translator first; prompts and tool permissions managed declaratively.

Read `Michael/docs/README.md` (the documentation index) and `Michael/docs/deployment.md` before implementation. All documentation lives in `Michael/docs/`, one file per Open WebUI feature (models, tools, mcp, functions, terminal, skills, knowledge, accounts, branding); update the matching file when behaviour changes.

## Security and reproducibility

This GitHub fork is PUBLIC. Never commit internal API keys, private endpoint/CA material, real office documents, private service URLs, tokens, databases, or user exports. Use ignored runtime environment/secrets and synthetic fixtures. Confirm company policy before publishing deployment-specific details.

Do not disable TLS verification. Mount private corporate CA material and configure each runtime's trust appropriately. Pin service versions/digests; do not depend on latest tags. Secrets and user state are not declarative configuration.

Do not mount the Docker socket or host filesystem into agents/terminal by default. Executable tools and terminal capabilities require explicit least-privilege access. Validate uploaded-file ownership before translator jobs; use scoped opaque file/job references, not model-supplied arbitrary paths or URLs.

## Integration approach

Open WebUI currently includes a Streamable HTTP MCP client. Stdio servers need a supervised bridge or adapter; do not assume a Compose service can expose stdio directly over the network.

Model presets, tools, and other DB-backed resources need an idempotent authenticated bootstrap/reconciliation path. Mounting prompt or tool files alone does not prove that Open WebUI loads them. Verify supported APIs/schema against the pinned checkout before implementation. Bootstrap must preserve user-owned content and avoid duplicate resources or destructive resets.

Separate package-level backend/frontend tests from Compose integration tests. Do not claim reproducibility until a clean-volume deployment passes provider TLS, preset provisioning, MCP discovery, file translation, restart, and upgrade checks.
