from paths import TOOLS_ROOT, DATA_ROOT
"""Resumable bilingual extraction and on-demand sentence explanations."""
import copy
import hashlib
import json
import threading
import time
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from media_core import ROOT
from learning_text import parse_cues, ocr_languages, make_rows, annotations, wordlists
from learning_model import ModelClient, cache_key, load_config

class LearningService:
    def __init__(self, library):
        self.library = library
        self.jobs = {}
        self.guard = threading.RLock()
        self.work_lock = threading.Lock()
        self.analysis_queue = deque()
        self.analysis_running = False
        self.stopping = threading.Event()

    def state(self, material_id):
        with self.guard:
            job = self.jobs.get(material_id)
            if job: return {k: v for k, v in job.items() if k != 'cancel'}
        previous = self.library.cache_get('learning-job:' + material_id)
        if previous and previous.get('status') in ('queued', 'running'):
            previous = {**previous, 'status': 'paused', 'message': '上次处理已中断，可继续生成。'}
        return previous or {'status': 'idle', 'message': ''}

    def view(self, material_id):
        document = self.library.document(material_id)
        if document:
            document = copy.deepcopy(document)
            for row in document.get('rows', []): row['annotations'] = annotations(row['en'])
        config = load_config()
        if document:
            with self.guard:
                for row in document.get('rows', []):
                    row['analysis_status'] = self.jobs.get(self._analysis_key(material_id, row['id']), {}).get('status', '')
        batch = self.state('analysis-batch:' + material_id)
        rows = (document or {}).get('rows', [])
        batch.update(done=sum(bool(row.get('analysis')) for row in rows), total=len(rows))
        if rows and batch['done'] == batch['total']:
            batch.update(status='done', message=f'本篇解析已全部保存：{len(rows)}/{len(rows)}。')
        return {'material': self.library.get(material_id), 'document': document, 'job': self.state(material_id), 'analysis_job': batch,
                'provider': '离线模型（生成内容需校对）' if config['mode'] == 'local' else config.get('model', '在线模型'),
                'wordlists': wordlists()['sources']}

    def _update(self, key, **values):
        with self.guard:
            job = self.jobs[key]
            job.update(values)
            self.library.cache_put('learning-job:' + key, {k: v for k, v in job.items() if k != 'cancel'})

    def start(self, source, item, roi=None):
        material_id = source['_material_id']
        if not self.library.get(material_id)['english']:
            raise ValueError('请先勾选“作为英语学习素材”。')
        with self.guard:
            if material_id in self.jobs and self.jobs[material_id]['status'] in ('queued', 'running'):
                return self.state(material_id)
            self.jobs[material_id] = {'status': 'queued', 'message': '准备生成双语阅读…', 'done': 0,
                                      'total': source.get('duration') or 0, 'cancel': threading.Event()}
        threading.Thread(target=self._run, args=(copy.deepcopy(source), copy.deepcopy(item), roi), daemon=True).start()
        return self.state(material_id)

    def cancel(self, material_id):
        with self.guard:
            if material_id in self.jobs: self.jobs[material_id]['cancel'].set()
        return self.state(material_id)

    def _run(self, source, item, roi):
        material_id = source['_material_id']
        cancel = self.jobs[material_id]['cancel']
        def check():
            if cancel.is_set(): raise InterruptedError('已停止；完成的字幕和翻译已保留，可继续。')
        def log(text):
            check(); self._update(material_id, status='running', message=text)
        last_progress = [0.0]
        def progress(done, total):
            check()
            if time.monotonic() - last_progress[0] > 1:
                with self.guard: self.jobs[material_id].update(done=done, total=total)
                last_progress[0] = time.monotonic()
        try:
            with self.work_lock:
                check()
                self._update(material_id, status='running', message='正在检查中英文字幕来源…')
                old = self.library.document(material_id)
                if old and old.get('extraction_complete') and old.get('rows') and roi is None:
                    document = old
                else:
                    document = self._extract(source, item, roi, log, progress, check, cancel)
                check()
                missing = [row for row in document['rows'] if not row.get('zh')]
                if missing:
                    log(f'英文已保存，正在补译缺少中文的 {len(missing)} 个句段…')
                    with ModelClient(log) as model:
                        for offset in range(0, len(missing), 3):
                            check()
                            batch = missing[offset:offset + 3]
                            uncached = []
                            for row in batch:
                                saved = self.library.cache_get(cache_key('translation', source['title'] + '\n' + row['en']))
                                if saved:
                                    row.update(zh=saved['zh'], zh_origin=saved['origin'])
                                else: uncached.append(row)
                            if uncached:
                                translations = model.translate(uncached, source['title'])
                                for row in uncached:
                                    row.update(zh=translations[row['id']], zh_origin=model.label)
                                    self.library.cache_put(cache_key('translation', source['title'] + '\n' + row['en']),
                                                           {'zh': row['zh'], 'origin': model.label})
                            document['status_label'] = f'正在补译中文：{min(offset + 3, len(missing))}/{len(missing)}'
                            self.library.save_document(material_id, document)
                            self._update(material_id, message=document['status_label'], done=min(offset + 3, len(missing)), total=len(missing))
                document.update(status='ready', status_label='已生成；画面识别与模型内容可对照原片校对', sample=False)
                self.library.save_document(material_id, document)
                target = self.library.archive_document(material_id, document)
                self._update(material_id, status='done', message='双语阅读已保存到英语知识库。', path=str(target or ''))
        except InterruptedError as error:
            document = self.library.document(material_id)
            if document: self.library.archive_document(material_id, document)
            self._update(material_id, status='paused', message=str(error))
        except Exception as error:
            document = self.library.document(material_id)
            if document: self.library.archive_document(material_id, document)
            self._update(material_id, status='error', message=str(error)[:500] or '生成未完成，可以继续重试。')

    def _extract(self, source, item, roi, log, progress, check, cancel):
        from media_exports import _fetch_track, _video_for_ocr, _audio_to
        from transcription import screen_text, transcribe_wav
        material_id = source['_material_id']
        folder = self.library.archive_metadata(material_id)
        work = DATA_ROOT / 'learning-work' / material_id
        work.mkdir(parents=True, exist_ok=True)
        english, chinese, origins = [], [], []
        en_origin = zh_origin = ''
        def preserve(label, data, suffix='json'):
            raw = data if isinstance(data, bytes) else data.encode('utf-8')
            digest = hashlib.sha256(raw).hexdigest()[:12]
            path = folder / (label + '_' + digest + '.' + suffix)
            if not path.exists(): path.write_bytes(raw)
            self.library.add_asset(material_id, 'subtitle', path)
            return path
        for prefix in ('en', 'zh'):
            tracks = [track for track in source.get('subtitles', []) if track.get('language', '').lower().startswith(('zh', 'cmn') if prefix == 'zh' else ('en',))]
            tracks.sort(key=lambda track: bool(track.get('automatic')))
            for track in tracks:
                check()
                try:
                    raw = _fetch_track(track)
                    cues = parse_cues(raw, track.get('ext', 'vtt'))
                    if not cues: continue
                    preserve('平台原字幕_' + prefix, raw, track.get('ext', 'vtt') if track.get('ext') in ('vtt','srt','json','json3','ttml','xml','srv1','srv2','srv3') else 'txt')
                    origin = ('平台自动字幕' if track.get('automatic') else '平台字幕') + '（' + track.get('language', prefix) + '）'
                    if prefix == 'en': english, en_origin = cues, origin
                    else: chinese, zh_origin = cues, origin
                    origins.append(origin); break
                except (ValueError, OSError): continue
        if not english or not chinese:
            video = work / 'frames.mkv'
            checkpoint_path = work / 'ocr-progress.json'
            saved = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else {}
            if roi is not None and saved.get('roi')!=roi:saved={}
            if not video.exists() and not saved.get('complete'):
                partial = work / 'frames.partial.mkv'
                try:
                    _video_for_ocr(source, item, partial, log, progress); partial.replace(video)
                finally:
                    partial.unlink(missing_ok=True)
            check()
            def checkpoint(events, at, region):
                expected=float(source.get('duration') or 0)
                state = {'events': events, 'at': at, 'roi': region, 'complete': expected>0 and at >= expected - .5}
                temporary = checkpoint_path.with_suffix('.part')
                temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8'); temporary.replace(checkpoint_path)
                en, zh = ocr_languages(events)
                rows, unmatched = make_rows(english or en, chinese or zh, en_origin or '原视频英文画面字幕识别', zh_origin or '原视频中文画面字幕识别')
                if rows:
                    self.library.save_document(material_id, {'status': 'partial', 'status_label': f'画面字幕已处理到 {int(at)//60:02}:{int(at)%60:02}；可先阅读完成部分',
                        'rows': rows, 'origins': origins + ['原视频中英画面字幕识别（0.4秒采样）'], 'coverage': [[0, at]], 'unmatched_chinese': unmatched})
            try:
                if saved.get('complete'):
                    result = saved
                else:
                    result = screen_text(video, roi or saved.get('roi'), log, progress, bilingual=True,
                        start_at=max(0, saved.get('at', 0) + (.4 if saved.get('events') else 0)),
                        previous=saved.get('events'), checkpoint=checkpoint, cancel=cancel)
                    final_state={'events':result['events'],'at':float(source.get('duration') or 0),'roi':result['roi'],'complete':True}
                    checkpoint_path.write_text(json.dumps(final_state,ensure_ascii=False),encoding='utf-8')
                en, zh = ocr_languages(result['events'])
                preserve('原视频画面字幕', json.dumps(result, ensure_ascii=False, indent=2))
                duration = float(source.get('duration') or 0)
                coverage = sum(max(0, cue['end'] - cue['start']) for cue in en)
                if not english and en and (duration <= 0 or coverage > duration * .15): english, en_origin = en, '原视频英文画面字幕识别'
                if not chinese and zh: chinese, zh_origin = zh, '原视频中文画面字幕识别'
                origins.append('原视频画面字幕识别（0.4秒采样）')
            except (ValueError, OSError):
                if not english: log('未取得足够清晰的英文画面字幕，继续识别英语原声…')
                else: log('未取得清晰的中文画面字幕，保留英文并补充翻译…')
            finally:
                if not cancel.is_set(): video.unlink(missing_ok=True)
        if not english:
            audio = work / 'speech.wav'
            try:
                _audio_to(source, item, audio, wav=True, log=log, progress=progress)
                result = transcribe_wav(audio, source.get('title', ''), log, progress, language='en')
                english = result.get('events') or []
                preserve('原声英文识别', json.dumps(result, ensure_ascii=False, indent=2))
                en_origin = '英语原声识别（Whisper large-v3，需校对）'; origins.append(en_origin)
            finally: audio.unlink(missing_ok=True)
        if not english: raise ValueError('没有取得可读的英文字幕，未生成学习稿。')
        rows, unmatched = make_rows(english, chinese, en_origin, zh_origin)
        old = self.library.document(material_id) or {}
        previous = {row['en']: row for row in old.get('rows', []) if row.get('analysis')}
        for row in rows:
            if row['en'] in previous: row['analysis'] = previous[row['en']]['analysis']
        document = {'status': 'partial', 'status_label': '原文已提取，正在检查中文对照', 'rows': rows,
                    'origins': origins, 'coverage': [[0, source.get('duration')]], 'unmatched_chinese': unmatched,
                    'extraction_complete': True, 'sample': False}
        self.library.save_document(material_id, document)
        return document

    @staticmethod
    def _analysis_key(material_id, row_id):
        return 'analysis:' + material_id + ':' + row_id

    def analysis_state(self, material_id, row_id):
        document = self.library.document(material_id) or {}
        row = next((row for row in document.get('rows', []) if row['id'] == row_id), None)
        if row and row.get('analysis'):
            return {'status': 'done', 'analysis': row['analysis'], 'origin': row.get('analysis_origin', '已保存的句子解析')}
        return self.state(self._analysis_key(material_id, row_id))

    def _save_analysis(self, material_id, row, value):
        # Commit each finished sentence before taking the next task or unloading the model.
        with self.library.lock:
            latest = self.library.document(material_id) or {}
            target = next((item for item in latest.get('rows', []) if item['id'] == row['id'] and item['en'] == row['en']), None)
            if not target: raise ValueError('原文句段已更新，请重新生成解析。')
            target.update(analysis=value['analysis'], analysis_origin=value['origin'])
            self.library.save_document(material_id, latest)
            self.library.archive_document(material_id, latest)

    def _queue_analysis(self, material_id, row):
        key = self._analysis_key(material_id, row['id'])
        if row.get('analysis'): return self.analysis_state(material_id, row['id'])
        cached = self.library.cache_get(cache_key('analysis', row['en']))
        if cached:
            self._save_analysis(material_id, row, cached)
            return {'status': 'done', **cached}
        if self.jobs.get(key, {}).get('status') in ('queued', 'running'): return self.state(key)
        self.jobs[key] = {'status': 'queued', 'message': '已加入解析队列，关闭窗口后会继续处理。'}
        self._update(key)
        self.analysis_queue.append((material_id, copy.deepcopy(row)))
        return self.state(key)

    def _start_analysis_worker(self):
        if self.analysis_queue and not self.analysis_running and not self.stopping.is_set():
            self.analysis_running = True
            threading.Thread(target=self._analysis_work, daemon=True).start()

    def analyze(self, material_id, row_id):
        with self.guard:
            document = self.library.document(material_id) or {}
            row = next((row for row in document.get('rows', []) if row['id'] == row_id), None)
            if not row: raise ValueError('句子不存在，请刷新阅读页面。')
            if not row.get('analysis') and self.state(material_id)['status'] in ('queued', 'running'):
                return {'status': 'error', 'message': '字幕仍在生成，完成后可解析新的句子。'}
            result = self._queue_analysis(material_id, row)
            self._start_analysis_worker()
            return result

    def analyze_all(self, material_id):
        with self.guard:
            document = self.library.document(material_id) or {}
            rows = document.get('rows', [])
            if not rows: return {'status': 'error', 'message': '请先生成双语阅读文本。'}
            if self.state(material_id)['status'] in ('queued', 'running'):
                return {'status': 'error', 'message': '字幕仍在生成，完成后可提前生成本篇解析。'}
            key = 'analysis-batch:' + material_id
            if self.jobs.get(key, {}).get('status') in ('queued', 'running'): return self.state(key)
            self.jobs[key] = {'status': 'queued', 'message': '已加入后台解析队列，可继续阅读。'}
            for row in rows: self._queue_analysis(material_id, row)
            self._refresh_analysis_batch(material_id)
            self._start_analysis_worker()
            return self.state(key)

    def _refresh_analysis_batch(self, material_id):
        with self.guard:
            key = 'analysis-batch:' + material_id
            if key not in self.jobs: return
            rows = (self.library.document(material_id) or {}).get('rows', [])
            done = sum(bool(row.get('analysis')) for row in rows)
            states = [self.jobs.get(self._analysis_key(material_id, row['id']), {}).get('status') for row in rows if not row.get('analysis')]
            active = any(status in ('queued', 'running') for status in states)
            status = ('running' if 'running' in states else 'queued') if active else ('done' if done == len(rows) else 'error')
            message = (f'正在后台生成解析：已保存 {done}/{len(rows)}，可继续阅读。' if active else
                       f'本篇解析已全部保存：{done}/{len(rows)}。' if status == 'done' else
                       f'解析已保存 {done}/{len(rows)}；未完成的句子可点击继续生成。')
            if self.stopping.is_set() and status != 'done': status, message = 'paused', f'解析已保存 {done}/{len(rows)}，下次可继续生成。'
            self._update(key, status=status, message=message, done=done, total=len(rows))

    def _analysis_work(self):
        current = None
        try:
            # Serialize with subtitle extraction. One model session serves this entire queue.
            with self.work_lock, ExitStack() as stack:
                model = None
                while not self.stopping.is_set():
                    with self.guard:
                        if not self.analysis_queue: break
                        current = self.analysis_queue.popleft()
                    material_id, row = current
                    key = self._analysis_key(material_id, row['id'])
                    self._update(key, status='running', message='正在分析句子结构，关闭窗口后会继续处理。')
                    self._refresh_analysis_batch(material_id)
                    # Recheck persistence: another request may already have restored the result.
                    saved = self.analysis_state(material_id, row['id'])
                    if saved['status'] == 'done':
                        self._update(key, **saved); self._refresh_analysis_batch(material_id); continue
                    cached = self.library.cache_get(cache_key('analysis', row['en']))
                    if not cached and model is None:
                        # A failed model load ends the queue rather than retrying it for every sentence.
                        model = stack.enter_context(ModelClient(lambda text: self._update(key, message=text)))
                    if self.stopping.is_set(): break
                    try:
                        value = cached or {'analysis': model.analyze(row['en'], self.library.get(material_id)['title']), 'origin': model.label}
                        if not value['analysis'].strip(): raise ValueError('模型没有返回完整解析，可继续重试。')
                        self.library.cache_put(cache_key('analysis', row['en']), value)
                        self._save_analysis(material_id, row, value)
                        self._update(key, status='done', **value)
                    except Exception as error:
                        self._update(key, status='paused' if self.stopping.is_set() else 'error', message=str(error)[:500])
                    self._refresh_analysis_batch(material_id)
        except Exception as error:
            with self.guard:
                unfinished = ([current] if current else []) + list(self.analysis_queue)
                self.analysis_queue.clear()
                for material_id, row in unfinished:
                    self._update(self._analysis_key(material_id, row['id']), status='paused' if self.stopping.is_set() else 'error', message=str(error)[:500])
                for material_id in {task[0] for task in unfinished}: self._refresh_analysis_batch(material_id)
        finally:
            with self.guard:
                self.analysis_running = False
                self._start_analysis_worker()

    def close(self):
        self.stopping.set()
        with self.guard:
            for key, job in self.jobs.items():
                if job.get('cancel'): job['cancel'].set()
                if key.startswith(('analysis:', 'analysis-batch:')) and job['status'] in ('queued', 'running'):
                    self._update(key, status='paused', message='处理已中断；已完成的解析已保存，下次可继续生成。')
            self.analysis_queue.clear()
        from learning_model import shutdown_models
        shutdown_models()
