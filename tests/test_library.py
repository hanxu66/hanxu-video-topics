import copy
import importlib.util
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
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
        self.assertTrue(any(event['field'] == 'recall_prompt' and event['value'] == changed['recall_prompt']
                            for event in first['supplements']))

    def test_supplement_retains_record_time_and_exact_source_observation(self):
        lib.merge(self.work, [sample()])
        tid = self.data()['topics'][0]['id']
        lib.feedback(self.work, tid, '【合成选择】这条想讲', '已选', '核心问题')
        old = copy.deepcopy(self.data()['topics'][0])
        update = sample()
        update['recall_prompt'] = '【合成新提示】哪次同一参考图里的颜色和构图被误认为同一要求？'
        update['sources'][0]['observed_at'] = '2026-10-23T12:00:00+08:00'
        with patch.object(lib, 'now', return_value='2026-10-24T12:00:00+08:00'):
            lib.merge(self.work, [update])
        after = self.data()['topics'][0]
        event = after['supplements'][0]
        self.assertTrue(event['id'].startswith('SUP-'))
        self.assertEqual(event['topic_id'], tid)
        self.assertEqual(event['at'], '2026-10-24T12:00:00+08:00')
        self.assertEqual(event['source_observations'], [{'platform': 'synthetic',
                         'ref': 'https://example.invalid/video/123',
                         'observed_at': '2026-10-23T12:00:00+08:00'}])
        for field in ('topic', 'status', 'feedback', 'recall_prompt'):
            self.assertEqual(after[field], old[field])
        rendered = lib.render(self.work)
        for value in (old['recall_prompt'], event['value'], event['id'], event['at'],
                      event['source_observations'][0]['observed_at']):
            self.assertIn(value, rendered)
        before = (self.work / 'library.json').read_bytes()
        lib.merge(self.work, [update])
        self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_same_suggestion_with_new_evidence_keeps_two_event_histories(self):
        lib.merge(self.work, [sample()])
        update = sample(); update['recall_prompt'] = '【合成建议】回想一次当场确认修改范围的对话'
        lib.merge(self.work, [update])
        first = copy.deepcopy(self.data()['topics'][0]['supplements'][0])
        update['sources'][0]['observed_at'] = '2026-10-25T12:00:00+08:00'
        lib.merge(self.work, [update])
        events = self.data()['topics'][0]['supplements']
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], first)
        self.assertNotEqual(events[0]['id'], events[1]['id'])
        self.assertNotEqual(events[0]['source_observations'], events[1]['source_observations'])

    def test_legacy_supplement_and_review_export_never_invents_metadata(self):
        lib.merge(self.work, [sample()])
        data = self.data(); topic = data['topics'][0]
        legacy = {'field': 'recall_prompt', 'value': '【合成旧提示】回想一次颜色偏好没问清的情况'}
        topic['supplements'] = [legacy]
        topic['status_reviews'] = [{'at': '2026-10-23T12:00:00+08:00', 'topic_id': topic['id'],
                                   'previous_status': '待补角度', 'status': '储备题',
                                   'reason': '【合成旧复评】补了场景，无事件ID'}]
        lib.atomic_json(self.work / 'library.json', data)
        before = (self.work / 'library.json').read_bytes()
        rendered = lib.render(self.work)
        self.assertIn(legacy['value'], rendered)
        self.assertIn('| 未记录 | 未记录 |', rendered)
        self.assertEqual((self.work / 'library.json').read_bytes(), before)
        update = sample(); update['recall_prompt'] = legacy['value']
        lib.merge(self.work, [update])
        self.assertEqual(self.data()['topics'][0]['supplements'], [legacy])

    def test_statistics_count_observations_and_compare_timezones_not_text(self):
        item = sample()
        item['sources'][0]['observed_at'] = '2026-10-23T13:00:00+09:00'
        repeat = copy.deepcopy(item['sources'][0]); repeat['observed_at'] = '2026-10-23T05:00:00Z'
        other = copy.deepcopy(repeat); other['ref'] = 'https://example.invalid/video/456'
        other['observed_at'] = '2026-10-23T04:30:00Z'
        item['sources'] += [repeat, other]
        lib.merge(self.work, [item])
        stats = lib.source_statistics(self.data()['topics'][0])
        self.assertEqual(stats, {'source_observations': 3, 'unique_sources': 2,
                                'latest_observed_at': '2026-10-23T05:00:00Z'})
        rendered = lib.render(self.work)
        self.assertIn('| 3 | 2 | 2026-10-23T05:00:00Z |', rendered)
        legacy = copy.deepcopy(self.data()); source = legacy['topics'][0]['sources'][0]
        source.pop('observed_at'); source.pop('canonical_ref')
        legacy['topics'][0]['sources'] = [source]
        lib.atomic_json(self.work / 'library.json', legacy)
        before = (self.work / 'library.json').read_bytes()
        self.assertIn('| 1 | 1 | 未记录 |', lib.render(self.work))
        self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_old_selection_never_attaches_to_topics_created_after_feedback(self):
        with patch.object(lib, 'now', return_value='2026-10-22T10:00:00+08:00'):
            lib.merge(self.work, [sample(), sample('pricing')])
        tid = self.data()['topics'][0]['id']
        quote = '【合成选择】选这条，其他先留着'
        with patch.object(lib, 'now', return_value='2026-10-22T11:00:00+08:00'):
            event = lib.feedback(self.work, tid, quote, '已选', '核心问题')
        history = copy.deepcopy(self.data()['feedback'])
        with patch.object(lib, 'now', return_value='2026-10-22T12:00:00+08:00'):
            lib.merge(self.work, [sample('new-presentation', '【合成新题】展示方案如何解释选择依据')])
        old, other, new = self.data()['topics']
        self.assertEqual(old['feedback'], [event])
        self.assertEqual(old['status'], '已选')
        self.assertEqual(other['status'], '储备题')
        self.assertEqual(new['status'], '储备题')
        self.assertEqual(new['feedback'], [])
        self.assertGreater(new['created_at'], event['at'])
        self.assertEqual(self.data()['feedback'], history)
        self.assertEqual(lib.render(self.work).count(quote), 1)

    def test_import_cannot_infer_publishing_and_feedback_can_keep_state(self):
        item = sample(); item['status'] = '已发布'
        with self.assertRaises(ValueError):
            lib.merge(self.work, [item])
        lib.merge(self.work, [sample()])
        topic_id = self.data()['topics'][0]['id']
        lib.feedback(self.work, topic_id, '只是觉得表达方式有启发')
        self.assertEqual(self.data()['topics'][0]['status'], '储备题')

    def test_merge_preserves_research_state_even_with_different_incoming_status(self):
        lib.merge(self.work, [sample()])
        update = sample(); update['status'] = '优先备选'
        lib.merge(self.work, [update])
        self.assertEqual(self.data()['topics'][0]['status'], '储备题')

    def test_default_import_cannot_promote_or_downgrade_any_old_research_state(self):
        for state in lib.IMPORT_STATES:
            first = sample('state-' + state); first['status'] = state
            lib.merge(self.work, [first])
            incoming = sample('state-' + state)
            incoming['sources'][0]['ref'] = 'https://example.invalid/extra/' + state
            lib.merge(self.work, [incoming])
            topic = next(t for t in self.data()['topics'] if t['question_key'] == first['question_key'])
            self.assertEqual(topic['status'], state)
            self.assertEqual(len(topic['sources']), 2)

    def test_explicit_reassessment_keeps_original_content_and_audits_the_change(self):
        first = sample(); first['status'] = '待补角度'; lib.merge(self.work, [first])
        before = self.data()['topics'][0]
        event = lib.reassess(self.work, before['id'], '待补角度', '储备题', '合成复评：已将泛角度具体化，实际采用仍未知')
        after = self.data()['topics'][0]
        self.assertEqual(after['status'], '储备题')
        for field in ('topic', 'sources', 'feedback', 'question_key', 'recall_prompt'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['status_reviews'], [event])
        self.assertTrue(event['id'].startswith('REV-'))
        self.assertIn(event['id'], lib.render(self.work))
        self.assertEqual(event['previous_status'], '待补角度')
        self.assertIn(event['reason'], lib.render(self.work))
        self.assertEqual(self.data()['feedback'], [])
        # Later source imports cannot silently undo an explicit review.
        lib.merge(self.work, [first])
        self.assertEqual(self.data()['topics'][0]['status'], '储备题')
        lib.reassess(self.work, before['id'], '储备题', '待补证据', '合成复评：来源证据不足')
        self.assertEqual(len(self.data()['topics'][0]['status_reviews']), 2)

    def test_invalid_or_stale_reassessment_cannot_partially_write(self):
        lib.merge(self.work, [sample()]); tid = self.data()['topics'][0]['id']
        before = (self.work / 'library.json').read_bytes()
        for args in [(tid, '待补角度', '优先备选', '过期状态'),
                     (tid, '储备题', '优先备选', '  '),
                     ('missing', '储备题', '优先备选', '找不到对象'),
                     (tid, '储备题', '已选', '研究不能代选')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                lib.reassess(self.work, *args)
            self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_reassessment_cannot_override_user_feedback_or_user_states(self):
        lib.merge(self.work, [sample()]); tid = self.data()['topics'][0]['id']
        lib.feedback(self.work, tid, '【模拟反馈】只喜欢提问题的方式', scope='改写')
        before = (self.work / 'library.json').read_bytes()
        with self.assertRaises(ValueError):
            lib.reassess(self.work, tid, '储备题', '优先备选', '研究者想改')
        self.assertEqual((self.work / 'library.json').read_bytes(), before)
        for state in ('已选', '已拍', '已发布', '暂缓', '不再使用'):
            lib.feedback(self.work, tid, '【模拟事实】' + state, state)
            before = (self.work / 'library.json').read_bytes()
            with self.assertRaises(ValueError):
                lib.reassess(self.work, tid, state, '储备题', '研究不能重置')
            self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_rewrite_feedback_stays_visible_without_rejecting_problem(self):
        lib.merge(self.work, [sample()])
        before = self.data()['topics'][0]
        quote = '原问题有启发，只是工具教程的改写我不想拍'
        lib.feedback(self.work, before['id'], quote, scope='改写')
        update = sample(title='新的改写'); update['status'] = '优先备选'
        lib.merge(self.work, [update])
        after = self.data()['topics'][0]
        for field in ('id', 'question_key', 'topic', 'status', 'sources'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['feedback'][0]['scope'], '改写')
        self.assertIn(quote, lib.render(self.work))

    def test_round_feedback_is_independent_of_topic_and_profile(self):
        lib.merge(self.work, [sample(), sample('capacity', '接多少项目合适')])
        topics = copy.deepcopy(self.data()['topics'])
        profile = (self.work / 'profile.json').read_bytes()
        event = lib.feedback(self.work, None, '下一轮多找沟通，这轮先不讲工具', scope='本轮')
        self.assertIsNone(event['topic_id'])
        self.assertIsNone(event['status'])
        self.assertEqual(self.data()['topics'], topics)
        self.assertEqual((self.work / 'profile.json').read_bytes(), profile)
        self.assertIn(event['quote'], lib.render(self.work))
        lib.feedback(self.work, None, '以后都不讲编程教程', scope='长期偏好')
        self.assertEqual((self.work / 'profile.json').read_bytes(), profile)

    def test_invalid_feedback_scope_and_broad_status_leave_file_unchanged(self):
        lib.merge(self.work, [sample()])
        tid = self.data()['topics'][0]['id']
        before = (self.work / 'library.json').read_bytes()
        for args in [(None, '已选', '本轮'), (tid, '不再使用', '改写'),
                     (None, None, None), (None, None, '核心问题'),
                     ('missing', None, '本轮'), (tid, None, 'unknown')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                lib.feedback(self.work, args[0], '测试原话', args[1], args[2])
            self.assertEqual((self.work / 'library.json').read_bytes(), before)

    def test_export_includes_legacy_topic_only_feedback_without_inventing_scope(self):
        lib.merge(self.work, [sample()])
        data = self.data()
        data['topics'][0]['feedback'] = [{'quote': '方向对了，但没有选题'}]
        lib.atomic_json(self.work / 'library.json', data)
        before = (self.work / 'library.json').read_bytes()
        exported = lib.render(self.work)
        self.assertIn('方向对了，但没有选题', exported)
        self.assertIn('未标注，按原话核对', exported)
        self.assertEqual((self.work / 'library.json').read_bytes(), before)
        self.assertEqual(self.data()['topics'][0]['status'], '储备题')

    def test_feedback_quotes_are_rendered_once_and_escaped(self):
        lib.merge(self.work, [sample()])
        tid = self.data()['topics'][0]['id']
        lib.feedback(self.work, tid, '只借题面 | 先留着\n还没选', scope='改写')
        exported = lib.render(self.work)
        self.assertEqual(exported.count('只借题面 ／ 先留着 还没选'), 1)

    def test_cli_round_feedback_and_export_from_extracted_package(self):
        out = Path(self.temp.name) / 'cli-release.zip'
        release.build(out)
        with zipfile.ZipFile(out) as archive:
            archive.extractall(Path(self.temp.name) / 'cli-package')
        script = Path(self.temp.name) / 'cli-package/hanxu-video-topics/scripts/topic_library.py'
        user = Path(self.temp.name) / 'cli-user'
        def run(*args):
            return subprocess.run([sys.executable, str(script), '--workspace', str(user), *args],
                                  capture_output=True, text=True)
        self.assertEqual(run('init').returncode, 0)
        result = run('feedback', '--scope', '本轮', '--quote', '本轮只整理给定材料')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)['topic_id'])
        self.assertEqual(run('export').returncode, 0)
        self.assertIn('本轮只整理给定材料', (user / '选题库.md').read_text())
        before = (user / 'library.json').read_bytes()
        self.assertEqual(run('feedback', '--scope', '本轮', '--quote', '测试', '--status', '已选').returncode, 2)
        self.assertEqual((user / 'library.json').read_bytes(), before)

    def test_source_query_identity_and_markdown_export(self):
        self.assertEqual(lib.canonical('https://example.invalid/watch?id=1&utm_source=x#top'),
                         'https://example.invalid/watch?id=1')
        self.assertNotEqual(lib.canonical('https://example.invalid/watch?id=1'),
                            lib.canonical('https://example.invalid/watch?id=2'))
        item = sample(title='修改 | 报价\n怎么谈')
        lib.merge(self.work, [item])
        self.assertIn('修改 ／ 报价 怎么谈', lib.render(self.work))

    def test_extracted_cli_requires_a_separate_explicit_review(self):
        out = Path(self.temp.name) / 'review-release.zip'; release.build(out)
        with zipfile.ZipFile(out) as archive:
            archive.extractall(Path(self.temp.name) / 'review-package')
        script = Path(self.temp.name) / 'review-package/hanxu-video-topics/scripts/topic_library.py'
        user = Path(self.temp.name) / 'review-user'
        def run(*args):
            return subprocess.run([sys.executable, str(script), '--workspace', str(user), *args],
                                  capture_output=True, text=True)
        self.assertEqual(run('init').returncode, 0)
        item = sample(); item['status'] = '待补角度'
        batch = Path(self.temp.name) / 'batch.json'; lib.atomic_json(batch, [item])
        self.assertEqual(run('merge', '--input', str(batch)).returncode, 0)
        tid = json.loads((user / 'library.json').read_text())['topics'][0]['id']
        item.pop('status'); lib.atomic_json(batch, [item])
        self.assertEqual(run('merge', '--input', str(batch)).returncode, 0)
        self.assertEqual(json.loads((user / 'library.json').read_text())['topics'][0]['status'], '待补角度')
        before = (user / 'library.json').read_bytes()
        missing_reason = run('reassess', '--id', tid, '--from-status', '待补角度', '--status', '储备题')
        self.assertEqual(missing_reason.returncode, 2)
        self.assertEqual((user / 'library.json').read_bytes(), before)
        result = run('reassess', '--id', tid, '--from-status', '待补角度', '--status', '储备题', '--reason', '合成复评：补足具体角度')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['previous_status'], '待补角度')
        self.assertEqual(run('export').returncode, 0)
        self.assertIn('合成复评：补足具体角度', (user / '选题库.md').read_text())

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
