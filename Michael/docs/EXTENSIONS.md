# Managed visual and interface extensions

`extensions.json` declares the reviewed source and activation policy. The unified
`bootstrap/provision.py` runs `bootstrap/extensions.py` before preset provisioning.
Use `provision.py --only extensions --check` for a read-only comparison or omit
`--check` to reconcile. Unrelated tools/functions and their settings are preserved.

| ID | Type | Source | Policy |
|---|---|---|---|
| `visuals_toolkit_v4` | Workspace tool | `tools/visuals_toolkit_v4.py` | Private; no public grants or automatic preset attachment |
| `delegate_agents` | Workspace tool | `tools/delegate_subtask.py` | Public read grant; attached to the `lenny` preset by `presets.json`. See [DELEGATE_AGENTS.md](DELEGATE_AGENTS.md) |
| `interface_toggles` | Event function | `functions/interface_toggles.py` | Active, not a global filter |
| `collapsed_sidebar_pinned_models` | Event function | `functions/collapsed_sidebar_pinned_models.py` | Active, not a global filter |

These are reviewed exports of the installed versions. Their MIT license metadata,
author attribution and upstream links remain in the source headers. No runtime
valves, user exports, credentials or private endpoints are committed.

## Visuals Toolkit V4

Cole's version 4.0.0 exposes 18 rendering functions: charts, tables, comparison
tables, dashboards, flowcharts, funnels, Gantt charts, gauges, heatmaps, metric
grids, multi-charts, pies, radar, Sankey, timelines, trees, waterfalls and candlesticks.
Models pass structured JSON arguments; outputs are embeddable HTML with plain-text
fallbacks. `mode: auto` is the default. This is separate from the PowerPoint tool:
it renders chat visuals, not PPTX files. It does not need model vision.

The HTML loads Plotly 2.27.0 from `cdn.plot.ly`; clients need access to that CDN for
interactive charts. It is not a fully offline renderer. The bootstrap preserves
existing valves (there were no explicitly saved valves at import), with defaults
in the reviewed source. Attaching the tool to a preset or broadening its access is
a separate explicit configuration change; retaining it does not grant it to Lenny.

## Interface functions

Both @G30 version 1.0.0 functions use the shared static-asset registry to contribute
to `/static/loader.js`, coexisting with Theme Designer Pro. Interface Toggles adds
settings for switch styling, hotkey hints, timestamps, notifications, confirmation,
assistant bubbles, tool output and the Generate Message Pair shortcut. Collapsed
Sidebar Pinned Models shows the user's pinned models as sidebar icons; its API
requests use the current browser session. Browser preferences remain user-owned.

The installed source declares Open WebUI 0.11.0 or newer. After changes, reload
the browser to load updated assets. The bootstrap checks source and active/global
flags; that check alone is not a browser rendering test.

## Explicit retirements and exclusions

The manifest removes only these IDs when present:

- Tools: `openui`, `delegated_agent_runner`, `file_sending_tool`, `delegate_subtask_with_model`, `sub_agent`
  (the last two are superseded by `delegate_agents`).
- Function: `llmtrace` (including its global filter registration).

Before updating or removing an existing resource, the reconciler saves its source
and administrator valves in mode-0600 files under ignored `runtime/extension-backup/`.
Backups can contain secrets: never commit them. Deletion does not erase historical
chat output, user settings or any external tracing records. Old chats can still
mention removed tools.

`readable_generation_info` is intentionally left installed and unmanaged pending
work on internal-model support. Theme Designer Pro remains a separately supplied
private plugin handled by `branding.py`. Neither is implicitly copied or removed
by this reconciler. Model capabilities, including vision, are not changed here.

## Validation

`python3 Michael/tests/test_extensions.py` tests read-only checks, installation,
activation, explicit removals, preservation of unrelated resources, and repeat-run
idempotence. The application API loads the retained source; a separate read-back
checks that source, access policy, activation flags and removals match the manifest.
