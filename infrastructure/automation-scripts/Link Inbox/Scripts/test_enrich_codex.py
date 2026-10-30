"""Regression checks for untrusted-source Codex enrichment."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import enrich_note_llm as enrich
from external_resource_note import build_auto_note

class EnrichTests(unittest.TestCase):
    def test_legacy_model_and_untrusted_source_isolation(self):
        completed = SimpleNamespace(returncode=0, stdout='{"summary":["ok"]}', stderr='')
        with patch.object(enrich.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(enrich.call_codex('untrusted source', 'claude-haiku-4-5-20251001')['summary'], ['ok'])
        cmd = run.call_args.args[0]
        self.assertIn('--ignore-user-config', cmd)
        self.assertIn('shell_tool', cmd)
        self.assertIn('unified_exec', cmd)
        self.assertIn('apps', cmd)
        self.assertIn('web_search="disabled"', cmd)
        self.assertNotIn('--model', cmd)
        self.assertEqual(run.call_args.kwargs['input'], 'untrusted source')

    def test_note_enrichment_preserves_transcript(self):
        data = {'summary':['Итог'], 'essence':'Мысль', 'links':[], 'tools':[], 'insights':[], 'solutions':[], 'anton_relevance':'—', 'strategic':'not applicable'}
        transcript = 'Тестовый материал о том, как готовить встречи, записывать решения и назначать ответственных участников команды.'
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'note.md'
            path.write_text(build_auto_note({'url':'https://example.com/test','title':'Тест'}, '# Тест\n\n## Transcript\n'+transcript))
            with patch.object(enrich, 'call_codex', return_value=data):
                self.assertEqual(enrich.enrich_one(path), 'done')
            result=path.read_text()
            self.assertIn('enrichment: done', result)
            self.assertIn(transcript, result)
            self.assertIn('Итог', result)

if __name__ == '__main__':
    unittest.main()
