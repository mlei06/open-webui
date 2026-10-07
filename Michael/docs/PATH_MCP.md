# PATH MCP rollout

[MCP documentation index](MCP.md)

This declaration replaces the local review stack's hand-made PATH connection and preset. It is not evidence of a live rollout or model acceptance. No PATH backend is built or started by Michael Compose.

## Managed resources

- `mcp/mcp.json` registers Streamable HTTP connection **`path`**, public-read like the other enabled Michael connections. The nine declared and filtered tools are `path_search_records`, `path_get_record_status`, `path_get_record_details`, `path_get_record_history`, `path_get_records`, `path_count_records`, `path_get_activity`, `path_get_filter_values` and `path_lookup_employees`. They replace the old three-tool interface, not aliases.
- `models/presets.json` updates preset **`path`** in place to **PATH assistant**, using Nemotron 3 Ultra on Davy, native function calling and `prompts/path-assistant.md`. Only `server:mcp:path`, built-in time/user input and the user_context filter are attached; no mail drafts, web, files or knowledge bases. Name resolution uses PATH's own `path_lookup_employees`; no separate directory or team dependency.
- **Office Agent** and **Lenny** retain their existing tools/actions and now declaratively retain their hand-attached `server:mcp:path`. Other presets do not get PATH.
- `models/user-context.json` gives `path` signed-in name/id only. The filter derives the company itcode from the email local part, not the account UUID. The assistant validates it with PATH and passes `recipient_network_ids`, never `me`. This is an identity filter, not authorization. Names can instead use the explicitly labelled fuzzy `recipient_name` route across current recipient/receiver labels.

`bootstrap/mcp_servers.py` merges connections by `info.id`, adopting the hand-made `path` without duplicating it; unrelated connections and extra fields are retained. `bootstrap/presets.py` updates the existing model id `path`, preserves unmanaged fields and changes nothing on a second identical run. UI changes to managed fields are reconciled to the declaration. The existing provisioning order is MCP, user context, then presets; there is no extra provisioning script.

## Endpoint and security

`PATH_MCP_URL` is resolved by the registration script from runtime environment configuration. Its default (and `.env.example` value) is `http://host.docker.internal:18076/mcp/`, **only for this local review stack**, reachable from the Open WebUI container rather than the browser. Supply an approved internal endpoint for another deployment. Do not commit private URLs, CA material or credentials; never disable TLS verification for an HTTPS endpoint.

`PATH_MCP_API_KEY` is optional and empty by default: registration uses `auth_type=none` for the currently approved keyless company-network backend. If the operator configures a shared bearer key on PATH, configure the same key privately for registration. It is not an employee identity key. No forwarded-email header is required. Public-read Open WebUI visibility does not secure a reachable keyless backend: operators own the approved internal network boundary and model-access policy. PATH records must stay on the approved internal Davy model, not outside models.

## Operator rollout (not executed by offline tests)

After the nine-tool backend rollout, the operator reviews private endpoint/access configuration and backs up existing connection/preset settings privately. Run the existing authenticated bootstrap MCP registration, user-context and preset stages, then re-run/check them to establish no drift. Registration verifies all nine tool names; an old backend must fail verification rather than be described as ready. Do not use `--no-verify` as acceptance evidence. Verify actual model traces for signed-in “my packages”, ambiguous names, fuzzy receiver names, status/count selection, MAIL, admin-mark wording and America/New_York date boundaries. Check Office Agent/Lenny still have their other tools and PATH. Offline tests need no Docker or live provisioning.

## Rollback

Before registration, reverting this declaration has no runtime effect. After registration, removing the entry alone does **not** remove the existing connection (unmanaged entries are preserved unless explicitly pruned); dropping the preset declaration alone also does not delete the model. Under an approved operator change, restore the backed-up connection/preset/context configuration through the authenticated admin APIs, or disable PATH access and detach `server:mcp:path` from all three presets before restoring the old backend. Restore the matching old tool filter and prompt if restoring the three-tool interface. Do not run a broad prune or reset the database/volume. Reverting Git is not a runtime rollback; never claim old prompts work with the new schemas or vice versa.
