"""Static guards for the theme tokens (no browser, no network, standard library only).

  python3 Michael/tests/test_branding_contrast.py

The pixel-level audit is tests/verify_contrast.mjs; these catch a token edit that
breaks the ramp before anyone runs it.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bootstrap'))
import branding  # noqa: E402

TOKENS = json.loads((branding.BRANDING_DIR / 'tokens.json').read_text())


class TokenContrastTests(unittest.TestCase):
    def test_static_contrast_report_passes(self):
        failed = [msg for ok, msg in branding.contrast_report(TOKENS) if not ok]
        self.assertEqual(failed, [])

    def test_light_text_steps_hold_aa_on_the_darkest_ground(self):
        # Open WebUI paints inactive tabs, meta lines and hints with gray-300..gray-600.
        light = TOKENS['light']
        ground = branding.over(branding.parse_color(light['veil']), branding.parse_color(light['gradient']['stops'][0][0]))
        for step in ('300', '400', '500', '600', '700', '800'):
            r = branding.ratio(branding.parse_color(light['ramp'][step]), ground)
            self.assertGreaterEqual(r, 4.5, f'light gray-{step} on the gradient start: {r:.2f}:1')

    def test_ramp_is_monotonic_in_light(self):
        steps = ['300', '400', '500', '600', '700', '800', '850', '900', '950']
        lum = [branding.luminance(branding.parse_color(TOKENS['light']['ramp'][k])) for k in steps]
        self.assertEqual(lum, sorted(lum, reverse=True))

    def test_every_token_set_defines_what_the_css_reads(self):
        for mode in ('dark', 'light'):
            for key in ('edge', 'error', 'warning', 'success', 'rule', 'panel', 'panelRaised', 'sidebar'):
                self.assertIn(key, TOKENS[mode], f'{mode}.{key}')

    def test_built_theme_is_safe_for_the_plugin_loader(self):
        logo = branding.BRAND_DIR / 'lenovo-logo.svg'
        if not logo.is_file():
            self.skipTest('runtime/brand/lenovo-logo.svg not present')
        sections = branding.build_sections(TOKENS, logo)
        flat = ''.join((sections['vars'], sections['structural'], sections['gradient'], sections['custom']))
        self.assertLessEqual(branding.longest_plain_run(flat), branding.MAX_PLAIN_RUN)
        self.assertEqual(branding.external_urls(flat), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
