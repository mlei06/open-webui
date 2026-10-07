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
    width = spec.get('width', min(1600, round(1600 * theme['picture_ratio'])))
    height = spec.get('height', round(width / theme['picture_ratio']))
    if type(width) is not int or type(height) is not int or not 600 <= width <= 2400 or not 400 <= height <= 2400 or width * height > 4_000_000:
        raise ValueError('Use bounded slide-ready dimensions, 600–2400 by 400–2400 pixels, at most 4 MP')
    # Raster resolution must not make slide text smaller: size in physical points.
    pixels_per_point = width / theme.get('picture_width_points', width / 3)
    axis_font = max(24, round(14 * pixels_per_point))
    heading_font = max(32, round(18 * pixels_per_point))
    caption_font = max(16, round(9 * pixels_per_point))
    line_width = max(24, int((width - 180) / (caption_font * 0.6)))
    caption_lines = [line for part in caption_parts for line in textwrap.wrap(part, line_width)]
    caption = '<br>'.join(caption_lines)
    title_width = max(24, int((width - 120) / (heading_font * 0.6)))
    title_lines = textwrap.wrap(title, title_width)
    if len(title_lines) > 2:
        raise ValueError('Use a shorter title for the selected chart width')
    title = '<br>'.join(title_lines)
    traces = []
    layout = {'width': width, 'height': height, 'paper_bgcolor': theme['background'],
              'plot_bgcolor': theme['background'],
              'font': {'family': theme['font'], 'size': axis_font, 'color': theme['foreground']},
              'title': {'text': title, 'x': 0.02, 'font': {'size': heading_font, 'family': theme['font']}},
              'margin': {'l': 120, 'r': 90, 't': max(95, heading_font * (len(title_lines) + 1)), 'b': round(axis_font * 4.5) + round(caption_font * 1.4) * len(caption_lines) + 30},
              'colorway': theme['colors'], 'showlegend': False,
              'xaxis': {'automargin': True, 'zeroline': False},
              'yaxis': {'automargin': True, 'zeroline': False},
              'annotations': [{'text': caption, 'xref': 'paper', 'yref': 'paper', 'x': 0, 'y': 0, 'yshift': -round(axis_font * 4.5),
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
                     'marker': {'color': theme['colors'][i % len(theme['colors'])]},
                     'x': values if kind == 'bar' and orientation == 'horizontal' else categories,
                     'y': categories if kind == 'bar' and orientation == 'horizontal' else values}
            if kind == 'bar':
                trace['orientation'] = 'h' if orientation == 'horizontal' else 'v'
            else:
                trace.update(mode='lines+markers', connectgaps=False,
                             line={'color': theme['colors'][i % len(theme['colors'])], 'width': 4})
            traces.append(trace)
        layout['showlegend'] = len(series) > 1
        if kind == 'bar':
            bar_mode = spec.get('bar_mode', 'group')
            if bar_mode not in ('group', 'stack'):
                raise ValueError('bar_mode must be group or stack')
            if orientation == 'horizontal' and len(categories) * axis_font * 1.5 > height - layout['margin']['t'] - layout['margin']['b']:
                raise ValueError('Too many categories to remain legible in this slide picture; use a supplied aggregate top-N/Other or larger compatible dimensions')
            layout['barmode'] = bar_mode
            count_axis = 'xaxis' if orientation == 'horizontal' else 'yaxis'
            category_axis = 'yaxis' if orientation == 'horizontal' else 'xaxis'
            layout[count_axis].update(title={'text': unit}, rangemode='tozero')
            layout[category_axis].update(type='category', categoryorder='array', categoryarray=categories)
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
        raise ValueError('Use bar, line, heatmap, timeline, sequence or gantt')
    return {'data': traces, 'layout': layout}, {'width': width, 'height': height, 'metadata': metadata,
                                              'caption': caption, 'warnings': warnings}
