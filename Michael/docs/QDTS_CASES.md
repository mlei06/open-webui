# QDTS case MCP — search, operations, and validation

[MCP documentation index](MCP.md) · [Connection provisioning](ExternalToolServers.md)

Scope: the **Michael/** deployment only. The independent devqdts checkout supplies `qdts_cases/`, `Dockerfile.cases` and `requirements-cases.txt`; read its `docs/CASES_MCP.md` before building. Pin/review that checkout's revision. There is no live QDTS login or sync in this stack.

## Trust and access

- `qdts` in `mcp/mcp.json` uses stateless Streamable HTTP at `http://qdts-cases:8000/mcp`, one required shared bearer key, public read grants and thirteen read-only tools: `search_cases`, `get_case`, `get_case_notes`, `get_case_summary`, `get_case_status`, `get_case_slice`, `get_cases`, `get_case_filter_values`, `lookup_case_entities`. The inventory and allowlist also include aggregate_cases and the three specialized entity lookups. No write tool, host port, user token or `X-User-Email` header exists. Open WebUI admins can read the stored connection key; valve encryption does not encrypt that config.
- All loaded cases and **all notes, including private/unclear-private notes**, are readable by everyone with this tool access. `include_notes` controls keyword matching, not visibility. This is not case-level authorization. Do not deploy publicly.
- Customer names are plain; the current service has **no alias mode**. No `QDTS_ALIAS_KEY` is generated. `QDTS_CUSTOMER_NAMES=plain` is an integration declaration, not a service-side anonymization switch.
- Provisioning, standalone preset provisioning and QDTS MCP registration fail closed if `XAI_API_KEY` is set, the effective base model is not `nemotron-3-ultra`, multiple provider URLs are supplied, the approved Davy endpoint is missing from saved connections, or saved URLs/keys/configs cannot be aligned; saved non-Davy connections are tolerated only when explicitly disabled (`OPENAI_API_CONFIGS[index].enable=false`) or keyless (missing per-connection config counts as enabled). Guards run before writing tools/presets. Failed API reads also stop provisioning. They deliberately do not print secrets or silently remove connections.
- The guard assumes the single configured endpoint is the approved internal Davy host. It is a **provision-time check, not runtime DLP or authorization**. Admins must not add outside/direct connections, switch models in the UI or expose these tools to outside models later. Lenny still has web search and mail tools; its prompt forbids sending case text to outside services. Prompts are not a hard security boundary; approve that residual risk before rollout. Prefer the dedicated Case Assistant for case work.

The owner-selected preset base is now **Nemotron 3 Ultra** (`nemotron-3-ultra`) on the approved internal provider. This replaces the previous Gemma-only model choice; provider isolation and shared-data access restrictions remain in force. See [model migration](SYSTEM_PROMPTS.md#migrating-the-base-model-without-resetting-live-settings).

## Presets and query semantics

Lenny gains the thirteen case tools alongside its existing tools. Case Assistant starts with only those case tools (plus built-in time/user input), no directory dependency, mail/action, files, web or knowledge base. Its SVG icon is rasterized by the existing preset bootstrap. The global `user_context` filter supplies name and id (itcode); for "my cases" the agent passes that id as `person`, or uses the owner role for "my owned cases". That is only a query hint.

Both prompts use a single literal title+summary search and exact whole-set aggregates. There are no exact/related modes or query_scope. Search defaults to newest10; limit0 returns only the exact count. Sorting never changes membership. Use focused case reads and original notes for evidence.

The new tools are `aggregate_cases`, `search_product`, `search_team`, `search_customer`; `lookup_case_entities` is model-facing people lookup. Entity lookups return real IDs, labels, bounded counts and index revision. Pass IDs in `product_ids`, `team_ids`, `customer_ids`; each list is OR and separate fields are AND. Series selectors require `product_descendants=true`. Detail familyId joins the source productCatalogId, never the unrelated getFamilies directory. IDs missing from basic snapshots are not guessed from labels; customer duplicate names stay distinct.

`employee_ids` accepts exact QDTS user_id/unique itcode. `employee_relation=current_owner` differs from `recorded_participant`: deduplicated owner/originator/prior/lifecycle/human-comment/task-creator evidence, excluding followers/membership and identified system accounts. Missing attribution is not no work. Employee trends use case dates, not dates of participation.

`aggregate_cases` uses the same flat filters/query as search, one or two group_by dimensions and bounded top_n with Other/Unknown. Supported dimensions: team,employee,product,product_family,customer,state,created_week/month/year,closed_week/month/year,open_age_band. Employee/family groups can overlap; counts are distinct per group and totals count all matching cases. Zero buckets are filled only within observed indexed coverage for a single time dimension; boundary periods are marked partial. Multi-dimension groups report observed combinations. No pre-2026 zero history or inferred SLA/failure-rate metrics.

Legacy *_after/*_before remain inclusive dates; new *_from/*_until are inclusive/exclusive UTC bounds. Preserve index_revision between pages or lookup/search. Removed mode/query_scope calls are explicitly rejected. Product/customer/team string filters remain legacy discovery compatibility; new selected IDs are exact. Existing source summary/state/closure/changed-date limitations travel with responses.

Examples:

```json
{"group_by":["team"],"state":"open","top_n":10}
{"group_by":["product"],"created_from":"2026-09-01","created_until":"2026-10-01","top_n":10}
{"group_by":["created_week"],"top_n":100}
{"employee_ids":["<lookup-user-id>"],"employee_relation":"recorded_participant","product_ids":["<lookup-product-id>"],"limit":10}
{"employee_ids":["<lookup-user-id>"],"employee_relation":"recorded_participant","query":"display","limit":10}
```

Protected REST POST `/api/search_cases` and `/api/aggregate_cases` on the internal QDTS service use the same typed flat arguments; both require the shared bearer key. They are not unauthenticated routes on the replay frontend. Never expose the internal service publicly.

## Data flow and refresh

1. Operator runs an approved devqdts sync/normalize **outside this stack**, or obtains a consistent normalized SQLite backup. Do not copy an active database and WAL/SHM at unrelated times. `QDTS_DEVQDTS_DATA` names the approved folder containing `devqdts.db` and any matching sidecars; init-env deliberately does not discover it.
2. Profile `index` runs a one-shot `qdts-indexer`. Only it receives the source folder, read-only with `create_host_path: false`. It prepares ownership of its dedicated volume, then runs the builder as uid/gid 10001. The source must be readable by that identity. Do not loosen permissions on real data casually; arrange an approved readable backup.
3. `qdts-cases-data` holds the derived private 0600 index, owned by uid 10001. `qdts-cases` runs as the same non-root UID and mounts **only this volume read-only**. The service image contains neither source data nor browser profiles. The folder mount allows atomic replacements to become visible without restarting the service.
4. Repeat the one-shot index command after each approved normalized refresh. No timer or sync daemon is installed here. A failed rebuild retains the last good index; inspect the builder's count-only diagnostics and exit status (0 success, 2 schema/build, 3 integrity/shrink, 4 busy WAL). Never delete a live WAL to fix it. Rollback/rebuild is an operator decision, not an automated reset.

## Updating the running local service

Use the existing `michael` Compose project and its configured environment. From
the Open WebUI checkout, after testing the devqdts changes:

```sh
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml build qdts-cases
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml --profile index run --rm --no-deps qdts-indexer
docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-deps qdts-cases
```

Retain the old image ID/tag for rollback before rebuilding. Recreate only
`qdts-cases`; do not recreate Open WebUI or the source replica for this update.
This update uses index schema 2: back up the existing index, rebuild with the indexer BEFORE starting the new service, and retain the schema1 image/index pair for rollback. The
existing case-index volume remains mounted read-only. Roll back by selecting the
retained image through `QDTS_CASES_IMAGE` and recreating only this service.

Verify health, authenticated MCP tools/list (no mode/query_scope; thirteen tools), and matching search/count/aggregate queries. Use Open WebUI's authenticated
connection verification endpoint to prove the running app sees the new schema.
No credentials or raw case bodies belong in verification logs.

## Updating preset prompts

Managed source prompts are [Lenny](../prompts/lenny.md) and
[Case Assistant](../prompts/case-assistant.md). They instruct literal query membership, exact whole-set counts, ID lookup and explicit employee participation. Editing these files alone does not update running
presets. Use the existing authenticated prompt-refresh helper with only those
preset definitions, preserving capabilities, base model, tools and access grants.
Back up affected model records privately under ignored `runtime/` first, verify
saved prompt text, and refresh the model list. See [prompt operations](SYSTEM_PROMPTS.md).
Reload Open WebUI and start a new chat after the update; historical messages are
not rewritten. Full preset reconciliation can change unrelated live settings and
is unnecessary for a prompt-only update.

## Initial integration rollout — operator approval required

This section covers first-time installation, not a routine code/prompt refresh. No command below authorizes modifying the live `michael` stack. After approving the integration, the Davy-only policy, all-user private-note access, source revision and backup, the operator must:

1. Privately add `QDTS_CASES_SRC=/approved/devqdts/checkout`, `QDTS_DEVQDTS_DATA=/approved/normalized-backup`, `QDTS_CUSTOMER_NAMES=plain` and optionally a pinned `QDTS_CASES_IMAGE` to the live env. Keep `QDTS_MCP_URL` empty for the internal default. Generate the shared key without displaying it:

   ```bash
   python3 Michael/bootstrap/provision.py --init-env --cases-src /approved/devqdts/checkout
   ```

   This writes `QDTS_MCP_API_KEY` only if missing/empty and does not rotate existing keys. Keep mode 600. Remove `XAI_API_KEY` and non-Nemotron base overrides privately, and explicitly remove saved outside connections in Admin Settings **before** attaching case tools. Do not copy credentials into terminal commands, transcripts or support dumps.

2. Build and initialize only the new services:

   ```bash
   docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml build qdts-cases
   docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml --profile index run --rm --no-deps qdts-indexer
   docker compose -p michael --env-file Michael/.env -f Michael/docker-compose.yaml up -d --no-build --no-deps qdts-cases
   ```

3. Verify `/certs/ca-bundle.pem` is visible **inside** Open WebUI, then reconcile the new connection, user-context valves and presets without touching existing mail/directory/workspace tools:

   ```bash
   docker exec michael-open-webui-1 test -r /certs/ca-bundle.pem
   python3 Michael/bootstrap/provision.py --only mcp,filter,presets
   python3 Michael/bootstrap/provision.py --only mcp,filter,presets --check
   ```

   Check access as an ordinary user and verify no outside connection exists. The bootstrap reads the key from the host env file; the existing WebUI does not need a container recreation just for this connection. **Expected downtime: none** for the targeted path above. Do not run an unqualified `up --build` as part of this change: that may recreate WebUI/mail/directory and cause a restart window/sign-outs. If a WebUI recreation is separately approved, preserve its existing volume and stable `WEBUI_SECRET_KEY`, expect startup downtime and verify its cert bind mount afterwards.

4. Roll back by removing `qdts` from the saved tool connections and Lenny's tool list, disabling/removing Case Assistant, and restoring the prior user-context valves (or reprovisioning the prior reviewed revision). Stop only `qdts-cases` if approved; retain its volume until the retention decision. Never run `down -v` on the live project or remove `open-webui_open-webui`. Case key rotation changes both service env and saved connection: recreate only the case service, then reprovision MCP; calls fail during that brief interval.

## Validation

Offline contracts (from the repository root):

```bash
AUDIT_LOG_SOURCE="$PWD/Michael/functions/audit_log.py" \
USER_CONTEXT_SOURCE="$PWD/Michael/functions/user_context.py" \
uv run --no-project --with python-pptx --with python-docx --with pillow \
  --with pydantic --with httpx --with markdown-it-py --with mdit-py-plugins \
  --with pyyaml --with lxml \
  python -m unittest discover -s Michael/tests -p 'test_*.py'
```

Inventory, preset and integration tests assert all nine allowed tools, focused routing, state discovery, identity and safety rules. This suite needs no Docker or live stack. The source variables keep function tests on tracked files rather than their legacy `/tmp` defaults; dependencies enable the optional office/mail tests. Private-brand asset checks can skip when their untracked logo is absent.

Nine-tool integration validation (2026-10-05): the complete offline Michael suite ran 188 tests successfully (two private-brand asset checks skipped); Python compileall and `git diff --check` passed. The synthetic fixture now routes focused status/summary, slices, batches, filter discovery and name lookup, and verifies Hold discovery before blocked-case search. Docker/native-tool-loop proof was updated for nine tools but **not run** for this change; no live stack, private env or real case data was accessed. The Davy-only provisioning guard is unchanged. The synthetic source builder `tests/fixtures/cases_source.py` creates five wholly invented cases; it imports the column contract from the read-only devqdts package and reads **no real database**. Do not use a builder that pseudonymizes real extracts. The test provider is deterministic and local; it is a wiring test, not a claim about Davy's reasoning quality.

`tests/cases_e2e.py --cases-src /approved/devqdts/checkout` builds from that checkout and uses an explicit fresh compose project, port and WebUI volume. It checks the live WebUI identity before/after, mounts a system-only CA bundle and verifies it inside its own container, builds the invented index using the compose indexer, tests authentication and tool discovery/calls, provisions twice, checks ordinary-user visibility and exercises native tool loops through both presets. It never reads `Michael/.env`, real case data or private CA/key files. Its own env/volumes/cert override are disposable. It tears down only its own stack and volume. Read the script before running; Docker access is required.

Historical validation for the original three-tool integration (2026-10-05): 115 unit checks passed across case integration, presets, MCP servers, provision, icons, knowledge bases and branding contrast; one branding asset-dependent check skipped. The Docker proof passed image build from the primary devqdts checkout, indexer UID/permissions, in-container cert visibility, missing/wrong-key refusal, all three tools and private-note search, twice-applied provisioning plus drift checks, ordinary-user visibility and ten native tool loops (five scenarios for each of Lenny/Case Assistant), saved outside-connection refusal, own-volume cleanup, and unchanged live WebUI container identity on port 3000. The tested service exposed `closed_newest`; fixture/provider code tolerates the earlier schema without it. This does not validate Davy model quality, production TLS/provider reachability or a real-data refresh; those remain operator checks.
