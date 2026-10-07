"""Bounded Plotly figures for chat and raster export, with no query/count logic."""
import html
import json
import math
import textwrap
from datetime import datetime


def _script_json(value):
    return json.dumps(value, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def _visual_text(value, *, limit=180):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Chart text must be a bounded string')
    return html.escape(value)


def _visual_number(value, *, nullable=True, unit=None):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Chart values must be finite numbers or disclosed nulls')
    if unit == 'cases' and (value < 0 or int(value) != value):
        raise ValueError('Case counts must be nonnegative integers')
    return value


def _visual_date(value):
    if not isinstance(value, str):
        raise ValueError('Use real ISO dates; use sequence for undated events')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    except ValueError:
        raise ValueError('Use valid ISO dates') from None


# Categorical colours that stay distinguishable side by side. The starter's six accents cycle, so a
# chart with more than a few series would repeat colours; these are used instead once there are four or more.
_DISTINCT_COLORS = ['#E1251B', '#1F4E79', '#2E8B57', '#C17A00', '#6D73C9', '#BD5E91',
                    '#008C95', '#5B5B5B', '#8E6C8A', '#D4A017', '#3046AD', '#7A3E00']


def _colorway(theme, count):
    """Colours for `count` series: the template's own accents for up to three, a distinct set beyond."""
    if count <= 3:
        return list(theme['colors'])
    return list(_DISTINCT_COLORS)


def _is_dark(color):
    try:
        value = color.lstrip('#')
        red, green, blue = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return False
    return (0.299 * red + 0.587 * green + 0.114 * blue) < 110


def _visual_figure(spec, theme):
    """Create one allowlisted figure; callers cannot inject arbitrary Plotly objects."""
    if not isinstance(spec, dict) or len(json.dumps(spec, allow_nan=False)) > 64 * 1024:
        raise ValueError('Chart specification must be an object of at most 64 KiB')
    kind = spec.get('kind', 'bar')
    title = _visual_text(spec.get('title', 'Chart'))
    unit = _visual_text(spec.get('unit', 'cases'), limit=40)
    metadata = spec.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be an object')
    warnings = metadata.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > 10:
        raise ValueError('Use at most ten coverage warnings')
    caption_parts = []
    for key in ('source', 'scope', 'coverage', 'date_basis', 'as_of', 'data_as_of', 'period', 'participation_semantics'):
        if metadata.get(key) is not None:
            caption_parts.append(key.replace('_', ' ') + ': ' + _visual_text(str(metadata[key]), limit=240))
    caption_parts += [_visual_text(w, limit=240) for w in warnings]
    # Keep captions legible rather than silently clipping important coverage.
    if len(' '.join(caption_parts)) > 1100:
        raise ValueError('Coverage caption is too long; keep full source metadata separately')
    caption = '<br>'.join(caption_parts)
    slide = 'picture_layout' in spec or 'width' in spec or 'height' in spec or spec.get('show_title') is False
    width = spec.get('width', min(1600, round(1600 * theme['picture_ratio'])) if slide else 960)
    height = spec.get('height', round(width / theme['picture_ratio']) if slide else 640)
    if type(width) is not int or type(height) is not int or not 600 <= width <= 2400 or not 400 <= height <= 2400 or width * height > 4_000_000:
        raise ValueError('Use bounded slide-ready dimensions, 600–2400 by 400–2400 pixels, at most 4 MP')
    # Raster resolution must not make slide text smaller: size in physical points.
    pixels_per_point = width / theme.get('picture_width_points', width / 3)
    axis_font = max(24, round(14 * pixels_per_point))
    heading_font = max(32, round(18 * pixels_per_point))
    caption_font = max(16, round(9 * pixels_per_point))
    # Full provenance travels in adjacent details and image metadata. The footer keeps
    # a source/period reference and makes the accompanying disclosures explicit.
    compact = [part for part in caption_parts if part.startswith(('source:', 'period:'))]
    if caption_parts:
        compact.append('Scope, coverage and date basis in details' +
                       ('; %d warning(s) in details' % len(warnings) if warnings else ''))
    line_width = max(24, int((width - 96) / (caption_font * 0.62)))
    caption_lines = textwrap.wrap('  •  '.join(compact), line_width)
    caption = '<br>'.join(caption_lines)
    title_width = max(24, int((width - 120) / (heading_font * 0.6)))
    show_title = spec.get('show_title', True)
    if not isinstance(show_title, bool):
        raise ValueError('show_title must be true or false')
    title_lines = textwrap.wrap(title, title_width) if show_title else []
    if len(title_lines) > 2:
        raise ValueError('Use a shorter title for the selected chart width')
    title = '<br>'.join(title_lines)
    traces = []
    below_axis = round(axis_font * 3.6)  # tick labels and axis title have their own band
    layout = {'width': width, 'height': height, 'paper_bgcolor': theme['background'],
              'plot_bgcolor': theme['background'],
              'font': {'family': theme['font'], 'size': axis_font, 'color': theme['foreground']},
              'title': {'text': title, 'x': 0.02, 'xanchor': 'left', 'y': 0.98, 'yanchor': 'top', 'yref': 'container',
                        'font': {'size': heading_font, 'family': theme['font']}},
              'margin': {'l': 120, 'r': 90, 't': max(95, heading_font * (len(title_lines) + 1)) if show_title else 40, 'b': below_axis + round(caption_font * 1.4) * len(caption_lines) + 24},
              'colorway': _colorway(theme, 1), 'showlegend': False,
              'xaxis': {'automargin': False, 'zeroline': False},
              'yaxis': {'automargin': False, 'zeroline': False},
              'annotations': [{'text': caption, 'xref': 'paper', 'yref': 'paper', 'x': 0, 'y': 0, 'yshift': -below_axis,
                               'xanchor': 'left', 'yanchor': 'top', 'showarrow': False,
                               'align': 'left', 'font': {'size': caption_font}}] if caption else []}
    if layout['margin']['b'] > height * 0.45:
        raise ValueError('Coverage caption needs a taller chart')
    if kind in ('bar', 'line'):
        raw_categories = spec.get('categories', [])
        series = spec.get('series', [])
        if not isinstance(raw_categories, list) or not 1 <= len(raw_categories) <= 200:
            raise ValueError('Use 1–200 ordered categories')
        categories = [_visual_text(x) for x in raw_categories]
        if not isinstance(series, list) or not 1 <= len(series) <= 12:
            raise ValueError('Use 1–12 aligned series')
        orientation = spec.get('orientation', 'horizontal' if kind == 'bar' else 'vertical')
        if orientation not in ('horizontal', 'vertical'):
            raise ValueError('Unknown chart orientation')
        axis_type = spec.get('axis_type', 'category')
        if axis_type not in ('category', 'date'):
            raise ValueError('axis_type must be category or date')
        if axis_type == 'date':
            categories = [_visual_date(x) for x in raw_categories]
        colors = _colorway(theme, len(series))
        layout['colorway'] = colors
        bar_mode = spec.get('bar_mode', 'group')
        if bar_mode not in ('group', 'stack'):
            raise ValueError('bar_mode must be group or stack')
        label_values = len(series) * len(categories) <= 48
        # The tallest bar or stack, so only segments big enough to hold a label get one.
        grid_values = []
        for series_item in series:
            if not isinstance(series_item, dict):
                raise ValueError('Each series must be an object')
            row = series_item.get('values', [])
            if not isinstance(row, list) or len(row) != len(categories):
                raise ValueError('Every series must align with all categories')
            grid_values.append([_visual_number(v, unit=unit) or 0 for v in row])
        peak = max([sum(col) for col in zip(*grid_values)] if bar_mode == 'stack' else [v for row in grid_values for v in row] or [0]) or 1
        for i, series_item in enumerate(series):
            if not isinstance(series_item, dict):
                raise ValueError('Each series must be an object')
            values = series_item.get('values', [])
            if not isinstance(values, list) or len(values) != len(categories):
                raise ValueError('Every series must align with all categories')
            values = [_visual_number(v, unit=unit) for v in values]
            if unit == 'cases' and any(v is not None and (v < 0 or int(v) != v) for v in values):
                raise ValueError('Case counts must be nonnegative integers')
            trace = {'type': 'bar' if kind == 'bar' else 'scatter',
                     'name': _visual_text(series_item.get('name', 'Series')),
                     'marker': {'color': colors[i % len(colors)]},
                     'x': values if kind == 'bar' and orientation == 'horizontal' else categories,
                     'y': categories if kind == 'bar' and orientation == 'horizontal' else values}
            if kind == 'bar':
                trace['orientation'] = 'h' if orientation == 'horizontal' else 'v'
                if label_values:
                    # Value labels, blank for zero and missing so empty groups stay clean.
                    shown = (lambda v: v and abs(v) / peak >= 0.06) if bar_mode == 'stack' else (lambda v: v)
                    trace.update(text=[('%g' % v) if shown(v) else '' for v in values], cliponaxis=False, textangle=0,
                                 textfont={'size': max(14, round(axis_font * 0.7))},
                                 textposition='inside' if bar_mode == 'stack' else 'outside')
                    if bar_mode == 'stack':
                        trace['insidetextanchor'] = 'middle'
            else:
                trace.update(mode='lines+markers', connectgaps=False,
                             line={'color': colors[i % len(colors)], 'width': 4})
            traces.append(trace)
        layout['showlegend'] = len(series) > 1
        if len(series) > 1:
            legend_font = max(18, round(axis_font * 0.85))
            names = [str(item.get('name', 'Series')) for item in series]
            room = width - layout['margin']['l'] - layout['margin']['r']
            rows, used = 1, 0
            for name in names:
                needed = len(name) * legend_font * 0.58 + legend_font * 2.6
                if used and used + needed > room:
                    rows, used = rows + 1, 0
                used += needed
            if rows <= 2:
                # Few series: a horizontal legend above the plot.
                layout['legend'] = {'orientation': 'h', 'x': 0, 'xanchor': 'left', 'y': 1.0, 'yanchor': 'bottom',
                                    'font': {'size': legend_font}, 'traceorder': 'normal'}
                layout['margin']['t'] += round(rows * legend_font * 1.55) + 14
            else:
                # Many series: a legend down the side keeps the plot tall instead of burying it under legend rows.
                legend_width = max(len(name) for name in names) * legend_font * 0.58 + legend_font * 2.6
                if legend_width > width * 0.42 or len(names) * legend_font * 1.6 > height * 0.9:
                    raise ValueError('Too many or too long series names for this chart size; shorten them or use fewer series')
                layout['legend'] = {'orientation': 'v', 'x': 1.01, 'xanchor': 'left', 'y': 1.0, 'yanchor': 'top',
                                    'font': {'size': legend_font}, 'traceorder': 'normal'}
                layout['margin']['r'] += round(legend_width)
        if kind == 'bar':
            layout['bargap'] = 0.18
            layout['bargroupgap'] = 0.04
            layout['uniformtext'] = {'minsize': 11, 'mode': 'hide'}
            if orientation == 'horizontal' and len(categories) * axis_font * 1.5 > height - layout['margin']['t'] - layout['margin']['b']:
                raise ValueError('Too many categories to remain legible in this slide picture; use a supplied aggregate top-N/Other or larger compatible dimensions')
            layout['barmode'] = bar_mode
            count_axis = 'xaxis' if orientation == 'horizontal' else 'yaxis'
            category_axis = 'yaxis' if orientation == 'horizontal' else 'xaxis'
            grid = '#4E444E' if _is_dark(theme['background']) else '#E6E2E4'
            layout[count_axis].update(title={'text': unit}, rangemode='tozero', gridcolor=grid)
            layout[category_axis].update(type='category', categoryorder='array', categoryarray=categories, showgrid=False)
            if orientation == 'vertical' and len(categories) <= 12:
                layout[category_axis]['tickangle'] = 0
            if orientation == 'horizontal':
                layout[category_axis]['autorange'] = 'reversed'
        else:
            layout['xaxis'].update(type=axis_type)
            layout['yaxis']['title'] = {'text': unit}
    elif kind == 'heatmap':
        rows = spec.get('row_labels', []); cols = spec.get('column_labels', []); matrix = spec.get('values', [])
        if not isinstance(rows, list) or not isinstance(cols, list) or not 1 <= len(rows) <= 30 or not 1 <= len(cols) <= 30:
            raise ValueError('Use 1–30 heatmap rows and columns')
        if not isinstance(matrix, list) or len(matrix) != len(rows) or any(not isinstance(r, list) or len(r) != len(cols) for r in matrix):
            raise ValueError('Heatmap must be rectangular and match its labels')
        traces = [{'type': 'heatmap', 'x': [_visual_text(x) for x in cols], 'y': [_visual_text(x) for x in rows],
                   'z': [[_visual_number(v, unit=unit) for v in row] for row in matrix], 'hoverongaps': False,
                   'colorscale': [[0, theme['colors'][min(2, len(theme['colors']) - 1)]], [1, theme['colors'][0]]],
                   'colorbar': {'title': {'text': unit}}}]
    elif kind in ('timeline', 'sequence'):
        events = spec.get('events', [])
        if not isinstance(events, list) or not 1 <= len(events) <= 40 or any(not isinstance(e, dict) for e in events):
            raise ValueError('Use 1–40 evidence events')
        labels = [_visual_text(e.get('label', '')) for e in events]
        if kind == 'timeline':
            dates = [_visual_date(e.get('date')) for e in events]
            layout['xaxis']['type'] = 'date'
        else:
            dates = list(range(1, len(events) + 1))
            layout['xaxis'].update(title={'text': 'Supplied sequence; no dates or duration implied'}, tickmode='linear', dtick=1)
        traces = [{'type': 'scatter', 'x': dates, 'y': list(range(len(events))), 'mode': 'markers',
                   'marker': {'color': theme['colors'][0], 'size': 16}, 'text': labels,
                   'hovertemplate': '%{text}<extra></extra>'}]
        layout['yaxis'].update(tickvals=list(range(len(events))), ticktext=labels, autorange='reversed')
        layout['margin']['l'] = min(500, max(180, max(len(x) for x in labels) * 10))
    elif kind == 'gantt':
        tasks = spec.get('tasks', [])
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 40:
            raise ValueError('Use 1–40 dated task intervals')
        labels = []; bases = []; durations = []
        for item in tasks:
            start = _visual_date(item.get('start')); end = _visual_date(item.get('end'))
            duration = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
            if duration <= 0:
                raise ValueError('Task intervals require end after start; unknown dates belong in sequence')
            labels.append(_visual_text(item.get('label', 'Task'))); bases.append(start); durations.append(duration)
        traces = [{'type': 'bar', 'orientation': 'h', 'base': bases, 'x': durations, 'y': labels,
                   'marker': {'color': theme['colors'][0]}}]
        layout['xaxis']['type'] = 'date'; layout['yaxis']['autorange'] = 'reversed'
    else:
        raise ValueError('kind must be bar, line, heatmap, timeline, sequence or gantt. For a pie, donut, gauge, table, funnel, radar, sankey, waterfall, flowchart, tree or dashboard call the matching render_* tool and then export_visual with its visual_id')
    # Explicit label space prevents Plotly's automatic margins from stealing footer
    # space or invalidating its wrapping width. Preserve raw category values/order.
    labels = layout['yaxis'].get('categoryarray') or layout['yaxis'].get('ticktext')
    if labels:
        label_width = min(round(width * 0.34), max(120, round(max(len(html.unescape(x)) for x in labels) * axis_font * 0.58)))
        chars = max(8, int(label_width / (axis_font * 0.62)))
        wrapped = ['<br>'.join(textwrap.wrap(x, chars)) for x in labels]
        layout['margin']['l'] = label_width + 28
        layout['yaxis'].update(tickmode='array', tickvals=labels if 'categoryarray' in layout['yaxis'] else layout['yaxis']['tickvals'], ticktext=wrapped)
        rows = sum(max(1, x.count('<br>') + 1) for x in wrapped)
        if rows * axis_font * 1.25 > height - layout['margin']['t'] - layout['margin']['b']:
            raise ValueError('Category labels need a taller chart or fewer supplied categories')
    if layout['xaxis'].get('type') == 'category':
        labels = layout['xaxis']['categoryarray']
        chars = max(6, int((width - layout['margin']['l'] - layout['margin']['r']) / len(labels) / (axis_font * 0.62)))
        wrapped = ['<br>'.join(textwrap.wrap(x, chars)) for x in labels]
        layout['xaxis'].update(tickmode='array', tickvals=labels, ticktext=wrapped)
        extra = max(x.count('<br>') for x in wrapped) * round(axis_font * 1.3)
        below_axis += extra
        layout['margin']['b'] += extra
    if caption:
        plot_width = width - layout['margin']['l'] - layout['margin']['r']
        if plot_width < 120:
            raise ValueError('Labels and legend need a wider chart')
        layout['annotations'][0].update(x=(48 - layout['margin']['l']) / plot_width, yshift=-below_axis)
    layout['meta'] = {'source_metadata': metadata}
    return {'data': traces, 'layout': layout}, {'width': width, 'height': height, 'metadata': metadata,
                                              'caption': caption, 'warnings': warnings}
