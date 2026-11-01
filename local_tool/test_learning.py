"""Persistence, source fidelity, file preservation and HTTP integration checks."""
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from media_library import Library
from learning_jobs import LearningService
from learning_text import parse_cues, make_rows, ocr_languages, annotations
from learning_model import ModelClient, _protect
from preview_server import PreviewServer

class LearningChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / 'materials.sqlite3', self.root / 'vault')
        self.source = {'platform': 'BiliBili', 'title': 'English with 中文标题', 'duration': 4,
            'source_url': 'https://www.bilibili.com/video/BV1Demo000001/?vd_source=tracking',
            'preview': {'video': {'url': 'https://cdn.example/secret-stream?token=SIGNED',
                       'headers': {'Cookie': 'PRIVATE_SESSION'}}},
            'subtitles': [
                {'language': 'en', 'ext': 'json', 'automatic': False, 'data': json.dumps({'body': [
                    {'from': 0, 'to': 2, 'content': 'Why is it important to learn?'},
                    {'from': 2, 'to': 4, 'content': 'We should not give up.'}]})},
                {'language': 'zh', 'ext': 'json', 'automatic': False, 'data': json.dumps({'body': [
                    {'from': 0, 'to': 2, 'content': '为什么学习很重要？'},
                    {'from': 2, 'to': 4, 'content': '我们不应该放弃。'}]}, ensure_ascii=False)}]}
        self.material_id = self.library.remember(self.source)['id']

    def tearDown(self):
        self.temp.cleanup()

    def test_history_survives_restart_and_deduplicates(self):
        self.library.classify(self.material_id, True)
        another = {**self.source, 'source_url': 'https://www.bilibili.com/video/BV1Demo000001/'}
        self.assertEqual(self.library.remember(another)['id'], self.material_id)
        reopened = Library(self.library.path, self.library.vault)
        self.assertEqual(len(reopened.list()), 1)
        self.assertTrue(reopened.get(self.material_id)['english'])
        part = {**self.source, 'source_url': 'https://www.bilibili.com/video/BV1Demo000001/?p=2'}
        self.assertNotEqual(reopened.remember(part)['id'], self.material_id)
        raw = self.library.path.read_bytes()
        self.assertNotIn(b'PRIVATE_SESSION', raw); self.assertNotIn(b'SIGNED', raw)

    def test_user_edits_to_generated_note_are_preserved(self):
        self.library.classify(self.material_id, True)
        document = {'rows': [{'id': 'one', 'en': 'Original sentence.', 'zh': '原句。'}], 'origins': []}
        target = self.library.archive_document(self.material_id, document)
        target.write_text('My own study notes', encoding='utf-8')
        document['rows'][0]['zh'] = '新的翻译。'
        regenerated = self.library.archive_document(self.material_id, document)
        self.assertNotEqual(target, regenerated)
        self.assertEqual(target.read_text(encoding='utf-8'), 'My own study notes')
        self.assertIn('新的翻译', regenerated.read_text(encoding='utf-8'))
        self.library.classify(self.material_id, False)
        self.assertTrue(target.exists())

    def test_caption_timestamps_and_no_line_number_alignment(self):
        en = parse_cues('WEBVTT\n\n00:01.000 --> 00:02.000\nHello &amp; welcome.\n', 'vtt')
        self.assertEqual(en[0]['start'], 1)
        self.assertEqual(en[0]['text'], 'Hello & welcome.')
        rows, unmatched = make_rows(en, [{'start': 8, 'end': 9, 'text': '不同时间'}], 'en', 'zh')
        self.assertEqual(rows[0]['zh'], '')
        self.assertEqual(len(unmatched), 1)
        en = [{'start': 0, 'end': 1, 'text': 'First sentence.'}, {'start': 1, 'end': 2, 'text': 'Second sentence.'}]
        rows, _ = make_rows(en, [{'start': 0, 'end': 2, 'text': '中文合成一句。'}], 'en', 'zh')
        self.assertEqual(len(rows), 1);self.assertEqual(rows[0]['alignment'], 'paragraph')
        self.assertIn('Second sentence.', rows[0]['en'])

    def test_bilingual_ocr_keeps_both_languages(self):
        en, zh = ocr_languages([{'start': 1, 'end': 3, 'text': '为什么重要？\nWhy does it matter?'}])
        self.assertEqual(en[0]['text'], 'Why does it matter?')
        self.assertEqual(zh[0]['text'], '为什么重要？')
        self.assertEqual(zh[0]['end'], 3.4)

    def test_original_both_languages_generate_without_any_model(self):
        self.library.classify(self.material_id, True)
        service = LearningService(self.library)
        with patch('learning_jobs.ModelClient', side_effect=AssertionError('Original subtitles must not be translated')):
            service.start(self.source, {'kind': 'ytdlp'})
            deadline = time.monotonic() + 5
            while service.state(self.material_id)['status'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(.02)
        self.assertEqual(service.state(self.material_id)['status'], 'done', service.state(self.material_id))
        document = self.library.document(self.material_id)
        self.assertEqual(len(document['rows']), 2)
        self.assertEqual(document['rows'][1]['zh'], '我们不应该放弃。')
        self.assertIn('平台字幕', document['rows'][0]['zh_origin'])
        self.assertTrue((self.library.folder(self.material_id) / '阅读正文（工具生成）.md').exists())
        self.assertEqual(len(list(self.library.folder(self.material_id).glob('平台原字幕_*'))), 2)
        service.close()

    def test_model_missing_or_reordered_ids_rejected(self):
        model = object.__new__(ModelClient)
        model.complete = lambda *args: '{"translations":[{"id":"other","zh":"中文"}]}'
        with self.assertRaises(ValueError):model.translate([{'id': 'expected', 'en': 'Hello.'}], 'Title')

    def test_reference_word_labels_and_phrases(self):
        with patch('learning_text.wordlists',return_value={'words':{'virtue':{'tags':['tem8'],'meanings':['品德']}},'sources':[]}):
            marks = annotations('We make sense of the competing theories of virtue.')
        self.assertTrue(any(m['word'] == 'make sense of' and m['kind'] == 'phrase' for m in marks))
        self.assertTrue(any(m['kind'] == 'word' and 'tem8' in m['tags'] for m in marks))

    def test_dpapi_roundtrip(self):
        self.assertEqual(_protect(_protect(b'not-a-real-key'), True), b'not-a-real-key')

    def test_reading_http_origin_local_ranges_and_classification(self):
        service = LearningService(self.library)
        video = self.root / 'video.mp4';video.write_bytes(b'0123456789abcdef')
        item = {'kind': 'ytdlp', 'preview': {'video': {'file_path': str(video)}}}
        server = PreviewServer(library=self.library, learning=service)
        address = server.register(self.source, item)
        try:
            with urllib.request.urlopen(address + 'reading') as response:self.assertIn('中文对照', response.read().decode())
            with urllib.request.urlopen(urllib.request.Request(address + 'video', headers={'Range': 'bytes=3-7'})) as response:
                self.assertEqual(response.status, 206);self.assertEqual(response.read(), b'34567')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(address + 'classify', data=b'{"english":true}'))
            self.assertEqual(error.exception.code, 403)
            req = urllib.request.Request(address + 'classify', data=b'{"english":true}', headers={'Origin': server.origin})
            with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['english'])
            with urllib.request.urlopen(address + 'reading-data') as response:self.assertTrue(json.load(response)['material']['english'])
            invalid = urllib.request.Request(address + 'learning-start', data=b'{"roi":[0,0,2,2]}', headers={'Origin': server.origin})
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(invalid)
            self.assertEqual(error.exception.code, 400)
        finally:server.close();service.close()

if __name__ == '__main__':unittest.main(verbosity=2)
