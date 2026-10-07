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
            self.assertNotIn('from office_delivery import',bundled)
            self.assertNotIn('from visual_figure import',bundled)

if __name__=='__main__': unittest.main()
