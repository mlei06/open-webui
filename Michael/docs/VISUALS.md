# Plotly charts in chat, Files, and Terminal

`visuals_toolkit_v4.render_visualization` makes one bounded Plotly figure, renders it interactively inside the existing sandboxed chat iframe, exports a PNG through the selected registered Terminal, and registers the exact PNG in the invoking user's Files store. Its result context contains `status`, `download_url`, `workspace_path`, `terminal_saved`, hash, dimensions, theme, actual font, source metadata, and warnings. URLs start with `/api/v1/files/`; preserve the leading slash. A native image preview provides a fallback when interactive scripts are unavailable. No model-generated code, external rendering service, arbitrary layout, URL or destination path is accepted.

The active administrator-selected PowerPoint starter owns the palette, font and picture ratio. `get_slide_layouts` also returns that `visual_theme`. Charts use the starter's light background, dark foreground, six accents, and typography, without copying logos/artwork. Axis/heading/caption text targets 14/18/9 physical slide points, scaled to the picture width; dense horizontal bars are rejected when labels cannot remain legible. The installed template requests Arial; that licensed font is unavailable in this runtime. Export and browser use the installed, locally served Liberation Sans instead and return an explicit warning. The iframe scales the complete fixed-dimension chart to fit its viewport, preserving labels and margins. It does not claim pixel-identical display at different viewport sizes.

Default PNG fits within 1600 pixels on its longest side; width/height follow the selected picture placeholder (1244 × 1600 for the installed portrait `Title w/Image` placeholder). Optional `width`/`height` are bounded to 600–2400 / 400–2400 and 4 megapixels. `format: "jpeg"` is optional. Output names are unique, exclusive, owner-checked files under `~/workspace/output`; existing files cannot be overwritten. Supply an authorized Terminal through the chat's Terminal selector. Paths, hashes or download links are returned only when verified. `partial_success` can mean a real Terminal output with no Files link; keep that asset and disclose the failed destination. Never substitute shared cache paths.

Example tool arguments (synthetic values):

```json
{
  "specification": {
    "kind": "bar",
    "title": "Cases by customer",
    "unit": "cases",
    "categories": ["Customer A", "Other", "Unknown"],
    "series": [{"name": "Cases", "values": [14, 6, 2]}],
    "metadata": {
      "source": "QDTS aggregates, same requested filters",
      "date_basis": "case open date",
      "period": "2026 Q1 partial",
      "warnings": ["Employee participation overlaps; totals are not additive"]
    }
  }
}
```

Use QDTS aggregate responses, not search-page counts. Preserve the supplied order, Other/Unknown, source/date basis, coverage and partial periods. No historical dates, pre-2026 zeros, or missing heatmap values are invented. `bar` supports ranked horizontal (default), vertical, grouped and stacked series; `line` supports aligned weekly/monthly/yearly and multiple series, with null gaps. For period labels such as `2026-01`, use the default category axis; use `axis_type: "date"` only for real ISO date values. `heatmap` requires rectangular `values` matched to `row_labels`/`column_labels`; null stays missing. `timeline` requires valid dated `events`; `sequence` uses supplied event order with no time/duration claim. `gantt` requires positive real `start`/`end` intervals. Series/category/event limits reject overlarge inputs rather than fabricate/truncate totals. Full metadata remains in the result; keep displayed captions short enough to remain legible.

To reuse the PNG, pass the returned `workspace_path` as `terminal_image_path` in the existing PowerPoint `Title w/Image` slide. The default dimensions fit its actual picture ratio; avoid changing aspect ratio when reusing the chart. The PowerPoint bridge reads the verified invoking-user asset.

## Installation and rollout

The existing Terminal image is extended by `Michael/terminal/Dockerfile`, pinned to Plotly 7.0.0, Kaleido 1.2.0, Choreographer 1.4.0, Debian Chromium 154.0.8037.92-1~deb13u1 and fonts-liberation 1:2.1.5-3. It keeps the same service, nonroot user, multiuser identity configuration, `/home` volume and resource limits. Plotly 7's unused default request-header setting is disabled for compatibility with Kaleido 1.2; the renderer loads packaged local assets only. No new service is added. Package availability changes require an intentional pin refresh and export check; do not silently switch to a remote renderer.

From `Michael/`:

```sh
mkdir -p runtime/plotly
docker compose --env-file .env -f docker-compose.yaml build open-terminal
docker compose --env-file .env -f docker-compose.yaml up -d --no-deps open-terminal
python3 bootstrap/plotly_assets.py
docker compose --env-file .env -f docker-compose.yaml up -d --no-deps open-webui
```

`plotly_assets.py` copies the installed renderer, regular/bold Liberation Sans and font license into generated `runtime/plotly`. Compose mounts only that directory read-only at WebUI's `/static/plotly`. Browser and export use the same packaged renderer. On a clean installation these steps publish assets before use; repeat after changing the renderer pin. Bootstrap's extension/office bundler embeds the shared delivery and figure owners in each deployed tool, so DB-backed tools have no unresolved sibling imports. Use the managed extension owner to register the toolkit, and the existing model/preset owner to attach it to Lenny and Case Assistant. Existing access grants remain authoritative; attachment does not make the private toolkit globally readable. Broad preset provisioning can overwrite operator settings, so an existing installation should update only the named tool source and intended model fields.

Synthetic acceptance tests: `python3 -m unittest discover -s Michael/tests -p test_visual_figures.py`; office parity tests run in the existing WebUI Python environment. Live delivery should additionally check owner/unauthenticated download guards, PNG/Terminal hash equality, fallback warnings, and actual PPTX media reuse. No external model call is required.
