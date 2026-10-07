---
name: Visualization
description: How to draw charts, tables, dashboards and diagrams with the visuals toolkit and turn any of them into a PNG for a slide or download. Load it when the user asks for a chart, graph, table view, diagram, or an image of one.
---

# Visualization: make it, then render it

Two routes. Pick by where the numbers come from. `render_visualization` draws only `bar`, `line`, `heatmap`, `timeline`, `sequence` and `gantt` (a `pie`, `donut`, gauge, table, funnel, radar, sankey or waterfall is NOT one of them): for those, and for anything not from QDTS, use the matching `render_*` tool and then `export_visual`.

| Route | When | Calls |
|---|---|---|
| **Evidence chart** | The numbers come from QDTS (`aggregate_records`) | `render_visualization(specification)`: one call shows the chart in chat AND saves the PNG |
| **Any other visual** | A table, pie, gauge, timeline, flowchart, dashboard, anything with data you have | `render_<kind>(...)` shows it in chat and returns a `visual_id`; then `export_visual(visual_id)` when an image is needed |

Never rebuild a visual by hand or paste its content into `export_visual`: it accepts only the `visual_id`.

## Evidence charts: `render_visualization`

1. Get the numbers with `aggregate_records` (see the **qdts** skill). The result includes a `chart` object. Never chart search-page totals.
2. Call `render_visualization(specification)` once. Use the `chart` object as the specification, adding what the chart needs:
   - `kind`: `bar`, `line`, `heatmap`, `timeline`, `sequence` or `gantt`; `title`; `unit`.
   - Bars and lines: ordered `categories` and aligned `series: [{name, values}]`, `orientation` (`horizontal` default, or `vertical`), `bar_mode` (`group` or `stack`), `axis_type` (`category` for period labels such as `2026-01`; `date` only for real ISO dates).
   - Heatmaps: `row_labels`, `column_labels` and a rectangular `values` grid (null stays missing). Timelines: `events: [{label, date}]` (a `sequence` omits dates). Gantt: `tasks: [{label, start, end}]`.
   - `metadata`: the exact source, date basis, period, coverage and warnings (Other, Unknown, partial periods, overlapping participation). Never imply pre-2026 zeros or employee work dates.
   - `show_title: false` when the chart goes on a slide that has its own title.
   - `width`/`height`, `picture_layout` and `format` are optional. An Open Terminal is **not** required: with none selected the image is rendered by a service and delivered as a download only.
3. The result gives `download_url`, `file_id`, and (with a terminal selected) `workspace_path`, `terminal_download_url` and warnings. Tell the user the one download link, disclose font or coverage warnings, and keep `workspace_path` for reuse (below).

For long category lists use a horizontal bar chart; for many series use `bar_mode: stack`. Keep to the supplied order and keep Other and Unknown.

## Every other visual: two steps

| Tool | Data you pass |
|---|---|
| `render_table(rows)` | list of row objects |
| `render_comparison_table(items, criteria, scores)` | items, criteria and a score map |
| `render_chart(x, y, chart_type)` / `render_multi_chart(series)` | one series / several `{name, x, y}`; `bar`, `line` or `scatter` |
| `render_pie_chart(labels, values, donut)` | labels and values |
| `render_heatmap(data, row_labels, col_labels)` | 2D numbers |
| `render_metrics_grid(metrics)` | `{name: value}` cards |
| `render_timeline(events)` / `render_gantt(tasks)` | dated events / `{task, start, end}` |
| `render_flowchart(steps)` / `render_tree(data)` | step list or edges / nested data |
| `render_gauge`, `render_radar`, `render_waterfall`, `render_funnel`, `render_sankey`, `render_candlestick` | their own numeric inputs |
| `render_dashboard(components)` | several of the above in one page |

Each shows the visual in chat and returns `{visual_id}`. Then, only when the user wants an image or it goes into a document, call `export_visual(visual_id, theme, width, save_to, format)`:
- `theme: "light"` for white slides and documents; leave the default only for an image that matches the dark chat view.
- `width` 600 to 2400 (default 1280); the height follows the content (very tall tables are refused: show fewer rows).
- With a terminal selected, output goes to `~/workspace/output` unless `save_to` names a folder or a full file path; nothing is overwritten. **Without a terminal** there is no workspace copy: the image is delivered as a download (`download_url`, `file_id`), and `save_to` is refused.

If the user only wants to see the chart, stop after the first step: do not export.

## Putting an image in a deck

Build a small deck yourself, or delegate a big one to the office-documents agent (see the **powerpoint** and **delegation** skills). Either way the image goes on a `Chart Slide` slide with `image_fit: "contain"`: set `terminal_image_path` to its `workspace_path` when a terminal was used, or `image_file_id` to the result's `file_id` when none was; when delegating, put that reference in the brief. Render with `show_title: false` (or the tool's own title off) when the slide carries the title. Use a native chart only for five series or fewer; anything denser should be the rendered image.

## Replying

One download URL per image (prefer `download_url`, else `terminal_download_url` when a terminal copy is verified), exactly as returned, with its leading slash. Say what the chart shows, the period and the source limits. Report a partial success honestly (for example the terminal copy exists but the download link failed).
