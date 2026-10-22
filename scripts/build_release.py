#!/usr/bin/env python3
"""Build an installable skill ZIP from an explicit source-to-archive allowlist."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {name: name for name in ('README.md', 'INSTALL.md', 'USAGE.md')}
for name in ('SKILL.md', 'LICENSE', 'agents/openai.yaml', 'references/profile.md',
             'references/profile-template.json', 'references/discovery.md',
             'references/quality.md', 'references/output.md', 'references/tools.md',
             'scripts/topic_library.py'):
    FILES['skills/hanxu-video-topics/' + name] = name


def read_public_file(relative):
    path = ROOT / relative
    if path.is_symlink() or ROOT not in path.resolve().parents:
        raise ValueError('release source must be an ordinary package file: ' + relative)
    result = path.read_bytes()
    if relative in ('README.md', 'INSTALL.md', 'USAGE.md'):
        result = result.replace(b'](skills/hanxu-video-topics/', b'](')
    return result


def build(output):
    output = Path(output).expanduser().resolve()
    # Read every source before creating output; reject links to private files.
    content = []
    for relative, target in FILES.items():
        content.append((target, read_public_file(relative)))
    if output.exists():
        raise ValueError('output already exists; choose a new path instead of overwriting')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, payload in content:
            entry = zipfile.ZipInfo('hanxu-video-topics/' + relative, (2026, 10, 22, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, payload)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(digest + '  ' + output.name + '\n')
    return {'path': str(output), 'sha256': digest, 'files': len(content)}


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
