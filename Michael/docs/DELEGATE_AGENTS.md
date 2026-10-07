# Delegate Agents

`tools/delegate_subtask.py` (tool id `delegate_agents`) lets a parent preset hand work
to one or more other configured model presets. Each agent runs in its own internal chat
with its own system prompt, parameters, tools, MCP servers and terminal. With
`background=true` the parent keeps working and is resumed when the agents finish.

It is a reviewed modification of pfn0's MIT-licensed "Delegate (Sub-agent Model Picker)"
tool; the attribution stays in the source header. Open WebUI's native `delegate_task`
always runs the parent's own model with the parent's tools. This tool adds a different
preset per task, which the native one cannot do.

## Installation

- `extensions.json` manages the tool: `provision.py --only extensions` installs or updates it.
  It has a public read grant, because Open WebUI hides a workspace tool from anyone who is
  neither its owner nor granted read access. Delegation does not widen access: the target
  preset must already be available to the calling user.
- `models/presets.json` attaches it to the `lenny` preset only. Other presets do not get it.
- Valves are runtime state, not declarative; the bootstrap never writes them (see below).

## Tools

`list_agents()` returns `{id, name, description}` for the presets the user can access.
A preset is a model that wraps another model. Registered base models are hidden unless the
`include_base_models` valve is on.

`delegate_agents(tasks, execution='parallel', background=True)`:

| Argument | Meaning |
|---|---|
| `tasks` | List of `{agent_id, task, context?, file_ids?}`. One item runs a single agent. |
| `execution` | `parallel` runs all agents at once. `sequential` runs them in order, passes each agent's output to the next, stops at the first failure and marks the rest `skipped`. |
| `background` | `true` returns a dispatch handle at once; `false` waits and returns every result. |

`create_timer` is carried over unchanged from the source tool.

## Behaviour

- Each agent's tools, filters, terminal and default features come from its own preset, not
  from the parent. `code_interpreter` is never enabled for delegated agents (it needs a live
  browser session). The preset's own system prompt and parameters are applied by the chat handler.
- Only files already attached to the parent chat can be passed on, and only by `file_ids`.
- Delegated agents cannot delegate again, and the mutating memory tools are removed for them
  (both handled by Open WebUI's internal-request flag).
- Background jobs post one combined message into the parent chat once every agent has finished:
  `[ASYNC SUBAGENT COMPLETE - job_xxxx]`, marked as untrusted output data. The parent is then
  resumed on **its own model** with its own tools and system prompt. (The earlier wrapper around
  the native helper resumed the parent on the sub-agent's model.) If the parent is still
  answering, the message is queued and processed afterwards.
- Each agent runs in its own internal chat, linked from the combined result message.

## Valves

Set by an administrator on the tool; nothing here is committed.

| Valve | Default | Purpose |
|---|---|---|
| `allowed_agent_ids` | empty | Comma-separated preset ids that may be delegated to. Empty allows every preset the user can access. |
| `include_base_models` | false | Also list registered base models. |
| `max_agents_per_call` | 8 | Agents in one call. |
| `max_parallel` | 4 | Agents of one job running at once. |
| `max_active_jobs` | 5 | Background jobs running at once on this server. |
| `max_iterations` | 30 | Tool-call rounds per agent. |
| `agent_timeout_seconds` | 900 | Timeout per agent. |
| `max_result_chars` | 30000 | Result size per agent. |
| `max_task_chars` | 16000 | Task plus context size. |

Because delegated agents use their preset's own tools and terminal, set `allowed_agent_ids`
to the presets you actually want Lenny to call.

## Verification

Unit tests stub Open WebUI's internals and need only `fastapi` and `pydantic`:

    uv run --no-project --with fastapi --with pydantic python Michael/tests/test_delegate_subtask.py

A live run on a stack with a reachable model provider used temporary presets and a temporary
copy of the tool, since removed. A parallel job and a sequential job each completed, the parent
resumed on its own model, and the sequential second agent quoted the first agent's output.

Not covered live: a failing sequential step, timeouts and cancellation, the allowlist valve,
and delegation to presets that have their own tools or terminal.

## Limits and upgrade notes

- The tool imports three private helpers from `open_webui.utils.subagents`
  (`_build_request`, `_parent_locks`, `process_pending_internal_messages`). Re-run the tests and a
  live parallel job after changing the pinned Open WebUI version.
- Background jobs live in the server process. A restart abandons running jobs; results of agents
  that did not finish are not posted.
- The older `delegate_subtask_with_model` and `sub_agent` tools may still be installed on a
  stack. They are not managed here and do not conflict with this tool's ids.
