#!/usr/bin/env python3
"""Local topic-library storage. No network, scoring, or semantic classification."""
import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = SKILL_ROOT.parent.parent if SKILL_ROOT.parent.name == 'skills' else SKILL_ROOT
IMPORT_STATES = {'储备题', '待补证据', '待补角度', '只借题面', '优先备选'}
FEEDBACK_STATES = IMPORT_STATES | {'已选', '已拍', '已发布', '暂缓', '不再使用'}
FEEDBACK_SCOPES = {'改写', '核心问题', '本轮', '长期偏好'}
SCOPES = {'metadata_only', 'user_statement', 'article_text'}


def now():
    return datetime.now(timezone.utc).isoformat()


def event_id(prefix, value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return prefix + '-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]


def source_statistics(topic):
    sources = topic.get('sources', [])
    identities = {(s['platform'], s.get('canonical_ref') or canonical(s['ref'])) for s in sources}
    dated = []
    for source in sources:
        value = source.get('observed_at')
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is not None:
                dated.append((stamp, value))
        except (AttributeError, TypeError, ValueError):
            pass  # Legacy unknown dates remain unknown; do not substitute import time.
    latest = max(dated, key=lambda pair: pair[0])[1] if dated else None
    return {'source_observations': len(sources), 'unique_sources': len(identities),
            'latest_observed_at': latest}


def supplement(topic, field, value, sources):
    # These are the observations supplied with the suggestion, not new web reads.
    references = sorted(
        [dict(platform=s['platform'], ref=s['canonical_ref'], observed_at=s['observed_at'])
         for s in sources], key=lambda s: (s['platform'], s['ref'], s['observed_at']))
    events = topic.setdefault('supplements', [])
    for old in events:
        if old.get('field') == field and old.get('value') == value:
            if 'source_observations' not in old or old['source_observations'] == references:
                return  # Keep legacy evidence gaps and make identical reimports idempotent.
    event = {'topic_id': topic['id'], 'field': field, 'value': value,
             'source_observations': references}
    event['id'] = event_id('SUP', event)
    event['at'] = now()
    events.append(event)


def workspace(value):
    path = Path(value).expanduser().resolve()
    if path == PACKAGE_ROOT or PACKAGE_ROOT in path.parents:
        raise ValueError('用户工作目录必须在技能安装包及维护仓库之外')
    return path


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_report(root, input_path, name):
    """Save UTF-8 data from a file, never execute report text as Python code."""
    root = workspace(root)
    if not name or name in {'.', '..'} or '/' in name or '\\' in name or '\x00' in name:
        raise ValueError('report name must be a single filename stem')
    payload = json.loads(Path(input_path).read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict):
        raise ValueError('report payload must be an object')
    markdown, record = payload.get('markdown'), payload.get('record')
    if not isinstance(markdown, str) or not markdown.strip() or not isinstance(record, dict):
        raise ValueError('report payload requires nonempty markdown and an object record')
    # Serialize and validate both before creating any output file.
    report_bytes = markdown.encode('utf-8')
    record_bytes = (json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if json.loads(record_bytes) != record:
        raise ValueError('record is not round-trip JSON data')
    output = root / 'outputs'
    if output.is_symlink():
        raise ValueError('outputs must be an ordinary directory')
    targets = [(output / (name + '.md'), report_bytes),
               (output / (name + '.json'), record_bytes)]
    if any(path.exists() or path.is_symlink() for path, _ in targets):
        raise ValueError('report output exists; use a new name, previous files are preserved')
    output.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, content in targets:
            with path.open('xb') as stream:
                created.append(path)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != content:
                raise OSError('report read-back differs from submitted bytes')
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {'markdown': str(targets[0][0]), 'record': str(targets[1][0]),
            'saved_at': now(), 'verified': True,
            'sha256': {path.suffix[1:]: hashlib.sha256(content).hexdigest()
                       for path, content in targets}}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' 必须是非空字符串')
    return value.strip()


def count(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('互动数不能是布尔值')
    text = str(value).replace(',', '').strip()
    unit = 1
    if text.endswith(('万', 'w', 'W')):
        text, unit = text[:-1], 10000
    elif text.endswith(('亿',)):
        text, unit = text[:-1], 100000000
    try:
        number = Decimal(text) * unit
    except InvalidOperation as exc:
        raise ValueError('互动数应为非负数字或null') from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError('互动数应为非负整数或null')
    return int(number)


def canonical(ref):
    parts = urlsplit(ref)
    if parts.scheme not in ('https', 'http'):
        return ref
    ignored = {'xsec_token', 'xsec_source', 'share_token', 'share_from'}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ignored]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'),
                       urlencode(sorted(query)), ''))


def normalize_topic(item):
    if not isinstance(item, dict):
        raise ValueError('每个选题必须是对象')
    item = copy.deepcopy(item)
    for key in ('question_key', 'topic', 'inspiration', 'recall_prompt', 'audience'):
        item[key] = require_text(item.get(key), key)
    item['question_key'] = ' '.join(item['question_key'].casefold().split())
    item['status'] = item.get('status', '储备题')
    if item['status'] not in IMPORT_STATES:
        raise ValueError('导入不得推断用户选择、拍摄或发布；请用feedback保存明确事实')
    sources = item.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('入库须有真实可回读来源，纯联想另留运行记录')
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('来源必须是对象')
        for key in ('platform', 'ref', 'raw_title', 'observed_at', 'evidence_scope'):
            source[key] = require_text(source.get(key), 'source.' + key)
        if source['evidence_scope'] not in SCOPES:
            raise ValueError('来源读取范围无效')
        try:
            stamp = datetime.fromisoformat(source['observed_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError()
        except ValueError as exc:
            raise ValueError('observed_at需实际带时区的ISO时间') from exc
        source['canonical_ref'] = canonical(source['ref'])
        metrics = source.get('metrics', {})
        if not isinstance(metrics, dict):
            raise ValueError('metrics必须为对象')
        for key in ('likes', 'comments', 'saves', 'shares', 'followers', 'views'):
            metrics[key] = count(metrics.get(key))
        source['metrics'] = metrics
    return item


def init(root):
    root = workspace(root)
    if (root / 'profile.json').exists() or (root / 'library.json').exists():
        raise ValueError('配置或库已存在，初始化不会覆盖')
    profile = read_json(SKILL_ROOT / 'references/profile-template.json')
    atomic_json(root / 'profile.json', profile)
    atomic_json(root / 'library.json', {'schema_version': 1, 'topics': [], 'feedback': []})
    return {'workspace': str(root), 'created': ['profile.json', 'library.json']}


def load(root):
    root = workspace(root)
    data = read_json(root / 'library.json')
    if data.get('schema_version') != 1 or not isinstance(data.get('topics'), list):
        raise ValueError('不支持的题库格式')
    return root, data


def merge(root, items):
    root, data = load(root)
    if not isinstance(items, list):
        raise ValueError('导入文件应为选题数组')
    # Validate entire batch before writing anything; preserve every source observation.
    items = [normalize_topic(item) for item in items]
    result = copy.deepcopy(data)
    index = {item['question_key']: item for item in result['topics']}
    added, updated, unchanged = [], [], []
    for item in items:
        key = item['question_key']
        previous = index.get(key)
        if previous is None:
            item['id'] = 'T-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]
            item['created_at'] = now()
            item['feedback'] = []
            result['topics'].append(item)
            index[key] = item
            added.append(item['id'])
            continue
        before = copy.deepcopy(previous)
        for source in item['sources']:
            if source not in previous['sources']:
                previous['sources'].append(source)
        # Adding evidence never reclassifies an existing topic. A research-state
        # change must use reassess with an explicit expected state and reason.
        # Enrich a legacy record without inventing a story or changing its choice.
        if not previous.get('recall_prompt'):
            previous['recall_prompt'] = item['recall_prompt']
        # Existing user decisions and wording survive incoming research.
        for field in ('topic', 'inspiration', 'recall_prompt', 'missing', 'interest_judgment'):
            if field in item and item[field] != previous.get(field):
                supplement(previous, field, item[field], item['sources'])
        (updated if previous != before else unchanged).append(previous['id'])
    atomic_json(root / 'library.json', result)
    return {'added_ids': sorted(set(added)), 'updated_ids': sorted(set(updated) - set(added)),
            'unchanged_ids': sorted(set(unchanged) - set(added) - set(updated)),
            'total_topics': len(result['topics'])}


def reassess(root, topic_id, expected_state, state, reason):
    """Explicitly review an unchosen research state, preserving user decisions."""
    root, data = load(root)
    reason = require_text(reason, '复评依据')
    if expected_state not in IMPORT_STATES or state not in IMPORT_STATES:
        raise ValueError('研究复评仅限研究状态；选择、拍摄、发布或停用须用feedback记录明确事实')
    topic = next((item for item in data['topics'] if item['id'] == topic_id), None)
    if topic is None:
        raise ValueError('题目ID不存在')
    if topic['status'] != expected_state:
        raise ValueError('当前状态与预期不符，请重新读回题库后复评')
    if topic.get('feedback'):
        raise ValueError('该题已有用户反馈，研究复评不覆盖用户决定；请核对原话并用feedback记录明确变化')
    event = {'at': now(), 'topic_id': topic_id, 'previous_status': topic['status'],
             'status': state, 'reason': reason}
    event['id'] = event_id('REV', event)
    topic['status'] = state
    topic.setdefault('status_reviews', []).append(event)
    atomic_json(root / 'library.json', data)
    return event


def feedback(root, topic_id, quote, state=None, scope=None):
    root, data = load(root)
    quote = require_text(quote, '用户反馈原话')
    if state is not None and state not in FEEDBACK_STATES:
        raise ValueError('无效反馈状态')
    if scope is not None and scope not in FEEDBACK_SCOPES:
        raise ValueError('无效反馈范围')
    if scope == '改写' and state is not None:
        raise ValueError('改写反馈不改变整题状态；明确选题或停用请另记对应反馈')
    topic = next((item for item in data['topics'] if item['id'] == topic_id), None) if topic_id else None
    if topic_id and topic is None:
        raise ValueError('题目ID不存在')
    if topic is None and (scope not in {'本轮', '长期偏好'} or state is not None):
        raise ValueError('无题目ID时只能记录本轮或长期偏好，不能改变题目状态')
    event = {'at': now(), 'topic_id': topic_id, 'quote': quote,
             'previous_status': topic['status'] if topic else None,
             'status': (state or topic['status']) if topic else None}
    if scope is not None:
        event['scope'] = scope
    event['id'] = event_id('FB', event)
    if topic is not None:
        topic.setdefault('feedback', []).append(event)
    data.setdefault('feedback', []).append(event)
    if state and topic is not None:
        topic['status'] = state
    atomic_json(root / 'library.json', data)
    return event


def index_events(root):
    """Explicitly assign IDs to legacy events; do not infer historical metadata."""
    root, data = load(root)
    result = copy.deepcopy(data)
    assigned_at = now()
    assigned = 0
    feedback_ids = {}
    metadata = {'id', 'id_origin', 'id_assigned_at'}

    def payload(event):
        return {k: v for k, v in event.items() if k not in metadata}

    def assign(event, prefix, identity):
        nonlocal assigned
        if event.get('id'):
            return
        event['id'] = event_id(prefix, identity)
        event['id_origin'] = 'indexed_legacy'
        event['id_assigned_at'] = assigned_at
        assigned += 1

    # Topic/global feedback entries can be mirrors of one logical event.
    feedback_events = list(result.get('feedback', []))
    feedback_events.extend(e for t in result['topics'] for e in t.get('feedback', []))
    for event in feedback_events:
        key = json.dumps(payload(event), ensure_ascii=False, sort_keys=True)
        if event.get('id'):
            feedback_ids.setdefault(key, {k: event[k] for k in metadata if k in event})
    for event in feedback_events:
        if event.get('id'):
            continue
        key = json.dumps(payload(event), ensure_ascii=False, sort_keys=True)
        if key in feedback_ids:
            event.update(feedback_ids[key])
        else:
            assign(event, 'FB', payload(event))
            feedback_ids[key] = {k: event[k] for k in metadata if k in event}
    for topic in result['topics']:
        for field, prefix in (('supplements', 'SUP'), ('status_reviews', 'REV')):
            for i, event in enumerate(topic.get(field, [])):
                assign(event, prefix, {'topic_id': topic['id'], 'field': field,
                                       'index': i, 'event': payload(event)})
    if data == result:
        return {'assigned_events': 0, 'backup': None}
    digest = hashlib.sha256((root / 'library.json').read_bytes()).hexdigest()[:16]
    backup = root / ('library.before-event-index-' + digest + '.json')
    original = (root / 'library.json').read_bytes()
    # An existing exact backup is safe; a conflict must not be overwritten.
    if backup.exists():
        if backup.read_bytes() != original:
            raise ValueError('历史备份存在且内容不符，未修改题库')
    else:
        with backup.open('xb') as stream:
            stream.write(original)
    atomic_json(root / 'library.json', result)
    return {'assigned_events': assigned, 'backup': str(backup),
            'note': '只补发记录标识与分配时间；未补历史事件时间或来源关联'}


def clean_cell(value):
    return str(value).replace('|', '／').replace('\n', ' ').replace('\r', ' ')


def render(root):
    _, data = load(root)
    lines = ['# 我的短视频灵感选题库', '',
             '| ID | 备选题 | 给我的启发 | 可以回想哪件事 | 状态 | 来源观察次数 | 去重作品数 | 最近来源观察时间 |',
             '|---|---|---|---|---|---|---|---|']
    for item in data['topics']:
        stats = source_statistics(item)
        lines.append('| ' + ' | '.join(clean_cell(v) for v in
                     (item['id'], item['topic'], item['inspiration'],
                      item.get('recall_prompt') or '待补回想提示', item['status'],
                      stats['source_observations'], stats['unique_sources'],
                      stats['latest_observed_at'] or '未记录')) + ' |')
    supplements = [(item['id'], event) for item in data['topics'] for event in item.get('supplements', [])]
    if supplements:
        lines.extend(['', '## 补充角度与提示（研究建议，非用户选择）', '',
                      '原题与原提示保留；记录时间与来源观察时间分开，旧记录缺项不补造。来源关联表示随建议提交的材料，不证明建议有效。', '',
                      '| 事件ID | 记录时间 | 题目 | 补充字段 | 新建议 | 关联来源观察 |',
                      '|---|---|---|---|---|---|'])
        for topic_id, event in supplements:
            references = event.get('source_observations', [])
            basis = '；'.join(s['platform'] + ' ' + s['ref'] + '（观察 ' + s['observed_at'] + '）'
                             for s in references) or '未记录'
            value = event.get('value')
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            lines.append('| ' + ' | '.join(clean_cell(v) for v in
                         (event.get('id') or '未记录', event.get('at') or '未记录', topic_id,
                          event.get('field') or '未记录', value, basis)) + ' |')
    # Old libraries may keep feedback only on a topic. Do not lose those quotes
    # or infer their scope when generating a continuation view.
    events = list(data.get('feedback', []))
    for item in data['topics']:
        for event in item.get('feedback', []):
            if event not in events:
                events.append(dict(event, topic_id=event.get('topic_id') or item['id']))
    if events:
        lines.extend(['', '## 反馈与下轮接续', '',
                      '以下保留历史原话；范围未标注或新旧反馈冲突时，按原话核对，不自动扩大为长期排除。', '',
                      '选择只对应明确对象；“其他先留着”不为反馈之后新增的题提供选择、否定或背书。长期偏好另按原话范围判断。', '',
                      '| 事件ID | 时间 | 对应题目 | 适用范围 | 用户原话 | 记录后状态 |',
                      '|---|---|---|---|---|---|'])
        for event in events:
            lines.append('| ' + ' | '.join(clean_cell(v) for v in
                         (event.get('id') or '未记录', event.get('at') or '未记录', event.get('topic_id') or '整轮／配置',
                          event.get('scope') or '未标注，按原话核对', event['quote'],
                          event.get('status') or '不改变题目状态')) + ' |')
    reviews = [event for item in data['topics'] for event in item.get('status_reviews', [])]
    if reviews:
        lines.extend(['', '## 明确复评记录', '',
                      '| 事件ID | 时间 | 题目 | 原状态 | 复评状态 | 复评依据（非用户反馈） |',
                      '|---|---|---|---|---|---|'])
        for event in reviews:
            lines.append('| ' + ' | '.join(clean_cell(event.get(key) or '未记录') for key in
                         ('id', 'at', 'topic_id', 'previous_status', 'status', 'reason')) + ' |')
    lines.extend(['', '完整原题、来源观察、补充角度、未知字段、反馈与复评见同目录library.json；背景与适用范围见profile.json。', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    p = sub.add_parser('merge'); p.add_argument('--input', required=True)
    p = sub.add_parser('reassess'); p.add_argument('--id', required=True)
    p.add_argument('--from-status', choices=sorted(IMPORT_STATES), required=True)
    p.add_argument('--status', choices=sorted(IMPORT_STATES), required=True)
    p.add_argument('--reason', required=True)
    p = sub.add_parser('feedback'); p.add_argument('--id')
    p.add_argument('--quote', required=True); p.add_argument('--status', choices=sorted(FEEDBACK_STATES))
    p.add_argument('--scope', choices=sorted(FEEDBACK_SCOPES))
    p = sub.add_parser('save-report'); p.add_argument('--input', required=True)
    p.add_argument('--name', required=True)
    sub.add_parser('list'); sub.add_parser('export'); sub.add_parser('index-events')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = init(args.workspace)
        elif args.command == 'merge':
            result = merge(args.workspace, read_json(Path(args.input)))
        elif args.command == 'reassess':
            result = reassess(args.workspace, args.id, args.from_status, args.status, args.reason)
        elif args.command == 'feedback':
            result = feedback(args.workspace, args.id, args.quote, args.status, args.scope)
        elif args.command == 'index-events':
            result = index_events(args.workspace)
        elif args.command == 'save-report':
            result = save_report(args.workspace, args.input, args.name)
        elif args.command == 'export':
            root = workspace(args.workspace)
            path = root / '选题库.md'
            path.write_text(render(root), encoding='utf-8')
            result = {'exported': str(path), 'note': '展示视图，唯一维护源仍为library.json'}
        else:
            result = load(args.workspace)[1]
        print(json.dumps(result, ensure_ascii=args.command == 'save-report', indent=2))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
