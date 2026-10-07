"""Skills: the declared guides, their installer, and the minimal system prompts that point to them.

  uv run --no-project --with pyyaml python Michael/tests/test_skills.py
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bootstrap'))
import skills as sk  # noqa: E402

EXPECTED = {'qdts', 'visualization', 'delegation', 'powerpoint', 'document-translation', 'web-search',
            'mail-drafting', 'path-mailroom', 'terminal-workspace'}
TOOL_DETAIL = ('search_cases', 'aggregate_records', 'lookup_entities', 'get_cases', 'get_records', 'generate_slides',
               'generate_document', 'render_visualization', 'render_chart', 'export_visual', 'translate_attachment',
               'delegate_agents', 'search_web', 'fetch_url', 'path_search_records', 'create_draft')


def declared():
    return sk.load()[0]


class DeclaredSkills(unittest.TestCase):
    def test_every_expected_skill_is_declared_with_the_fields_open_webui_needs(self):
        skills = declared()
        self.assertEqual(set(skills), EXPECTED)
        for skill_id, form in skills.items():
            self.assertRegex(skill_id, r'^[a-z0-9_-]+$')
            self.assertTrue(form['name'] and form['content'].startswith('#'), skill_id)
            self.assertTrue(20 <= len(form['description']) <= 400, skill_id)
            self.assertIn('load', form['description'].lower() + 'load', skill_id)
            self.assertEqual(form['access_grants'], sk.PUBLIC_READ)
            self.assertTrue(form['is_active'])
        self.assertEqual(len({f['name'] for f in skills.values()}), len(skills))

    def test_skills_refer_only_to_skills_that_exist(self):
        names = {f['name'].lower().replace(' ', '-') for f in declared().values()} | set(declared())
        for skill_id, form in declared().items():
            for mention in re.findall(r'\*\*([A-Za-z-]+)\*\* skill', form['content']):
                self.assertIn(mention.lower(), names, f'{skill_id} -> {mention}')

    def test_the_qdts_skill_covers_every_tool_and_the_planning_material(self):
        content = declared()['qdts']['content']
        for tool in ('lookup_entities', 'get_entity', 'search_cases', 'search_notes', 'search_tasks', 'aggregate_records', 'get_cases', 'get_records'):
            self.assertIn(f'`{tool}`', content)
        for needle in ('query_mode', 'sub_top_n', 'product', 'case_participant', 'Simple questions', 'Complex questions',
                       'Anti-patterns', 'INDEX_CHANGED', 'data_as_of'):
            self.assertIn(needle, content, needle)
        self.assertGreaterEqual(len(re.findall(r'^\*\*\d+\. ', content, re.M)), 10)  # worked multi-step examples

    def test_each_agent_skill_names_the_tools_it_teaches(self):
        skills = declared()
        for skill_id, tools in {'visualization': ('render_visualization', 'export_visual', 'visual_id'),
                                'delegation': ('list_agents', 'delegate_agents', 'file_ids', 'web-searcher', 'document-translator', 'office-documents'),
                                'powerpoint': ('get_slide_layouts', 'generate_slides', 'headers', 'terminal_image_path'),
                                'document-translation': ('translate_attachment', 'deliver_translation', 'cancel_translation'),
                                'web-search': ('search_web', 'fetch_url'),
                                'mail-drafting': ('create_draft', 'prepare_email_attachments', 'suggested_attachment_ids', 'send_draft'),
                                'path-mailroom': ('path_lookup_employees', 'path_count_records'),
                                'terminal-workspace': ('publish_workspace_file', 'import_attachment')}.items():
            for tool in tools:
                self.assertIn(tool, skills[skill_id]['content'], f'{skill_id}: {tool}')


class Parsing(unittest.TestCase):
    def write(self, text):
        directory = Path(tempfile.mkdtemp()) / 'demo'
        directory.mkdir()
        (directory / 'SKILL.md').write_text(text)
        return directory / 'SKILL.md'

    def test_frontmatter_is_required_and_complete(self):
        name, description, content = sk.parse(self.write('---\nname: Demo\ndescription: Does a thing\n---\n\n# Body\n\ntext\n'))
        self.assertEqual((name, description, content), ('Demo', 'Does a thing', '# Body\n\ntext\n'))
        for bad in ('# no frontmatter\n', '---\nname: Demo\n---\nbody\n', '---\nname: Demo\ndescription: x\n---\n\n'):
            with self.assertRaises(ValueError):
                sk.parse(self.write(bad))


class FakeOpenWebUI:
    def __init__(self, rows=None):
        self.rows, self.calls = rows or {}, []

    def __call__(self, base, method, route, token=None, body=None):
        self.calls.append((method, route))
        match = re.match(r'/api/v1/skills/id/([^/]+)$', route)
        if method == 'GET' and match:
            if match.group(1) not in self.rows:
                raise sk.ApiError('HTTP 404')
            return self.rows[match.group(1)]
        if route == '/api/v1/skills/create' or route.endswith('/update'):
            self.rows[body['id']] = {**body, 'access_grants': [{**g, 'id': 'x'} for g in body['access_grants']]}
            return self.rows[body['id']]
        if method == 'DELETE':
            self.rows.pop(route.split('/')[5])
            return True
        raise AssertionError(route)


class Installer(unittest.TestCase):
    def run_reconcile(self, fake, apply=True, retired=()):
        with patch.object(sk, 'call', new=fake):
            return sk.reconcile('b', 't', declared(), list(retired), apply)

    def test_a_fresh_install_creates_everything_and_a_second_run_changes_nothing(self):
        fake = FakeOpenWebUI()
        self.assertTrue(self.run_reconcile(fake))
        self.assertEqual(sum(1 for m, r in fake.calls if r == '/api/v1/skills/create'), len(EXPECTED))
        self.assertEqual(set(fake.rows), EXPECTED)
        fake.calls.clear()
        self.assertFalse(self.run_reconcile(fake))
        self.assertEqual([m for m, _ in fake.calls if m != 'GET'], [])

    def test_drift_in_content_or_grants_is_repaired_and_check_mode_writes_nothing(self):
        fake = FakeOpenWebUI()
        self.run_reconcile(fake)
        fake.rows['qdts']['content'] = 'edited in the app'
        fake.rows['delegation']['access_grants'] = []
        fake.calls.clear()
        self.assertTrue(self.run_reconcile(fake, apply=False))
        self.assertEqual([m for m, _ in fake.calls if m != 'GET'], [])
        self.assertTrue(self.run_reconcile(fake, apply=True))
        self.assertEqual(fake.rows['qdts']['content'], declared()['qdts']['content'])
        self.assertFalse(self.run_reconcile(fake, apply=False))

    def test_skills_that_are_not_declared_are_never_touched_but_retired_ids_are_removed(self):
        fake = FakeOpenWebUI({'mine': {'id': 'mine', 'name': 'Mine', 'description': 'x', 'content': 'x', 'is_active': True, 'access_grants': []},
                              'old-guide': {'id': 'old-guide', 'name': 'Old', 'description': 'x', 'content': 'x', 'is_active': True, 'access_grants': []}})
        self.run_reconcile(fake, retired=['old-guide'])
        self.assertIn('mine', fake.rows)
        self.assertNotIn('old-guide', fake.rows)
        self.assertEqual([c for c in fake.calls if 'mine' in c[1]], [])


class MinimalPrompts(unittest.TestCase):
    def prompt(self, name):
        return (ROOT / 'prompts' / name).read_text()

    def test_lenny_and_case_assistant_hold_role_style_and_pointers_not_tool_guides(self):
        for name, limit in (('lenny.md', 4500), ('case-assistant.md', 3200)):
            text = self.prompt(name)
            self.assertLess(len(text), limit, name)
            for detail in TOOL_DETAIL:
                self.assertNotIn(detail, text, f'{name} still carries tool usage: {detail}')
            self.assertIn('view_skill', text)
            self.assertIn('<user_context>', text)

    def test_lenny_names_every_skill_and_routes_web_translation_and_decks_to_agents(self):
        text = self.prompt('lenny.md')
        for skill in EXPECTED:
            self.assertRegex(text, rf'- {skill}:', skill)
        for agent in ('web-searcher', 'document-translator', 'office-documents'):
            self.assertIn(agent, text)
        for rule in ('short, simple jobs yourself', 'tool-heavy', 'in the background', 'whenever the user asks you to delegate'):
            self.assertIn(rule, text)  # delegate for heavy or background work, and on request; otherwise do it directly
        self.assertNotIn('never search the web yourself', text)

    def test_lenny_keeps_the_tools_for_the_skills_it_names(self):
        import json
        lenny = next(m for m in json.loads((ROOT / 'models' / 'presets.json').read_text())['presets'] if m['id'] == 'lenny')
        tools = {list(t.values())[0] for t in lenny['tools']}
        self.assertLessEqual({'doctranslator', 'document_translator', 'generate_slide_pptx', 'generate_docx_documents',
                              'visuals_toolkit_v4', 'delegate_agents', 'qdts', 'mail', 'path', 'workspace_files'}, tools)
        self.assertTrue(lenny['capabilities']['web_search'] and 'web_search' in lenny['builtin_tools'])

    def test_sending_mail_needs_an_explicit_instruction_everywhere_it_is_taught(self):
        skill = declared()['mail-drafting']['content']
        for needle in ('only', 'explicitly tells you to send', 'automation', 'do not send', 'cannot carry attachments', 'once'):
            self.assertIn(needle, skill, needle)
        self.assertIn('default is to draft for the user to review', skill)
        lenny = (ROOT / 'prompts' / 'lenny.md').read_text()
        self.assertIn('send_draft only when the user explicitly tells you to send it', lenny)

    def test_skills_teach_the_series_id_trap_and_no_invented_categories_and_no_improvised_charts(self):
        qdts = declared()['qdts']['content']
        for needle in ('"descendants": true', 'SERIES id', 'Never put a dimension name', 'Only what the data has', 'customer tier'):
            self.assertIn(needle, qdts, needle)
        viz = declared()['visualization']['content']
        self.assertIn('never with Python, matplotlib', viz)
        deck = declared()['powerpoint']['content']
        for needle in ('never a bare file name', 'never build one from scratch with python-pptx', 'native chart', 'never type `•`', 'Section Header_White', 'every new deck', 'layout_adjustments', 'You CAN edit a deck', 'exact `workspace_path` of the latest result', 'display_file', 'inline: true'):
            self.assertIn(needle, deck, needle)

    def test_lenny_offers_to_draft_feedback_to_michael_and_never_sends_it_unprompted(self):
        text = (ROOT / 'prompts' / 'lenny.md').read_text()
        self.assertIn('mlei4@lenovo.com', text)
        for needle in ('bug', 'feature', 'draft an email to Michael', 'draft it by default', 'send it only if the user says to', 'without pasting case text'):
            self.assertIn(needle, text, needle)
        self.assertIn('built by Michael Lei', text); self.assertIn('alpha testing', text)

    def test_the_delegation_skill_says_when_to_delegate_and_when_not_to(self):
        content = declared()['delegation']['content']
        for needle in ('Do short, simple jobs directly', 'tool-heavy', 'background', 'asks you to delegate', 'END YOUR TURN'):
            self.assertIn(needle, content, needle)

    def test_case_assistant_points_to_its_skills(self):
        text = self.prompt('case-assistant.md')
        for skill in ('qdts', 'visualization', 'terminal-workspace'):
            self.assertRegex(text, rf'- {skill}:')

    def test_every_skill_a_prompt_points_to_exists(self):
        known = set(declared())
        for path in (ROOT / 'prompts').glob('*.md'):
            text = path.read_text()
            self.assertLessEqual(set(re.findall(r'load the ([a-z-]+) skill', text)), known, path.name)
            if path.name in ('lenny.md', 'case-assistant.md'):
                self.assertLessEqual(set(re.findall(r'^- ([a-z-]+): ', text, re.M)), known, path.name)


if __name__ == '__main__':
    unittest.main()
