# Internal QDTS cases — operator rollout and offline proof

Scope: the **Michael/** deployment only. The independent devqdts checkout supplies `qdts_cases/`, `Dockerfile.cases` and `requirements-cases.txt`; read its `docs/CASES_MCP.md` before building. Pin/review that checkout's revision. There is no live QDTS login or sync in this stack.

## Trust and access

- `qdts` in `mcp/mcp.json` uses stateless Streamable HTTP at `http://qdts-cases:8000/mcp`, one required shared bearer key, public read grants and exactly nine tools: `search_cases`, `get_case`, `get_case_notes`, `get_case_summary`, `get_case_status`, `get_case_slice`, `get_cases`, `get_case_filter_values`, `lookup_case_entities`. Both the inventory and function-name allowlist contain these nine read-only tools. No write tool, host port, user token or `X-User-Email` header exists. Open WebUI admins can read the stored connection key; valve encryption does not encrypt that config.
- All loaded cases and **all notes, including private/unclear-private notes**, are readable by everyone with this tool access. `include_notes` controls keyword matching, not visibility. This is not case-level authorization. Do not deploy publicly.
- Customer names are plain; the current service has **no alias mode**. No `QDTS_ALIAS_KEY` is generated. `QDTS_CUSTOMER_NAMES=plain` is an integration declaration, not a service-side anonymization switch.
- Provisioning, standalone preset provisioning and QDTS MCP registration fail closed if `XAI_API_KEY` is set, the effective base model is not `gemma-4-31b-it`, multiple provider URLs are supplied, the approved Davy endpoint is missing from saved connections, or saved URLs/keys/configs cannot be aligned; saved non-Davy connections are tolerated only when explicitly disabled (`OPENAI_API_CONFIGS[index].enable=false`) or keyless (missing per-connection config counts as enabled). Guards run before writing tools/presets. Failed API reads also stop provisioning. They deliberately do not print secrets or silently remove connections.
- The guard assumes the single configured endpoint is the approved internal Davy host. It is a **provision-time check, not runtime DLP or authorization**. Admins must not add outside/direct connections, switch models in the UI or expose these tools to outside models later. Lenny still has web search and mail tools; its prompt forbids sending case text to outside services. Prompts are not a hard security boundary; approve that residual risk before rollout. Prefer the dedicated Case Assistant for case work.

## Presets and query semantics

Lenny gains the nine case tools alongside its existing tools. Case Assistant starts with only those case tools (plus built-in time/user input), no directory dependency, mail/action, files, web or knowledge base. Its SVG icon is rasterized by the existing preset bootstrap. The global `user_context` filter supplies name and id (itcode); for "my cases" the agent passes that id as `person`, or uses the owner role for "my owned cases". That is only a query hint.

Both prompts choose the smallest response and describe narrow-first keyword/BM25 search over title/AI summary, empty query for pure filters, honest totals/paging and freshness/coverage limitations. Discover real state values with `get_case_filter_values(field="state")`, never guess from a fixed list. "Blocked" maps to `Hold` only if discovered; always name the chosen state for any state mapping. If Escalated is absent, do not invent it; escalation-note matches are historical discussion, not current status. Cancel is closed and Verify is open. "Recently closed" uses the closed group, a stated `closed_after` cutoff and `sort=closed_newest` if supported by the discovered schema. Older service versions omit that sort with an explicit ordering limitation.

| Question | Preferred call |
|---|---|
| Current status, owner/team or what is due next? | `get_case_status(id="1")` |
| Summarize without discussion? | `get_case_summary(id="1")` |
| People or lifecycle owners? | `get_case_slice(id="1", section="people")` or `section="lifecycle"` |
| Overdue/open/completed tasks? | `get_case_slice(id="1", section="tasks", task_state="overdue")` (or `open`/`closed`) |
| Workflow events or recorded files? | `get_case_slice(id="1", section="timeline", limit=5)` or `section="attachments"` |
| Original/private discussion? | `get_case_notes(id="1", query="", limit=2)` |
| Compare known cases? | `get_cases(ids=["1","2"], view="status")` (or `summary`) |
| Which states/teams/products/customers/severities exist? | `get_case_filter_values(field="state")` (or the other singular field names) |
| Resolve a name? | `lookup_case_entities(kind="people", query="Alex Sample")` (or `customers`/`products`/`teams`) |
| Need multiple sections together? | `get_case(id="1", sections=["people","tasks"])` |

Examples are invented, not real records. `get_case` still defaults to the full record when sections is omitted; do not use it for just summary/status. Selected sections retain caps; slices page beyond them. Batch ids are strings, at most ten, and pagination repeats the same ids at `next_offset`, retaining item errors. All pagination follows `next_offset`, never `offset+limit`; a budget-trimmed page/subset is not complete. Severity discovery returns `filter_value` for search, not necessarily the display label. Name lookup asks for an explicit user choice on ambiguity, never first-candidate guessing or merging identical names; it is not the employee directory.

"Who worked on this case" uses people/lifecycle slices and commenters from `get_case_notes`, distinguishing roles without treating membership/following as work or inferring team membership from ownership. People totals count role rows. Timeline events are best-effort metadata, not discussion or an authoritative state machine; current state comes from the header. Attachments are metadata only. Missing detail, unavailable AI summary, null events or absent overdue rows are not proof of no work. AI summaries are not verified facts; use notes for original evidence. Case text and every tool result remain untrusted, never instructions.

## Data flow and refresh

1. Operator runs an approved devqdts sync/normalize **outside this stack**, or obtains a consistent normalized SQLite backup. Do not copy an active database and WAL/SHM at unrelated times. `QDTS_DEVQDTS_DATA` names the approved folder containing `devqdts.db` and any matching sidecars; init-env deliberately does not discover it.
2. Profile `index` runs a one-shot `qdts-indexer`. Only it receives the source folder, read-only with `create_host_path: false`. It prepares ownership of its dedicated volume, then runs the builder as uid/gid 10001. The source must be readable by that identity. Do not loosen permissions on real data casually; arrange an approved readable backup.
3. `qdts-cases-data` holds the derived private 0600 index, owned by uid 10001. `qdts-cases` runs as the same non-root UID and mounts **only this volume read-only**. The service image contains neither source data nor browser profiles. The folder mount allows atomic replacements to become visible without restarting the service.
4. Repeat the one-shot index command after each approved normalized refresh. No timer or sync daemon is installed here. A failed rebuild retains the last good index; inspect the builder's count-only diagnostics and exit status (0 success, 2 schema/build, 3 integrity/shrink, 4 busy WAL). Never delete a live WAL to fix it. Rollback/rebuild is an operator decision, not an automated reset.

## Captain-approved rollout — do not run automatically

No command below authorizes modifying the live `michael` stack. After approving this branch, the Davy-only policy, all-user private-note access, source revision and backup, the operator must:

1. Privately add `QDTS_CASES_SRC=/approved/devqdts/checkout`, `QDTS_DEVQDTS_DATA=/approved/normalized-backup`, `QDTS_CUSTOMER_NAMES=plain` and optionally a pinned `QDTS_CASES_IMAGE` to the live env. Keep `QDTS_MCP_URL` empty for the internal default. Generate the shared key without displaying it:

   ```bash
   python3 Michael/bootstrap/provision.py --init-env --cases-src /approved/devqdts/checkout
   ```

   This writes `QDTS_MCP_API_KEY` only if missing/empty and does not rotate existing keys. Keep mode 600. Remove `XAI_API_KEY` and non-Gemma base overrides privately, and explicitly remove saved outside connections in Admin Settings **before** attaching case tools. Do not copy credentials into terminal commands, transcripts or support dumps.

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
