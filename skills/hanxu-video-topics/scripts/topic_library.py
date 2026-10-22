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
SCOPES = {'metadata_only', 'user_statement', 'article_text'}


def now():
    return datetime.now(timezone.utc).isoformat()


def workspace(value):
    path = Path(value).expanduser().resolve()
    if path == PACKAGE_ROOT or PACKAGE_ROOT in path.parents:
        raise ValueError('用户工作目录必须在技能安装包及维护仓库之外')
    return path


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


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
        if previous['status'] in IMPORT_STATES and not previous.get('feedback'):
            previous['status'] = item['status']
        # Enrich a legacy record without inventing a story or changing its choice.
        if not previous.get('recall_prompt'):
            previous['recall_prompt'] = item['recall_prompt']
        # Existing user decisions and wording survive incoming research.
        for field in ('topic', 'inspiration', 'recall_prompt', 'missing', 'interest_judgment'):
            if field in item and item[field] != previous.get(field):
                observation = {'field': field, 'value': item[field]}
                if observation not in previous.setdefault('supplements', []):
                    previous['supplements'].append(observation)
        (updated if previous != before else unchanged).append(previous['id'])
    atomic_json(root / 'library.json', result)
    return {'added_ids': sorted(set(added)), 'updated_ids': sorted(set(updated) - set(added)),
            'unchanged_ids': sorted(set(unchanged) - set(added) - set(updated)),
            'total_topics': len(result['topics'])}


def feedback(root, topic_id, quote, state=None):
    root, data = load(root)
    quote = require_text(quote, '用户反馈原话')
    if state is not None and state not in FEEDBACK_STATES:
        raise ValueError('无效反馈状态')
    topic = next((item for item in data['topics'] if item['id'] == topic_id), None)
    if topic is None:
        raise ValueError('题目ID不存在')
    event = {'at': now(), 'topic_id': topic_id, 'quote': quote,
             'previous_status': topic['status'], 'status': state or topic['status']}
    topic.setdefault('feedback', []).append(event)
    data['feedback'].append(event)
    if state:
        topic['status'] = state
    atomic_json(root / 'library.json', data)
    return event


def clean_cell(value):
    return str(value).replace('|', '／').replace('\n', ' ').replace('\r', ' ')


def render(root):
    _, data = load(root)
    lines = ['# 我的短视频灵感选题库', '', '| ID | 备选题 | 给我的启发 | 可以回想哪件事 | 状态 | 来源数 |',
             '|---|---|---|---|---|---|']
    for item in data['topics']:
        identities = {(s['platform'], s['canonical_ref']) for s in item['sources']}
        lines.append('| ' + ' | '.join(clean_cell(v) for v in
                     (item['id'], item['topic'], item['inspiration'],
                      item.get('recall_prompt') or '待补回想提示', item['status'], len(identities))) + ' |')
    lines.extend(['', '完整原题、观察时间、未知字段与反馈见同目录library.json。', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    p = sub.add_parser('merge'); p.add_argument('--input', required=True)
    p = sub.add_parser('feedback'); p.add_argument('--id', required=True)
    p.add_argument('--quote', required=True); p.add_argument('--status', choices=sorted(FEEDBACK_STATES))
    sub.add_parser('list'); sub.add_parser('export')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = init(args.workspace)
        elif args.command == 'merge':
            result = merge(args.workspace, read_json(Path(args.input)))
        elif args.command == 'feedback':
            result = feedback(args.workspace, args.id, args.quote, args.status)
        elif args.command == 'export':
            root = workspace(args.workspace)
            path = root / '选题库.md'
            path.write_text(render(root), encoding='utf-8')
            result = {'exported': str(path), 'note': '展示视图，唯一维护源仍为library.json'}
        else:
            result = load(args.workspace)[1]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
