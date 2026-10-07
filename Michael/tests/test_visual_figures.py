"""Contract tests for bounded evidence figures and deployable tool source."""
import json
import sys
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'bootstrap'))
from visual_figure import _visual_figure, _script_json
from office_tools import bundle_delivery_source
THEME = dict(background='#FFFFFF', foreground='#000000', font='Arial', colors=['#E1251B','#871C23'], picture_ratio=1.7)

class VisualFigures(unittest.TestCase):
    def figure(self, **kwargs):
        spec = dict(kind='bar', categories=['A','Unknown','Other'], series=[dict(name='Cases',values=[4,2,None])])
        spec.update(kwargs)
        return _visual_figure(spec, THEME)

    def test_case_order_null_and_theme(self):
        f, _ = self.figure()
        self.assertEqual(f['data'][0]['x'], [4,2,None])
        self.assertEqual(f['layout']['yaxis']['categoryarray'], ['A','Unknown','Other'])
        self.assertEqual(f['layout']['paper_bgcolor'], '#FFFFFF')
        self.assertEqual(f['layout']['colorway'][0], '#E1251B')

    def test_shape_finite_counts_and_caps(self):
        for values in [[1], [True,2,3], [-1,2,3], [1.5,2,3], [float('inf'),2,3]]:
            with self.assertRaises(ValueError): self.figure(series=[dict(name='bad',values=values)])
        with self.assertRaises(ValueError): self.figure(categories=['a']*201)
        with self.assertRaises(ValueError): self.figure(width=2400,height=2400)

    def test_heatmap_missing_not_zero(self):
        f,_ = self.figure(kind='heatmap',row_labels=['A'],column_labels=['Jan','Feb'],values=[[None,3]])
        self.assertEqual(f['data'][0]['z'], [[None,3]])
        self.assertNotEqual(f['data'][0]['colorscale'][0][1], f['layout']['plot_bgcolor'])
        with self.assertRaises(ValueError): self.figure(kind='heatmap',row_labels=['A'],column_labels=['Jan','Feb'],values=[[3]])
        with self.assertRaises(ValueError): self.figure(kind='heatmap',row_labels=['A'],column_labels=['Jan','Feb'],values=[[-1,2]])

    def test_partial_period_provenance(self):
        metadata=dict(scope='2026 only',date_basis='case open date',period='2026-03 partial',warnings=['Employee participation overlaps; totals are not additive'])
        f,info=self.figure(kind='line',metadata=metadata)
        self.assertEqual(info['metadata'],metadata)
        self.assertFalse(f['data'][0]['connectgaps'])
        self.assertIn('partial',f['layout']['annotations'][0]['text'])

    def test_intervals_and_undated(self):
        with self.assertRaises(ValueError): self.figure(kind='timeline',events=[dict(label='No date')])
        with self.assertRaises(ValueError): self.figure(kind='gantt',tasks=[dict(label='bad',start='2026-01-02',end='2026-01-01')])
        f,_=self.figure(kind='sequence',events=[dict(label='Assigned'),dict(label='Transferred')])
        self.assertEqual(f['data'][0]['x'],[1,2])
        self.assertIn('no dates',f['layout']['xaxis']['title']['text'])

    def test_physical_slide_legibility_and_grouped_bars(self):
        theme={**THEME,'picture_width_points':420}
        spec=dict(kind='bar',categories=['A','B'],series=[dict(name='One',values=[1,2]),dict(name='Two',values=[2,3])],bar_mode='stack')
        f,_=_visual_figure(spec,theme)
        self.assertEqual(f['layout']['barmode'],'stack')
        self.assertGreaterEqual(f['layout']['font']['size'],40)
        with self.assertRaises(ValueError):
            _visual_figure(dict(kind='bar',categories=['A']*50,series=[dict(name='Count',values=[1]*50)]),theme)

    def test_breakout_and_bundling(self):
        value={'text':'</script><script>alert(1)</script>\u2028'}
        safe=_script_json(value)
        self.assertNotIn('</script>',safe)
        self.assertEqual(json.loads(safe),value)
        for name in ('visuals_toolkit_v4','generate_slides','generate_documents'):
            bundled=bundle_delivery_source((ROOT/'tools'/(name+'.py')).read_text())
            compile(bundled,name,'exec')
            self.assertNotIn('from workspace_delivery import',bundled)
            self.assertNotIn('from visual_figure import',bundled)


class ExportedChartLayout(unittest.TestCase):
    """Why an exported chart must look as good as the inline one: shape, legend, colours and labels."""
    WIDE = {**THEME, 'picture_ratio': 2.33, 'picture_width_points': 840, 'colors': ['#E1251B', '#871C23', '#D9C1D9', '#4D144A', '#C9D1F2', '#0E1B4D']}
    MONTHS = ['2026-07', '2026-08', '2026-09', '2026-10']
    NAMES = ['M Series desktops (ThinkCentre)', 'Lenovo Chromebooks Series', 'L Series laptops (ThinkPad)', '500 Series laptops (ideapad)',
             'P Series workstations (ThinkStation)', 'T Series laptops (ThinkPad)', 'X Series laptops (ThinkPad)', 'P Series laptops (ThinkPad)',
             '300 Series laptops (ideapad)', 'Edge Series laptops (ThinkPad)']

    def chart(self, count, **kwargs):
        series = [dict(name=self.NAMES[i], values=[(i + j) % 4 for j in range(4)]) for i in range(count)]
        spec = dict(kind='bar', categories=self.MONTHS, orientation='vertical', series=series)
        spec.update(kwargs)
        return _visual_figure(spec, self.WIDE)

    def test_a_chart_slide_gives_a_wide_canvas_not_a_portrait_one(self):
        _, info = self.chart(3)
        self.assertGreater(info['width'] / info['height'], 2)

    def test_many_series_get_distinct_colours_and_a_legend_down_the_side(self):
        f, _ = self.chart(10)
        colours = [t['marker']['color'] for t in f['data']]
        self.assertEqual(len(set(colours)), 10)
        legend = f['layout']['legend']
        self.assertEqual((legend['orientation'], legend['xanchor']), ('v', 'left'))
        self.assertGreater(f['layout']['margin']['r'], 300)  # room reserved so the plot keeps its height
        self.assertEqual(legend['traceorder'], 'normal')

    def test_few_series_keep_the_template_colours_and_a_horizontal_legend_on_top(self):
        f, _ = self.chart(3)
        self.assertEqual([t['marker']['color'] for t in f['data']], self.WIDE['colors'][:3])
        legend = f['layout']['legend']
        self.assertEqual((legend['orientation'], legend['yanchor']), ('h', 'bottom'))
        self.assertGreater(f['layout']['margin']['t'], 95)

    def test_series_names_that_cannot_fit_are_refused_not_squeezed(self):
        with self.assertRaises(ValueError):
            self.chart(4, series=[dict(name='x' * 150, values=[1, 2, 3, 4]) for _ in range(4)])

    def test_value_labels_skip_zero_and_missing(self):
        f, _ = _visual_figure(dict(kind='bar', categories=['A', 'B', 'C'], orientation='vertical', series=[dict(name='N', values=[3, 0, None])]), self.WIDE)
        self.assertEqual(f['data'][0]['text'], ['3', '', ''])
        self.assertEqual(f['data'][0]['textangle'], 0)

    def test_thin_stacked_segments_are_not_labelled(self):
        f, _ = self.chart(2, bar_mode='stack', series=[dict(name='Big', values=[30, 20, 10, 1]), dict(name='Thin', values=[1, 1, 1, 1])])
        self.assertEqual(f['data'][1]['text'], ['', '', '', ''])
        self.assertEqual(f['data'][0]['text'], ['30', '20', '10', ''])
        self.assertEqual(f['data'][0]['textposition'], 'inside')

    def test_many_points_are_left_unlabelled_rather_than_crowded(self):
        f, _ = _visual_figure(dict(kind='bar', categories=[f'C{i}' for i in range(20)], orientation='vertical',
                                   series=[dict(name=f'S{i}', values=[1] * 20) for i in range(3)]), self.WIDE)
        self.assertNotIn('text', f['data'][0])

    def test_the_caption_is_one_compact_paragraph(self):
        metadata = dict(source='QDTS replica', scope='Jul to Oct', coverage='154/158 cases', date_basis='started month', warnings=['Partial months', 'Groups overlap'])
        f, _ = self.chart(2, metadata=metadata)
        caption = f['layout']['annotations'][0]['text']
        self.assertLessEqual(caption.count('<br>'), 1)
        self.assertIn('\u2022', caption)
        self.assertIn('source: QDTS replica', caption)

    def test_a_slide_that_has_its_own_title_can_omit_the_images_title(self):
        f, _ = self.chart(2, show_title=False)
        self.assertEqual(f['layout']['title']['text'], '')
        with_title, _ = self.chart(2)
        self.assertLess(f['layout']['margin']['t'], with_title['layout']['margin']['t'])
        with self.assertRaises(ValueError):
            self.chart(2, show_title='no')

    def test_bars_are_thick_and_axis_labels_upright(self):
        f, _ = self.chart(2)
        self.assertLessEqual(f['layout']['bargap'], 0.25)
        self.assertEqual(f['layout']['xaxis']['tickangle'], 0)

if __name__=='__main__': unittest.main()
