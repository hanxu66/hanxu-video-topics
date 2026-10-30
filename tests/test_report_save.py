"""Report saving keeps multilingual content as UTF-8 data, not executable code."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_library import lib, release


class ReportSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'user with spaces'
        lib.init(self.root)
        self.original = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.draft = self.root / 'draft.json'
        self.markdown = '# 成人教学与视觉设计 📝\n' + ('“中文引号” / 品牌设计 / é / 日文 / $HOME / `literal` / \\\\n\n' * 300)
        self.record = {'quote': '客户说“不喜欢”\n下一句怎样追问？', 'unknown': None,
                       'nested': {'模拟': True, '保存次数': 1}}
        self.draft.write_text(json.dumps({'markdown': self.markdown, 'record': self.record},
                                        ensure_ascii=False), encoding='utf-8')

    def test_long_unicode_report_and_json_round_trip_without_library_changes(self):
        result = lib.save_report(self.root, self.draft, '接续报告 r1')
        self.assertTrue(result['verified'])
        self.assertEqual(Path(result['markdown']).read_bytes(), self.markdown.encode('utf-8'))
        self.assertEqual(json.loads(Path(result['record']).read_text(encoding='utf-8')), self.record)
        for name, content in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), content)

    def test_existing_record_preserved_without_publishing_partial_markdown(self):
        output = self.root / 'outputs'; output.mkdir()
        old = output / 'previous.json'; old.write_bytes(b'original history')
        with self.assertRaises(ValueError):
            lib.save_report(self.root, self.draft, 'previous')
        self.assertEqual(old.read_bytes(), b'original history')
        self.assertFalse((output / 'previous.md').exists())

    def test_invalid_utf8_data_is_rejected_without_creating_a_report(self):
        self.draft.write_bytes(b'{"markdown":"\xe6", "record":{}}')
        with self.assertRaises(UnicodeError):
            lib.save_report(self.root, self.draft, 'invalid')
        self.assertFalse((self.root / 'outputs' / 'invalid.md').exists())
        self.assertFalse((self.root / 'outputs' / 'invalid.json').exists())

    def test_invalid_schema_or_filename_cannot_overwrite_inputs(self):
        for name in ['../library', '/library', '..', 'a\\b']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                lib.save_report(self.root, self.draft, name)
        self.draft.write_text('{"markdown":"text", "record":[]}', encoding='utf-8')
        with self.assertRaises(ValueError):
            lib.save_report(self.root, self.draft, 'invalid')
        self.assertFalse((self.root / 'outputs').exists())

    def test_second_output_failure_rolls_back_only_new_files(self):
        original_open = Path.open
        def fail_record(path, *args, **kwargs):
            if path.name == 'new.json' and args and args[0] == 'xb':
                raise OSError('simulated second output failure')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', fail_record), self.assertRaises(OSError):
            lib.save_report(self.root, self.draft, 'new')
        self.assertFalse((self.root / 'outputs' / 'new.md').exists())
        self.assertFalse((self.root / 'outputs' / 'new.json').exists())
        self.assertTrue(self.draft.exists())
        for name, content in self.original.items():
            self.assertEqual((self.root / name).read_bytes(), content)

    def test_extracted_cli_with_ascii_stdout_preserves_utf8_files(self):
        archive = Path(self.tmp.name) / 'candidate.zip'; release.build(archive)
        package = Path(self.tmp.name) / 'extracted'
        with zipfile.ZipFile(archive) as z:
            z.extractall(package)
        script = package / 'hanxu-video-topics/scripts/topic_library.py'
        env = dict(os.environ, PYTHONIOENCODING='ascii')
        result = subprocess.run([sys.executable, str(script), '--workspace', str(self.root),
                                 'save-report', '--input', str(self.draft), '--name', '中文 报告'],
                                env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout.decode('ascii'))
        self.assertEqual(Path(receipt['markdown']).read_text(encoding='utf-8'), self.markdown)
        self.assertEqual(json.loads(Path(receipt['record']).read_text(encoding='utf-8')), self.record)


if __name__ == '__main__':
    unittest.main()
