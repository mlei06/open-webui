# MCP integrations

Start here for MCP usage and operations in the Michael deployment. Integration
guides live beside this index in `Michael/docs/`; lowercase `Michael/mcp/` holds
machine-readable connection configuration, not a second documentation tree.

## Guides by task

| Need | Canonical guide |
|---|---|
| QDTS case tools, exact/related search, evidence, service updates and validation | [QDTS_CASES.md](QDTS_CASES.md) |
| PATH mailroom tools, identity, custody semantics, rollout and rollback | [PATH_MCP.md](PATH_MCP.md) |
| MCP/OpenAPI connection configuration, registration, employee directory and troubleshooting | [ExternalToolServers.md](ExternalToolServers.md) |
| Workspace tools and Document Translator integration | [Tool.md](Tool.md) |
| Native Open Terminal setup, access and operations (separate from MCP) | [OPEN_TERMINAL.md](OPEN_TERMINAL.md) |
| Managed role prompts and updating running presets | [SYSTEM_PROMPTS.md](SYSTEM_PROMPTS.md) |
| Deployment entry point and repository layout | [Michael README](../README.md) |

## Configuration and ownership

- [mcp/mcp.json](../mcp/mcp.json) declares connections and tool allowlists;
  [bootstrap/mcp_servers.py](../bootstrap/mcp_servers.py) reconciles them through
  Open WebUI's authenticated API.
- [prompts/](../prompts/) holds model instructions; changes must be applied to
  database-backed presets before the running application uses them.
- Service-specific behavior belongs in its existing integration guide. Keep this
  index short and link to the owner rather than duplicating operational steps.
- Source-service implementation details remain in that service's own repository;
  these guides own its Open WebUI integration.

Documentation is not automatically loaded into prompts or MCP registration.
Keep credentials, private exports and live case content in ignored runtime storage.
