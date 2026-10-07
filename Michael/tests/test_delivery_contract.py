"""Delivery contract: tools reach the user's Open Terminal only through tools/workspace_delivery.py.

Every output-producing tool, now and in future, saves files, builds links and shapes its result with that
one module, so destinations, naming, size handling and serving behave identically. These checks fail if a
tool grows its own route.
"""
import re
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / 'tools'
LIBRARY = 'workspace_delivery.py'
# Shared modules and tools that never touch the terminal's filesystem.
NOT_TOOLS = {LIBRARY, 'visual_figure.py'}
# Reads other tools' results (paths and links) but delivers nothing itself.
CONSUMERS = {'delegate_subtask.py'}
OWN_TRANSPORT = ('files/upload', 'def _terminal_save', 'def _terminal_write', 'def _terminal_names', 'def _terminal_stat',
                 'def _proxy_url', 'def _terminal_download_url', 'def _destination', 'def _home_path', '/files/write')


class DeliveryContractTest(unittest.TestCase):
    def sources(self):
        return {p.name: p.read_text() for p in sorted(TOOLS.glob('*.py')) if p.name not in NOT_TOOLS}

    def test_no_tool_has_its_own_terminal_transport_naming_or_link_code(self):
        for name, source in self.sources().items():
            for forbidden in OWN_TRANSPORT:
                self.assertNotIn(forbidden, source, f'{name} must use workspace_delivery instead of {forbidden!r}')

    def test_tools_that_report_workspace_paths_import_the_library(self):
        reporting = [n for n, s in self.sources().items() if n not in CONSUMERS and re.search(r"workspace_path|terminal_download_url|terminal_saved", s)]
        self.assertTrue(reporting, 'expected delivering tools')
        for name in reporting:
            self.assertRegex(self.sources()[name], r'(?m)^from workspace_delivery import ', f'{name} reports terminal paths but does not use the shared library')

    def test_the_known_delivering_tools_are_all_covered(self):
        reporting = {n for n, s in self.sources().items() if 'workspace_delivery' in s}
        self.assertTrue({'generate_documents.py', 'generate_slides.py', 'visuals_toolkit_v4.py', 'document_translator.py', 'workspace_files.py'} <= reporting)

    def test_the_library_is_the_only_place_that_defines_these(self):
        library = (TOOLS / LIBRARY).read_text()
        for needed in ('def _terminal_save', 'def _home_path', 'def _destination', 'def _proxy_url', 'def _terminal_download_url', 'def _open_webui_copy', 'def _office_result'):
            self.assertIn(needed, library)

    def test_a_save_that_cannot_overwrite(self):
        library = (TOOLS / LIBRARY).read_text()
        self.assertIn('_unique(name, await _terminal_names(', library)


if __name__ == '__main__':
    unittest.main()
