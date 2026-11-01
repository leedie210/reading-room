import json
import subprocess
import threading
import unittest
import urllib.request
import urllib.error
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from media_core import ROOT,preview_from_info
from preview_server import PreviewServer
from media_exports import caption_text,export_audio,export_video,export_transcript,export_clip

OUT=ROOT/'analysis/preview-check'

class PreviewChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True,exist_ok=True)
        import imageio_ffmpeg
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        commands={
            'combined.mp4':['-f','lavfi','-i','testsrc2=size=640x360:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','8','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart'],
            'video.mp4':['-i',str(OUT/'combined.mp4'),'-an','-c:v','copy','-movflags','+faststart'],
            'audio.m4a':['-i',str(OUT/'combined.mp4'),'-vn','-c:a','copy'],
            'cover.png':['-i',str(OUT/'combined.mp4'),'-frames:v','1','-update','1'],
        }
        for name,args in commands.items():
            run=subprocess.run([ffmpeg,'-v','error','-y',*args,str(OUT/name)],capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if run.returncode:raise RuntimeError(run.stderr.decode('utf-8','replace'))
        cls.calls=[]
        class Fixture(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_HEAD(self):self.do_GET()
            def do_GET(self):
                p=OUT/self.path.strip('/')
                if p.name not in commands:self.send_error(404);return
                data=p.read_bytes();start,end=0,len(data)-1
                selected=self.headers.get('Range')
                if selected:
                    left,right=selected.removeprefix('bytes=').split('-')
                    start=int(left or 0);end=min(int(right or end),end)
                if start>=len(data):self.send_error(416);return
                self.send_response(206 if selected else 200)
                self.send_header('Content-Length',str(end-start+1))
                self.send_header('Content-Type',{'mp4':'video/mp4','m4a':'audio/mp4','png':'image/png'}[p.suffix[1:]])
                if selected:self.send_header('Content-Range',f'bytes {start}-{end}/{len(data)}')
                self.end_headers()
                if self.command!='HEAD':
                    try:self.wfile.write(data[start:end+1])
                    except (BrokenPipeError,ConnectionResetError):pass
        cls.upstream=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        threading.Thread(target=cls.upstream.serve_forever,daemon=True).start()
        cls.origin=f'http://127.0.0.1:{cls.upstream.server_port}'
        cls.service=PreviewServer(lambda s,i:cls.calls.append((s,i)))
        def source(split):
            preview={'video':{'url':cls.origin+('/video.mp4' if split else '/combined.mp4')},
                'audio':{'url':cls.origin+'/audio.m4a'} if split else None,'cover':{'url':cls.origin+'/cover.png'}}
            item={'kind':'ytdlp','url':'https://example.org/video','preview':preview}
            return {'title':'分离音视频预览检查' if split else '完整视频预览检查','platform':'本地测试','source_url':'https://example.org/video','preview':preview,'items':[item]},item
        cls.urls={}
        for kind,split in [('combined',False),('split',True)]:
            s,i=source(split);cls.urls[kind]=cls.service.register(s,i)

    @classmethod
    def tearDownClass(cls):
        cls.service.close();cls.upstream.shutdown();cls.upstream.server_close()

    def test_range_and_explicit_download(self):
        self.calls.clear()
        url=self.urls['combined']
        with urllib.request.urlopen(urllib.request.Request(url+'video',headers={'Range':'bytes=10-29'})) as r:
            self.assertEqual(r.status,206);self.assertEqual(len(r.read()),20)
            self.assertEqual(r.headers['Accept-Ranges'],'bytes')
            self.assertEqual(r.headers['Content-Range'],f'bytes 10-29/{(OUT/"combined.mp4").stat().st_size}')
        self.assertEqual(self.calls,[])
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(urllib.request.Request(url+'download',data=b''))
        self.assertEqual(raised.exception.code,403)
        with urllib.request.urlopen(urllib.request.Request(url+'download',data=b'',headers={'Origin':self.service.origin})) as r:self.assertEqual(r.status,200)
        self.assertEqual(len(self.calls),1)
        for route,mode in [('audio-export','audio'),('transcript-export','transcript')]:
            with urllib.request.urlopen(urllib.request.Request(url+route,data=b'',headers={'Origin':self.service.origin})) as r:self.assertEqual(r.status,200)
            self.assertEqual(self.calls[-1][1]['_export_mode'],mode)

    def test_clip_validation_and_subtitle_region(self):
        preview={'video':{'url':self.origin+'/combined.mp4'}}
        source={'title':'范围检查','duration':8,'preview':preview}
        address=self.service.register(source,{'kind':'ytdlp','preview':preview})
        def post(route,data):
            return urllib.request.urlopen(urllib.request.Request(address+route,data=json.dumps(data).encode(),headers={'Origin':self.service.origin,'Content-Type':'application/json'}))
        for data in ({'start':4,'end':2},{'start':1,'end':10},{'start':False,'end':2},{'start':'nan','end':2}):
            with self.assertRaises(urllib.error.HTTPError) as raised:post('clip-export',data)
            self.assertEqual(raised.exception.code,400)
        with post('clip-export',{'start':1.25,'end':3.75}) as response:self.assertEqual(response.status,200)
        self.assertEqual(self.calls[-1][1]['_clip_range'],{'start':1.25,'end':3.75})
        self.assertEqual(self.calls[-1][1]['_export_mode'],'clip')
        with post('transcript-export',{'method':'screen','roi':[.1,.7,.9,.9]}) as response:self.assertEqual(response.status,200)
        self.assertEqual(self.calls[-1][1]['_subtitle_roi'],[.1,.7,.9,.9])
        with self.assertRaises(urllib.error.HTTPError):post('transcript-export',{'method':'screen','roi':[-1,0,1,1]})

    def test_clips_preserve_timing_picture_and_separate_audio(self):
        import imageio_ffmpeg,numpy as np
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        start,end=2.17,5.42
        def frame(path,at):
            run=subprocess.run([ffmpeg,'-v','error','-ss',str(at),'-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,timeout=20)
            self.assertEqual(run.returncode,0);return np.frombuffer(run.stdout,dtype=np.uint8).astype(float)
        reference=frame(OUT/'combined.mp4',start)
        for split in (False,True):
            preview={'video':{'url':self.origin+('/video.mp4' if split else '/combined.mp4')},'audio':{'url':self.origin+'/audio.m4a'} if split else None}
            source={'title':'精确选段','duration':8,'preview':preview}
            item={'kind':'ytdlp','preview':preview,'_clip_range':{'start':start,'end':end}}
            with tempfile.TemporaryDirectory(dir=OUT) as directory:
                result=export_clip(source,item,directory)
                probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(result)],capture_output=True).stderr.decode('utf-8','replace')
                self.assertIn('Audio:',probe);self.assertIn('Video:',probe)
                self.assertRegex(probe,r'Duration: 00:00:03\.[23]')
                actual=frame(result,0);self.assertEqual(len(actual),len(reference))
                self.assertLess(float(np.mean(np.abs(actual-reference))),5)
                self.assertEqual([p.suffix for p in Path(directory).iterdir()],['.mp4'])

    def test_split_format_selection(self):
        data={'formats':[
            {'url':'https://cdn.example/v.mp4','vcodec':'avc1.64001f','acodec':'none','ext':'mp4','height':720},
            {'url':'https://cdn.example/a.m4a','vcodec':'none','acodec':'mp4a.40.2','ext':'m4a'},
        ],'thumbnail':'https://cdn.example/cover.jpg'}
        result=preview_from_info(data)
        self.assertEqual(result['video']['url'],data['formats'][0]['url'])
        self.assertEqual(result['audio']['url'],data['formats'][1]['url'])
        self.assertTrue(result['cover'])

    def test_caption_formats_and_text_only_output(self):
        self.assertEqual(caption_text('{"body":[{"content":"第一句"},{"content":"第二句"}]}','json'),'第一句\n第二句')
        self.assertEqual(caption_text('WEBVTT\n\n00:00.000 --> 00:02.000\n<c>hello &amp; world</c>\n','vtt'),'hello & world')
        self.assertEqual(caption_text('<timedtext><text>hello</text><text>world</text></timedtext>','srv1'),'hello\nworld')
        source={'title':'字幕检查','subtitles':[{'language':'zh','ext':'json3','data':'{"events":[{"segs":[{"utf8":"字幕正文"}]}]}'}]}
        with tempfile.TemporaryDirectory() as directory:
            result=export_transcript(source,{'kind':'video'},directory)
            self.assertEqual(result['origin'],'平台字幕')
            self.assertEqual(result['path'].read_text(encoding='utf-8-sig').strip(),'字幕正文')
            self.assertEqual([p.suffix for p in Path(directory).iterdir()],['.txt'])

    def test_audio_only_and_video_merge(self):
        preview={'video':{'url':self.origin+'/video.mp4'},'audio':{'url':self.origin+'/audio.m4a'}}
        item={'kind':'ytdlp','url':'https://example.org','preview':preview,'resolved_streams':True}
        source={'title':'导出检查','duration':8,'preview':preview}
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as directory:
            audio=export_audio(source,item,directory)
            video=export_video(source,item,directory)
            audio_probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',str(audio)],capture_output=True).stderr.decode('utf-8','replace')
            video_probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',str(video)],capture_output=True).stderr.decode('utf-8','replace')
            self.assertIn('Audio:',audio_probe);self.assertNotIn('Video:',audio_probe)
            self.assertIn('Audio:',video_probe);self.assertIn('Video:',video_probe)
            self.assertEqual(sorted(p.suffix for p in Path(directory).iterdir()),['.mp3','.mp4'])

if __name__=='__main__':unittest.main(verbosity=2)
