"""Queue, durability and HTTP checks for pre-generated sentence explanations."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from learning_jobs import LearningService
from learning_model import cache_key
from media_library import Library
from preview_server import PreviewServer


class AnalysisChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / 'library.sqlite3', self.root / 'vault')
        self.source = {'platform': 'YouTube', 'title': 'Learning test', 'source_url': 'https://www.youtube.com/watch?v=fixture'}
        self.mid = self.library.remember(self.source)['id']
        self.library.classify(self.mid, True)
        self.library.save_document(self.mid, {'status': 'ready', 'extraction_complete': True, 'origins': [], 'rows': [
            {'id': 'a', 'en': 'We should not give up.', 'zh': '我们不该放弃。'},
            {'id': 'b', 'en': 'It is important to learn.', 'zh': '学习很重要。'},
            {'id': 'c', 'en': 'Practice makes progress.', 'zh': '练习带来进步。'},
        ]})
        self.service = LearningService(self.library)

    def tearDown(self):
        self.service.close()
        self.wait_worker(self.service)
        self.temp.cleanup()

    def wait_worker(self, service=None):
        service = service or self.service
        deadline = time.monotonic() + 5
        while service.analysis_running and time.monotonic() < deadline: time.sleep(.01)
        self.assertFalse(service.analysis_running, 'Analysis worker failed to finish')

    def model(self, analyze=None, enter=None, exit=None):
        stats = SimpleNamespace(loads=0, exits=0, calls=[])
        class FakeModel:
            label = 'test model'
            def __init__(self, log): self.log = log
            def __enter__(self):
                stats.loads += 1
                self.log('正在加载离线学习模型…')
                if enter: enter()
                return self
            def __exit__(self, *args):
                stats.exits += 1
                if exit: exit()
            def analyze(self, sentence, title):
                stats.calls.append(sentence)
                return analyze(sentence) if analyze else '已解析：' + sentence
        return FakeModel, stats

    def test_batch_reuses_model_and_saves_every_sentence(self):
        model, stats = self.model()
        with patch('learning_jobs.ModelClient', model):
            self.service.analyze_all(self.mid); self.wait_worker()
        self.assertEqual((stats.loads, stats.exits, len(stats.calls)), (1, 1, 3))
        self.assertEqual(self.service.state('analysis-batch:' + self.mid)['status'], 'done')
        self.assertTrue(all(row.get('analysis') for row in self.library.document(self.mid)['rows']))
        note = self.library.folder(self.mid) / '阅读正文（工具生成）.md'
        self.assertEqual(note.read_text(encoding='utf-8').count('### 句子解析'), 3)
        self.assertEqual(len(list(note.parent.glob('阅读正文*.md'))), 1)

    def test_batch_preserves_user_notes_and_updates_one_generated_copy(self):
        original = self.library.archive_document(self.mid, self.library.document(self.mid))
        original.write_text('My own study notes', encoding='utf-8')
        model, _ = self.model()
        with patch('learning_jobs.ModelClient', model):
            self.service.analyze_all(self.mid); self.wait_worker()
        self.assertEqual(original.read_text(encoding='utf-8'), 'My own study notes')
        generated = list(original.parent.glob('阅读正文*新生成*.md'))
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].read_text(encoding='utf-8').count('### 句子解析'), 3)

    def test_repeated_batch_and_individual_requests_share_existing_tasks(self):
        started, release = threading.Event(), threading.Event()
        def blocked(sentence):
            if sentence.startswith('We '): started.set(); release.wait(3)
            return '解析：' + sentence
        model, stats = self.model(analyze=blocked)
        with patch('learning_jobs.ModelClient', model):
            try:
                self.service.analyze_all(self.mid); self.assertTrue(started.wait(2))
                self.service.analyze_all(self.mid)
                self.assertEqual(self.service.analyze(self.mid, 'a')['status'], 'running')
                self.assertEqual(self.service.analyze(self.mid, 'b')['status'], 'queued')
                self.assertEqual(self.service.analyze(self.mid, 'b')['status'], 'queued')
                self.assertEqual(self.service.view(self.mid)['analysis_job']['done'], 0)
            finally: release.set(); self.wait_worker()
        self.assertEqual((stats.loads, len(stats.calls)), (1, 3))

    def test_partial_failure_keeps_success_and_retry_only_generates_missing(self):
        def failing(sentence):
            if sentence.startswith('It '): raise ValueError('test inference failure')
            return '解析：' + sentence
        model, _ = self.model(analyze=failing)
        with patch('learning_jobs.ModelClient', model):
            self.service.analyze_all(self.mid); self.wait_worker()
        self.assertEqual(self.service.state('analysis-batch:' + self.mid)['status'], 'error')
        self.assertEqual(sum(bool(row.get('analysis')) for row in self.library.document(self.mid)['rows']), 2)
        retry, stats = self.model()
        with patch('learning_jobs.ModelClient', retry):
            self.service.analyze_all(self.mid); self.wait_worker()
        self.assertEqual(stats.calls, ['It is important to learn.'])

    def test_model_load_failure_is_not_retried_for_every_row(self):
        def unavailable(): raise ValueError('test load failure')
        model, stats = self.model(enter=unavailable)
        with patch('learning_jobs.ModelClient', model):
            self.service.analyze_all(self.mid); self.wait_worker()
        self.assertEqual(stats.loads, 1)
        self.assertEqual(stats.calls, [])
        self.assertEqual(self.service.state('analysis-batch:' + self.mid)['status'], 'error')
        self.assertTrue(all(self.service.analysis_state(self.mid, row)['status'] == 'error' for row in ('a', 'b', 'c')))

    def test_saved_result_available_before_model_unloads(self):
        unloading, release = threading.Event(), threading.Event()
        def exit_model(): unloading.set(); release.wait(3)
        model, _ = self.model(exit=exit_model)
        with patch('learning_jobs.ModelClient', model):
            try:
                self.service.analyze(self.mid, 'a'); self.assertTrue(unloading.wait(2))
                self.assertEqual(self.service.analyze(self.mid, 'a')['status'], 'done')
                self.assertEqual(self.service.analysis_state(self.mid, 'a')['status'], 'done')
            finally: release.set(); self.wait_worker()

    def test_shutdown_then_restart_resumes_only_unfinished_rows(self):
        second, release = threading.Event(), threading.Event()
        def interrupted(sentence):
            if sentence.startswith('It '):
                second.set(); release.wait(3); raise ValueError('interrupted')
            return '解析：' + sentence
        model, _ = self.model(analyze=interrupted)
        with patch('learning_jobs.ModelClient', model):
            try:
                self.service.analyze_all(self.mid); self.assertTrue(second.wait(2))
                self.assertEqual(self.service.analysis_state(self.mid, 'a')['status'], 'done')
                self.service.close()
            finally: release.set(); self.wait_worker()
        self.service = LearningService(Library(self.library.path, self.library.vault))
        self.assertEqual(self.service.state('analysis-batch:' + self.mid)['status'], 'paused')
        retry, stats = self.model()
        with patch('learning_jobs.ModelClient', retry):
            self.service.analyze_all(self.mid); self.wait_worker()
        self.assertEqual(stats.calls, ['It is important to learn.', 'Practice makes progress.'])
        self.assertEqual(self.service.view(self.mid)['analysis_job']['done'], 3)

    def test_completed_article_opens_after_restart_without_model(self):
        model, _ = self.model()
        with patch('learning_jobs.ModelClient', model):
            self.service.analyze_all(self.mid); self.wait_worker()
        reopened = LearningService(Library(self.library.path, self.library.vault))
        try:
            with patch('learning_jobs.ModelClient', side_effect=AssertionError('Must use saved explanations')):
                self.assertEqual(reopened.analyze_all(self.mid)['status'], 'done')
                self.assertEqual(reopened.analyze(self.mid, 'b')['status'], 'done')
                self.assertEqual(reopened.analysis_state(self.mid, 'c')['status'], 'done')
                self.assertFalse(reopened.analysis_running)
        finally: reopened.close()

    def test_existing_cache_restored_to_document_without_model(self):
        row = self.library.document(self.mid)['rows'][0]
        self.library.cache_put(cache_key('analysis', row['en']), {'analysis': 'Saved explanation', 'origin': 'cached'})
        with patch('learning_jobs.ModelClient', side_effect=AssertionError('Cache hit should not load model')):
            self.assertEqual(self.service.analyze(self.mid, 'a')['status'], 'done')
        self.assertEqual(self.library.document(self.mid)['rows'][0]['analysis'], 'Saved explanation')

    def test_changed_original_is_not_overwritten_by_stale_analysis(self):
        started, release = threading.Event(), threading.Event()
        def blocked(sentence): started.set(); release.wait(3); return 'Old explanation'
        model, _ = self.model(analyze=blocked)
        with patch('learning_jobs.ModelClient', model):
            try:
                self.service.analyze(self.mid, 'a'); self.assertTrue(started.wait(2))
                document = self.library.document(self.mid);document['rows'][0]['en'] = 'Changed original.'
                self.library.save_document(self.mid, document)
            finally: release.set(); self.wait_worker()
        self.assertNotIn('analysis', self.library.document(self.mid)['rows'][0])

    def test_empty_article_and_busy_extraction_do_not_start_analysis(self):
        self.service.jobs[self.mid] = {'status': 'running'}
        self.assertEqual(self.service.analyze_all(self.mid)['status'], 'error')
        self.assertFalse(self.service.analysis_running)
        self.service.jobs.clear();self.library.save_document(self.mid, {'rows': []})
        self.assertEqual(self.service.analyze_all(self.mid)['status'], 'error')

    def test_batch_http_origin_guard_and_saved_status(self):
        server = PreviewServer(library=self.library, learning=self.service)
        address = server.register(self.source, {'kind': 'ytdlp', 'preview': {}})
        model, stats = self.model()
        try:
            request = urllib.request.Request(address + 'analyze-all', data=b'{}')
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)
            request.add_header('Origin', server.origin)
            with patch('learning_jobs.ModelClient', model):
                with urllib.request.urlopen(request) as response:self.assertIn(json.load(response)['status'], ('queued', 'running', 'done'))
                self.wait_worker()
            with urllib.request.urlopen(address + 'reading-data') as response:
                data = json.load(response)
            self.assertEqual((data['analysis_job']['done'], data['analysis_job']['total']), (3, 3))
            with urllib.request.urlopen(address + 'analysis-status?row=b') as response:
                self.assertEqual(json.load(response)['status'], 'done')
            self.assertEqual(stats.loads, 1)
        finally: server.close()


if __name__ == '__main__': unittest.main(verbosity=2)
