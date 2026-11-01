"""Regression cases for lexical panels, sparse captions and opt-in learning export."""
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from app import App
from learning_jobs import LearningService
from learning_text import ocr_languages,make_rows,complete_english,reusable_document,same_screen_caption
from media_library import Library
from media_exports import export_transcript
from subtitle_pipeline import extract_captions

class SubtitleChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source={'title':'Synthetic captions','duration':330,'subtitles':[]}
        self.item={'kind':'ytdlp','_transcript_method':'auto'}
        self.speech={'text':'Real speech.','language':'en','model':'test ASR','processed_seconds':330,
                     'events':[{'start':0,'end':4,'text':'Real speech.'},{'start':325,'end':330,'text':'The final sentence.'}]}
    def tearDown(self):self.temp.cleanup()
    def test_glossary_is_not_chinese_translation(self):
        events=[{'start':0,'end':14,'text':'The more you share your life publicly.\n3. overshare v. 过度分享\n4. tempt v.诱惑\n语言频道'},
                {'start':14,'end':20,'text':'Real original subtitle.\n真正对应的中文原字幕。'}]
        en,zh=ocr_languages(events)
        self.assertEqual(len(en),2);self.assertEqual([c['text'] for c in zh],['真正对应的中文原字幕。'])
    def test_static_titles_do_not_prove_full_speech_coverage(self):
        cues=[{'start':0,'end':330,'text':'Choose your destination'}]
        self.assertFalse(complete_english(cues,330))
    def test_lexical_panel_must_not_swallow_changing_english(self):
        panel='\n1. focus n. 专注\n2. evolve v. 进步'
        self.assertFalse(same_screen_caption('The beginning.'+panel,'A different sentence.'+panel))
        self.assertTrue(same_screen_caption('The same sentence.'+panel,'The same sentence.'+panel))
    def test_no_punctuation_has_bounded_segments(self):
        cues=[{'start':i*4,'end':i*4+4,'text':'word number '+str(i)} for i in range(30)]
        rows,_=make_rows(cues,[],'speech','')
        self.assertGreater(len(rows),5)
        self.assertTrue(all(r['end']-r['start']<=18 for r in rows))
        self.assertEqual(sum(len(r['en'].split()) for r in rows),90)
    def test_long_chinese_interval_cannot_merge_many_english_sentences(self):
        cues=[{'start':i*4,'end':i*4+4,'text':'Sentence '+str(i)+'.'} for i in range(20)]
        rows,_=make_rows(cues,[{'start':0,'end':80,'text':'一段长时间显示的文字。'}],'en','zh')
        self.assertGreater(len(rows),4);self.assertTrue(all(r['end']-r['start']<=18 for r in rows))
    def test_old_bad_completed_document_not_reused(self):
        document={'extraction_complete':True,'rows':[{'start':0,'end':330,'en':'Choose your destination','zh':'3. overshare v. 过度分享'}]}
        self.assertFalse(reusable_document(document,330))
    def test_auto_standalone_goes_to_audio_without_full_ocr(self):
        with patch('media_exports._audio_to') as audio,patch('transcription.transcribe_wav',return_value=self.speech),patch('transcription.screen_text',side_effect=AssertionError('No full OCR for standalone auto')):
            result=extract_captions(self.source,self.item,self.root)
        audio.assert_called_once();self.assertEqual(result['quality']['basis'],'full-audio');self.assertIn('final sentence',result['text'])
    def test_explicit_speech_ignores_available_subtitle_and_ocr(self):
        self.source['subtitles']=[{'language':'en','data':'not used'}]
        with patch('media_exports._fetch_track',side_effect=AssertionError('Speech means speech')),patch('media_exports._audio_to'),patch('transcription.transcribe_wav',return_value=self.speech),patch('transcription.screen_text',side_effect=AssertionError('Speech must not use OCR')):
            result=extract_captions(self.source,{**self.item,'_transcript_method':'speech'},self.root,learning=True)
        self.assertEqual(result['method'],'speech')
    def test_sparse_platform_track_falls_back_to_audio(self):
        self.source['subtitles']=[{'language':'en','ext':'json','data':json.dumps({'body':[{'from':0,'to':330,'content':'Choose your destination'}]})}]
        with patch('media_exports._audio_to'),patch('transcription.transcribe_wav',return_value=self.speech):
            result=extract_captions(self.source,self.item,self.root)
        self.assertEqual(result['quality']['basis'],'full-audio')
    def test_completed_bad_ocr_cache_cannot_suppress_real_audio(self):
        mid='synthetic';folder=self.root/'learning-work'/mid;folder.mkdir(parents=True)
        (folder/'ocr-progress.json').write_text(json.dumps({'complete':True,'roi':[0,.3,1,.6],
            'events':[{'start':0,'end':330,'text':'Choose your destination\n3. overshare v. 过度分享'}]}),encoding='utf-8')
        with patch('subtitle_pipeline.DATA_ROOT',self.root),patch('media_exports._video_for_ocr',side_effect=ValueError('No video available')),patch('media_exports._audio_to'),patch('transcription.transcribe_wav',return_value=self.speech):
            result=extract_captions({**self.source,'_material_id':mid},self.item,self.root/'work',learning=True)
        self.assertEqual(result['chinese'],[]);self.assertEqual(result['quality']['basis'],'full-audio')
    def test_glossary_cache_retries_lower_subtitles_before_speech(self):
        mid='synthetic';folder=self.root/'learning-work'/mid;folder.mkdir(parents=True)
        (folder/'ocr-progress.json').write_text(json.dumps({'complete':True,'events':[{'start':0,'end':330,'text':'Choose your destination\n3. overshare v. 过度分享'}]}),encoding='utf-8')
        events=[{'start':i*3,'end':i*3+3,'text':f'This is a complete original sentence number {i} for reading.\n这是对应的中文原字幕第{i}句。'} for i in range(110)]
        def screen(path,region,*args,**kwargs):
            if region is None:return {'roi':[0,.3,1,.6],'events':[{'start':0,'end':24,'text':'Choose your destination\n3. overshare v. 过度分享'}]}
            if kwargs.get('end_at') is not None:
                start=kwargs['start_at'];span=kwargs['end_at']-start
                return {'roi':region,'events':[{'start':start+i*3,'end':start+i*3+3,'text':f'This is a complete original sentence number {i} for reading.\n这是对应的中文原字幕第{i}句。'} for i in range(int(span/3))]}
            return {'roi':region,'events':events}
        with patch('subtitle_pipeline.DATA_ROOT',self.root),patch('media_exports._video_for_ocr'),patch('transcription.screen_text',side_effect=screen),patch('transcription.transcribe_wav',side_effect=AssertionError('Original subtitles should win')):
            result=extract_captions({**self.source,'_material_id':mid},self.item,self.root/'work',learning=True)
        self.assertEqual(result['quality']['basis'],'validated-captions');self.assertEqual(len(result['chinese']),110)
    def test_english_classification_never_changes_button_into_page_navigation(self):
        for english in (False,True):
            library=Library(self.root/(str(english)+'.sqlite3'),self.root/('vault-'+str(english)))
            source={'title':'Synthetic','source_url':'https://example.org/'+str(english)}
            mid=library.remember(source)['id'];library.classify(mid,english)
            app=object.__new__(App);app.busy=False;app.library=library;app.cookie_file=None;app.events=queue.Queue()
            app.destination=MagicMock();app.destination.get.return_value=str(self.root)
            app.status=MagicMock();app.learning=MagicMock();app.bar={};app.open_preview_page=MagicMock()
            app.job=lambda work,done:done(work())
            output={'path':self.root/'captions.txt','origin':'原声识别','captions':{'fixture':True}}
            with patch('app.export_transcript',return_value=output) as exporter:
                app.save_resource(source,{'kind':'ytdlp','_export_mode':'transcript','_transcript_method':'speech'})
            app.open_preview_page.assert_not_called()
            self.assertEqual(exporter.call_args.args[1]['_english_learning'],english)
            if english:app.learning.start.assert_called_once()
            else:
                app.learning.start.assert_not_called();self.assertFalse(library.vault.exists())
    def test_learning_reuses_extracted_captions_without_second_recognition(self):
        library=Library(self.root/'library.sqlite3',self.root/'vault')
        source={'title':'Synthetic','source_url':'https://example.org/reuse','duration':4}
        mid=library.remember(source)['id'];library.classify(mid,True)
        captions={'english':[{'start':0,'end':4,'text':'The original sentence.'}],
            'chinese':[{'start':0,'end':4,'text':'对应的原中文。'}],'en_origin':'原字幕','zh_origin':'原译文',
            'origins':['original'],'quality':{'complete':True},'method':'speech','artifacts':[]}
        service=LearningService(library)
        try:
            with patch('subtitle_pipeline.extract_captions',side_effect=AssertionError('Must reuse extraction')):
                service.start(source,self.item,captions=captions)
                limit=time.monotonic()+5
                while service.state(mid)['status'] in ('queued','running') and time.monotonic()<limit:time.sleep(.02)
            self.assertEqual(service.state(mid)['status'],'done',service.state(mid))
            self.assertEqual(library.document(mid)['rows'][0]['zh'],'对应的原中文。')
        finally:service.close()
    def test_ocr_proxy_decimates_frames_preserving_time(self):
        import imageio_ffmpeg,subprocess
        from media_exports import _video_for_ocr
        raw=self.root/'input.mp4';proxy=self.root/'proxy.mkv';ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=25','-t','2','-c:v','libx264',str(raw)],check=True,capture_output=True)
        preview={'video':{'file_path':str(raw)}}
        _video_for_ocr({'preview':preview},{'kind':'ytdlp','preview':preview},proxy,lambda s:None,lambda a,b:None)
        probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(proxy)],capture_output=True).stderr.decode('utf-8','replace')
        self.assertIn('2.50 fps',probe.replace('2.5 fps','2.50 fps'));self.assertIn('Duration: 00:00:02.00',probe)

if __name__=='__main__':unittest.main(verbosity=2)
