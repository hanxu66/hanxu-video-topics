import copy
import importlib.util
import json
import posixpath
import re
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


lib = module('topic_library', ROOT / 'skills/hanxu-video-topics/scripts/topic_library.py')
release = module('build_release', ROOT / 'scripts/build_release.py')


def sample(key='scope-change', title='修改之前如何确认范围'):
    return {'question_key': key, 'topic': title, 'audience': '合成测试受众',
            'inspiration': '可以讲清额外修改怎样影响项目',
            'recall_prompt': '哪次客户说只改一点，你当时怎样答应，后来多做了什么？', 'sources': [
                {'platform': 'synthetic', 'ref': 'https://example.invalid/video/123?utm_source=test',
                 'raw_title': '合成原题', 'observed_at': '2026-10-22T12:00:00+08:00',
                 'evidence_scope': 'metadata_only', 'metrics': {'likes': '1.2万', 'followers': None}}]}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / 'user-work'
        lib.init(self.work)

    def data(self):
        return lib.load(self.work)[1]

    def test_init_preserves_existing_profile_and_data(self):
        profile = self.work / 'profile.json'
        profile.write_text('{"direction":"已有真实方向"}', encoding='utf-8')
        before = profile.read_bytes()
        with self.assertRaises(ValueError):
            lib.init(self.work)
        self.assertEqual(profile.read_bytes(), before)

    def test_user_data_cannot_be_written_in_public_package(self):
        with self.assertRaises(ValueError):
            lib.init(ROOT / 'user-work')

    def test_blank_profile_and_two_user_workspaces_stay_independent(self):
        template = ROOT / 'skills/hanxu-video-topics/references/profile-template.json'
        before = template.read_bytes()
        profile = json.loads((self.work / 'profile.json').read_text())
        self.assertIsNone(profile['audience'])
        self.assertIsNone(profile['topic_scope']['ai_relevance'])
        self.assertEqual(profile['experience_seeds'], [])
        self.assertEqual(profile['background']['roles'], [])
        profile['background']['roles'] = ['采购']
        profile['experience_seeds'] = ['一次供应商沟通']
        lib.atomic_json(self.work / 'profile.json', profile)
        second = Path(self.temp.name) / 'another-user'
        lib.init(second)
        self.assertEqual(json.loads((second / 'profile.json').read_text())['experience_seeds'], [])
        self.assertEqual(template.read_bytes(), before)

    def test_missing_recall_prompt_cannot_write_a_partial_batch(self):
        before = (self.work / 'library.json').read_bytes()
        invalid = sample('missing-prompt')
        invalid.pop('recall_prompt')
        with self.assertRaises(ValueError):
            lib.merge(self.work, [sample(), invalid])
        self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_legacy_topic_can_be_read_and_enriched_without_reopening_choice(self):
        lib.merge(self.work, [sample()])
        data = self.data()
        topic = data['topics'][0]
        topic.pop('recall_prompt')
        topic['status'] = '已选'
        topic['feedback'] = [{'quote': '我想拍这条'}]
        lib.atomic_json(self.work / 'library.json', data)
        self.assertIn('待补回想提示', lib.render(self.work))
        lib.merge(self.work, [sample(title='研究者建议的新标题')])
        enriched = self.data()['topics'][0]
        self.assertEqual(enriched['status'], '已选')
        self.assertEqual(enriched['topic'], '修改之前如何确认范围')
        self.assertEqual(enriched['recall_prompt'], sample()['recall_prompt'])
        self.assertIn(sample()['recall_prompt'], lib.render(self.work))

    def test_unknown_is_not_zero_and_formatted_counts_are_normalized(self):
        lib.merge(self.work, [sample()])
        metrics = self.data()['topics'][0]['sources'][0]['metrics']
        self.assertEqual(metrics['likes'], 12000)
        self.assertIsNone(metrics['followers'])
        self.assertIsNone(metrics['views'])
        for value in (True, -1, 'NaN', '0.5', '未知'):
            with self.assertRaises(ValueError):
                lib.count(value)

    def test_same_question_import_is_idempotent_and_keeps_observation_history(self):
        first = sample()
        lib.merge(self.work, [first])
        result = lib.merge(self.work, [first])
        self.assertEqual(result['added_ids'], [])
        self.assertEqual(len(self.data()['topics'][0]['sources']), 1)
        update = copy.deepcopy(first)
        update['sources'][0]['observed_at'] = '2026-10-23T12:00:00+08:00'
        update['sources'][0]['metrics']['likes'] = None
        lib.merge(self.work, [update])
        sources = self.data()['topics'][0]['sources']
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]['metrics']['likes'], 12000)
        self.assertIsNone(sources[1]['metrics']['likes'])

    def test_same_source_can_support_distinct_questions_without_counting_more_sources(self):
        lib.merge(self.work, [sample(), sample('pricing', '怎样预估额外工作的报价')])
        self.assertEqual(len(self.data()['topics']), 2)
        refs = {source['canonical_ref'] for topic in self.data()['topics'] for source in topic['sources']}
        self.assertEqual(len(refs), 1)

    def test_invalid_batch_leaves_previous_library_unchanged(self):
        before = (self.work / 'library.json').read_bytes()
        invalid = sample('bad'); invalid['sources'] = []
        with self.assertRaises(ValueError):
            lib.merge(self.work, [sample(), invalid])
        self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_choice_does_not_reject_other_topics_or_get_overwritten_by_import(self):
        lib.merge(self.work, [sample(), sample('capacity', '怎样评估项目承接量')])
        topic_id = self.data()['topics'][0]['id']
        lib.feedback(self.work, topic_id, '我想拍这一条，其余先留着', '已选')
        changed = sample(title='新研究的措辞建议'); changed['status'] = '优先备选'
        changed['recall_prompt'] = '有没有提前确认边界后少返工的例子？'
        lib.merge(self.work, [changed])
        first, second = self.data()['topics']
        self.assertEqual(first['status'], '已选')
        self.assertEqual(first['topic'], '修改之前如何确认范围')
        self.assertEqual(second['status'], '储备题')
        self.assertEqual(first['feedback'][0]['quote'], '我想拍这一条，其余先留着')
        self.assertEqual(first['recall_prompt'], sample()['recall_prompt'])
        self.assertIn({'field': 'recall_prompt', 'value': changed['recall_prompt']}, first['supplements'])

    def test_import_cannot_infer_publishing_and_feedback_can_keep_state(self):
        item = sample(); item['status'] = '已发布'
        with self.assertRaises(ValueError):
            lib.merge(self.work, [item])
        lib.merge(self.work, [sample()])
        topic_id = self.data()['topics'][0]['id']
        lib.feedback(self.work, topic_id, '只是觉得表达方式有启发')
        self.assertEqual(self.data()['topics'][0]['status'], '储备题')

    def test_research_can_update_unselected_status_after_more_evidence(self):
        lib.merge(self.work, [sample()])
        update = sample(); update['status'] = '优先备选'
        lib.merge(self.work, [update])
        self.assertEqual(self.data()['topics'][0]['status'], '优先备选')

    def test_source_query_identity_and_markdown_export(self):
        self.assertEqual(lib.canonical('https://example.invalid/watch?id=1&utm_source=x#top'),
                         'https://example.invalid/watch?id=1')
        self.assertNotEqual(lib.canonical('https://example.invalid/watch?id=1'),
                            lib.canonical('https://example.invalid/watch?id=2'))
        item = sample(title='修改 | 报价\n怎么谈')
        lib.merge(self.work, [item])
        self.assertIn('修改 ／ 报价 怎么谈', lib.render(self.work))

    def test_archive_allowlist_extraction_and_byte_identity(self):
        out = Path(self.temp.name) / 'release.zip'
        release.build(out)
        with zipfile.ZipFile(out) as archive:
            expected = {'hanxu-video-topics/' + item for item in release.FILES.values()}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIn('hanxu-video-topics/SKILL.md', archive.namelist())
            self.assertNotIn('hanxu-video-topics/skills/hanxu-video-topics/SKILL.md', archive.namelist())
            for source, target in release.FILES.items():
                if source.startswith('skills/'):
                    self.assertEqual(archive.read('hanxu-video-topics/' + target), (ROOT / source).read_bytes())
            for name in archive.namelist():
                if not name.endswith('.md'):
                    continue
                for ref in re.findall(r'\]\(([^)]+)\)', archive.read(name).decode()):
                    if '://' in ref or ref.startswith('#'):
                        continue
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), ref.split('#')[0]))
                    self.assertIn(resolved, archive.namelist(), (name, ref))
            archive.extractall(Path(self.temp.name) / 'isolated')
        extracted = Path(self.temp.name) / 'isolated/hanxu-video-topics'
        independent = module('extracted_library', extracted / 'scripts/topic_library.py')
        independent.init(Path(self.temp.name) / 'isolated-user')
        with self.assertRaises(ValueError):
            release.build(out)


if __name__ == '__main__':
    unittest.main()
