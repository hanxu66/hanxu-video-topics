"""Regression checks for event identity and non-destructive legacy indexing."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from test_library import lib, sample, release


class EventIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        lib.init(self.root)
        lib.merge(self.root, [sample()])

    def data(self):
        return lib.load(self.root)[1]

    def legacy(self):
        data = self.data()
        tid = data['topics'][0]['id']
        event = {'at': '2026-10-22T01:00:00+00:00', 'topic_id': tid,
                 'quote': '合成反馈：只留着，还没选', 'scope': '核心问题'}
        data['feedback'] = [copy.deepcopy(event)]
        topic = data['topics'][0]
        topic['feedback'] = [copy.deepcopy(event)]
        topic['supplements'] = [{'field': 'inspiration', 'value': '合成历史角度，来源关联未知'}]
        topic['status_reviews'] = [{'previous_status': '待补角度', 'status': '储备题',
                                   'reason': '合成旧复评，时间未保存'}]
        lib.atomic_json(self.root / 'library.json', data)
        return data

    def test_new_feedback_has_same_id_in_both_views(self):
        tid = self.data()['topics'][0]['id']
        event = lib.feedback(self.root, tid, '合成选择：先选这题', '已选', '核心问题')
        self.assertTrue(event['id'].startswith('FB-'))
        data = self.data()
        self.assertEqual(event, data['feedback'][0])
        self.assertEqual(event, data['topics'][0]['feedback'][0])
        self.assertIn(event['id'], lib.render(self.root))
        self.assertNotIn('id_assigned_at', event)

    def test_read_and_export_do_not_migrate_legacy(self):
        original = self.legacy()
        before = (self.root / 'library.json').read_bytes()
        text = lib.render(self.root)
        self.assertIn('未记录', text)
        self.assertEqual(self.data(), original)
        self.assertEqual((self.root / 'library.json').read_bytes(), before)

    def test_index_preserves_all_original_fields_and_exact_backup(self):
        original = self.legacy()
        before = (self.root / 'library.json').read_bytes()
        result = lib.index_events(self.root)
        self.assertEqual(Path(result['backup']).read_bytes(), before)
        self.assertEqual(result['assigned_events'], 3)  # mirrored feedback is one event
        indexed = self.data()
        self.assertEqual(indexed['feedback'][0]['id'], indexed['topics'][0]['feedback'][0]['id'])
        for event in [indexed['feedback'][0], *indexed['topics'][0]['supplements'],
                      *indexed['topics'][0]['status_reviews']]:
            self.assertEqual(event['id_origin'], 'indexed_legacy')
            self.assertTrue(event['id_assigned_at'])
        self.assertNotIn('at', indexed['topics'][0]['supplements'][0])
        self.assertNotIn('source_observations', indexed['topics'][0]['supplements'][0])
        self.assertNotIn('at', indexed['topics'][0]['status_reviews'][0])
        def strip(value):
            if isinstance(value, list):
                return [strip(v) for v in value]
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items()
                        if k not in {'id_origin', 'id_assigned_at'} and
                        not (k == 'id' and str(v).startswith(('SUP-', 'REV-', 'FB-')))}
            return value
        self.assertEqual(strip(indexed), original)
        self.assertEqual(indexed['topics'][0]['sources'], original['topics'][0]['sources'])

    def test_repeated_index_keeps_bytes_and_existing_ids(self):
        self.legacy()
        lib.index_events(self.root)
        before = (self.root / 'library.json').read_bytes()
        self.assertEqual(lib.index_events(self.root), {'assigned_events': 0, 'backup': None})
        self.assertEqual((self.root / 'library.json').read_bytes(), before)

    def test_index_does_not_relabel_new_events(self):
        tid = self.data()['topics'][0]['id']
        event = lib.feedback(self.root, tid, '合成改写反馈', scope='改写')
        lib.index_events(self.root)
        self.assertEqual(self.data()['feedback'][0], event)

    def test_extracted_cli_can_index_legacy_without_filling_time(self):
        self.legacy()
        archive = Path(self.tmp.name) / 'candidate.zip'
        release.build(archive)
        extracted = Path(self.tmp.name) / 'package'
        with zipfile.ZipFile(archive) as z:
            z.extractall(extracted)
        command = [sys.executable, str(extracted/'hanxu-video-topics/scripts/topic_library.py'),
                   '--workspace', str(self.root), 'index-events']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['assigned_events'], 3)
        self.assertNotIn('at', self.data()['topics'][0]['supplements'][0])


if __name__ == '__main__':
    unittest.main()
