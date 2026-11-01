from paths import TOOLS_ROOT, DATA_ROOT
"""Loopback-only, token-scoped media preview. No media files are persisted."""
import base64
import copy
import hashlib
import json
import mimetypes
import math
import re
import secrets
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from media_core import ROOT,DownloadRedirect,UA,media_opener
from media_ranges import clip_range,subtitle_roi

def fetch_cover(resource):
    if not resource:return None
    if resource.get('file_path'):
        with Path(resource['file_path']).open('rb') as stream:data=stream.read(8*1024*1024+1)
        if len(data)>8*1024*1024:raise ValueError('封面图片过大。')
        return data
    req=urllib.request.Request(resource['url'],headers={'User-Agent':UA,**resource.get('headers',{})})
    with media_opener(resource['url']).open(req,timeout=20) as r:
        data=r.read(8*1024*1024+1)
    if len(data)>8*1024*1024:raise ValueError('封面图片过大。')
    return data

def cover_png(resource):
    import imageio_ffmpeg
    data=fetch_cover(resource)
    if not data:return None
    run=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-i','pipe:0','-frames:v','1',
        '-vf','scale=230:180:force_original_aspect_ratio=decrease','-f','image2pipe','-c:v','png','pipe:1'],
        input=data,capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if run.returncode or not run.stdout:raise ValueError('封面暂时无法显示，仍可尝试播放预览。')
    return base64.b64encode(run.stdout).decode('ascii')

class PreviewServer:
    def __init__(self,on_download=None,range_chunk_size=0,library=None,learning=None,on_restore=None):
        self.sessions={}
        self.lock=threading.Lock()
        self.on_download=on_download
        self.library=library;self.learning=learning
        self.on_restore=on_restore
        if library:
            from reading_marks import ReadingMarks
            self.marks=ReadingMarks(library)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def send_bytes(self,data,mime,status=200):
                self.send_response(status)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                if self.command!='HEAD':self.wfile.write(data)
            def resolve(self):
                if self.headers.get('Host')!=owner.address:return None,None
                parts=urllib.parse.urlsplit(self.path).path.strip('/').split('/')
                with owner.lock:session=owner.sessions.get(parts[0])
                return session,parts[1] if len(parts)==2 else ('page' if len(parts)==1 else None)
            def do_HEAD(self):self.do_GET()
            def do_GET(self):
                session,route=self.resolve()
                if not session:return self.send_bytes(b'Preview expired.','text/plain',404)
                if route=='page':
                    return self.send_bytes(Path(__file__).with_name('preview.html').read_bytes(),'text/html; charset=utf-8')
                if route=='reading':
                    return self.send_bytes(Path(__file__).with_name('reading.html').read_bytes(),'text/html; charset=utf-8')
                if route in ('reading-room.css','reading-selection.js','shelf','shelf.js'):
                    filename={'shelf':'shelf.html'}.get(route,route)
                    mime='text/html' if route=='shelf' else 'text/css' if route.endswith('.css') else 'application/javascript'
                    return self.send_bytes(Path(__file__).with_name(filename).read_bytes(),mime+'; charset=utf-8')
                if route in ('shelf-data','library-marks','library-open','library-cover','marks-export'):
                    if not owner.library:return self.send_bytes(b'Unavailable','text/plain',409)
                    query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    try:
                        if route=='shelf-data':
                            data=[{k:r.get(k) for k in ('id','title','platform','has_reading','cover_path')} for r in owner.library.list(english_only=True,limit=10000)]
                            for record in data:record['cover']=bool(record.pop('cover_path'))
                        elif route=='library-marks':data=owner.marks.list()
                        elif route=='library-open':data={'url':owner.material_url(query.get('material',[''])[0])}
                        elif route=='library-cover':
                            record=owner.library.get(query.get('material',[''])[0]);path=Path(record.get('cover_path') or '')
                            if not record['english'] or not path.is_file():raise ValueError('No cover')
                            return self.send_bytes(path.read_bytes(),'image/png')
                        else:
                            paths=owner.marks.export();kind=query.get('kind',['word'])[0]
                            if kind not in paths:raise ValueError('Invalid kind')
                            return self.send_bytes(Path(paths[kind]).read_bytes(),'text/markdown; charset=utf-8')
                        return self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                    except (ValueError,OSError):return self.send_bytes('素材暂不可用，请刷新书架。'.encode(),'text/plain; charset=utf-8',404)
                if route in ('reading-data','analysis-status'):
                    if not owner.learning or not session['source'].get('_material_id'):return self.send_bytes(b'Unavailable','text/plain',409)
                    material_id=session['source']['_material_id']
                    if route=='reading-data':
                        data=owner.learning.view(material_id)
                        if owner.library:data['marks']=owner.marks.list(material_id)
                    else:
                        row_id=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('row',[''])[0]
                        data=owner.learning.analysis_state(material_id,row_id)
                    return self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                if route=='hls.js':
                    return self.send_bytes((TOOLS_ROOT/'hls/hls.min.js').read_bytes(),'application/javascript')
                if route=='info':
                    try:owner.restore_session(session)
                    except Exception:return self.send_bytes('视频地址未能刷新，请从素材历史重新解析。'.encode(),'text/plain; charset=utf-8',502)
                    data={k:session[k] for k in ('title','platform','source_url')}
                    data.update(video=bool(session['preview'].get('video')),audio=bool(session['preview'].get('audio')),
                                cover=bool(session['preview'].get('cover')),note=session['preview'].get('note',''),
                                hls=(session['preview'].get('video') or {}).get('protocol')=='hls',
                                transcript_method=session['source'].get('_transcript_method','auto'))
                    duration=session['source'].get('duration')
                    data['duration']=duration if isinstance(duration,(int,float)) and math.isfinite(duration) and duration>0 else None
                    material_id=session['source'].get('_material_id')
                    data['material_id']=material_id
                    data['learning_enabled']=bool(owner.learning and material_id)
                    if owner.library and material_id:data['english']=owner.library.get(material_id)['english']
                    return self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                resource=session['preview'].get(route) if route in ('video','audio','cover') else session['assets'].get(route)
                if not resource:
                    return self.send_bytes(b'Not found.','text/plain',404)
                try:self.proxy(resource,route,session)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
                except Exception:
                    if not getattr(self,'proxy_started',False):self.send_bytes('媒体地址暂不可用，请重新解析，或打开原网页预览。'.encode(),'text/plain; charset=utf-8',502)
                    else:self.close_connection=True
            def proxy(self,resource,role,session):
                if resource.get('file_path'):return self.local_file(resource,role)
                headers={'User-Agent':UA,**resource.get('headers',{})}
                selected=self.headers.get('Range')
                if selected:
                    match=re.fullmatch(r'bytes=(\d*)-(\d*)',selected)
                    if not match:return self.send_bytes(b'Invalid range','text/plain',400)
                    start,end=match.groups()
                    if start and range_chunk_size and not resource.get('protocol'):
                        end=str(min(int(end),int(start)+range_chunk_size-1)) if end else str(int(start)+range_chunk_size-1)
                    headers['Range']=f'bytes={start}-{end}'
                if self.headers.get('If-Range'):headers['If-Range']=self.headers['If-Range']
                request=urllib.request.Request(resource['url'],headers=headers,method=self.command)
                try:upstream=media_opener(resource['url']).open(request,timeout=25)
                except urllib.error.HTTPError as e:
                    if e.code==416:
                        self.send_response(416)
                        if e.headers.get('Content-Range'):self.send_header('Content-Range',e.headers['Content-Range'])
                        self.send_header('Content-Length','0');self.end_headers();return
                    raise
                with upstream:
                    mime=upstream.headers.get('Content-Type','').split(';')[0]
                    if mime in ('text/html','application/json'):raise ValueError('Upstream error page')
                    manifest=resource.get('protocol')=='hls' or 'mpegurl' in mime.lower() or urllib.parse.urlsplit(upstream.url).path.endswith('.m3u8')
                    if manifest and self.command!='HEAD':
                        body=upstream.read(3*1024*1024+1)
                        if len(body)>3*1024*1024 or not body.lstrip().startswith(b'#EXTM3U'):raise ValueError('Invalid HLS manifest')
                        def member(address):
                            absolute=urllib.parse.urljoin(upstream.url,address)
                            parsed=urllib.parse.urlsplit(absolute)
                            if parsed.scheme not in ('https','http') or not parsed.hostname:raise ValueError('Unsupported media resource')
                            selected_headers=dict(resource.get('headers',{}))
                            if parsed.hostname!=urllib.parse.urlsplit(resource['url']).hostname:
                                selected_headers={k:v for k,v in selected_headers.items() if k.lower() not in ('cookie','authorization')}
                            key='asset-'+hashlib.sha256(absolute.encode()).hexdigest()[:24]
                            with owner.lock:session['assets'][key]={'url':absolute,'headers':selected_headers,'protocol':'hls-asset'}
                            return key
                        lines=[]
                        for line in body.decode('utf-8-sig').splitlines():
                            if line.startswith('#'):line=re.sub(r'URI="([^\"]+)"',lambda m:'URI="'+member(m.group(1))+'"',line)
                            elif line.strip():line=member(line.strip())
                            lines.append(line)
                        return self.send_bytes(('\n'.join(lines)+'\n').encode(),'application/vnd.apple.mpegurl')
                    self.send_response(upstream.status)
                    if upstream.status==206 and not upstream.headers.get('Accept-Ranges'):
                        self.send_header('Accept-Ranges','bytes')
                    for key in ('Content-Length','Content-Range','Accept-Ranges','ETag','Last-Modified'):
                        if upstream.headers.get(key):self.send_header(key,upstream.headers[key])
                    default={'video':'video/mp4','audio':'audio/mp4','cover':'image/jpeg'}.get(role,'application/octet-stream')
                    if resource.get('ext')=='webm':default='video/webm' if role=='video' else 'audio/webm'
                    self.send_header('Content-Type',mime if mime and mime!='application/octet-stream' else default)
                    self.send_header('Cache-Control','no-store')
                    self.send_header('Connection','close')
                    self.end_headers();self.proxy_started=True;self.close_connection=True
                    if self.command=='HEAD':return
                    while block:=upstream.read(32*1024):self.wfile.write(block)
            def local_file(self,resource,role):
                path=Path(resource['file_path'])
                if not path.is_file():return self.send_bytes('本地文件已移动，请从素材库重新定位或解析。'.encode(),'text/plain; charset=utf-8',404)
                size=path.stat().st_size;start,end=0,size-1
                selected=self.headers.get('Range')
                if selected:
                    match=re.fullmatch(r'bytes=(\d*)-(\d*)',selected)
                    if not match:return self.send_bytes(b'Invalid range','text/plain',400)
                    left,right=match.groups()
                    if left:start=int(left);end=min(int(right),end) if right else end
                    elif right:start=max(0,size-int(right))
                    else:return self.send_bytes(b'Invalid range','text/plain',400)
                if start>=size or end<start:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(206 if selected else 200)
                self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes')
                if selected:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.send_header('Connection','close');self.end_headers();self.close_connection=True
                if self.command=='HEAD':return
                with path.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        block=stream.read(min(65536,remaining))
                        if not block:break
                        self.wfile.write(block);remaining-=len(block)
            def do_POST(self):
                session,route=self.resolve()
                if not session or route not in ('download','audio-export','transcript-export','clip-export','classify','learning-start','learning-cancel','analyze','analyze-all','save-mark'):return self.send_bytes(b'Not found','text/plain',404)
                if self.headers.get('Origin')!=owner.origin:return self.send_bytes(b'Forbidden','text/plain',403)
                if route=='save-mark':
                    if not owner.library or not session['source'].get('_material_id'):return self.send_bytes(b'Unavailable','text/plain',409)
                    try:
                        length=int(self.headers.get('Content-Length','0'))
                        if not 0<length<=65536:raise ValueError('选区过长。')
                        body=json.loads(self.rfile.read(length))
                        if not isinstance(body,dict):raise ValueError('选区无效。')
                        data=owner.marks.add(session['source']['_material_id'],body)
                        return self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                    except (ValueError,TypeError) as error:return self.send_bytes(str(error).encode(),'text/plain; charset=utf-8',400)
                if route in ('classify','learning-start','learning-cancel','analyze','analyze-all'):
                    if not owner.learning or not session['source'].get('_material_id'):return self.send_bytes(b'Unavailable','text/plain',409)
                    try:
                        length=int(self.headers.get('Content-Length','0'))
                        if not 0<=length<=4096:raise ValueError('请求过大。')
                        body=json.loads(self.rfile.read(length)) if length else {}
                        if not isinstance(body,dict):raise ValueError('请求格式无效。')
                        material_id=session['source']['_material_id']
                        if route=='classify':
                            if body.get('english') is False:owner.learning.cancel(material_id)
                            data=owner.library.classify(material_id,body.get('english'))
                        elif route=='learning-start':
                            if not (owner.library.document(material_id) or {}).get('extraction_complete'):owner.restore_session(session)
                            data=owner.learning.start(session['source'],session['item'],subtitle_roi(body.get('roi')))
                        elif route=='learning-cancel':data=owner.learning.cancel(material_id)
                        elif route=='analyze-all':data=owner.learning.analyze_all(material_id)
                        else:
                            if not isinstance(body.get('row'),str) or len(body['row'])>50:raise ValueError('句子编号无效。')
                            data=owner.learning.analyze(material_id,body['row'])
                        return self.send_bytes(json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                    except (ValueError,TypeError):return self.send_bytes('请检查英语分类、字幕框选范围或句子编号。'.encode(),'text/plain; charset=utf-8',400)
                if not owner.on_download:return self.send_bytes(b'Unavailable','text/plain',409)
                item=copy.deepcopy(session['item'])
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if length<0 or length>4096:raise ValueError('请求数据过大。')
                    body=json.loads(self.rfile.read(length)) if length else {}
                    if not isinstance(body,dict):raise ValueError('请求格式无效。')
                    if route=='clip-export':item['_clip_range']=clip_range(body.get('start'),body.get('end'),session['source'].get('duration'))
                    if route=='transcript-export':
                        method=body.get('method',session['source'].get('_transcript_method','auto'))
                        if method not in ('auto','speech','screen'):raise ValueError('文案识别方式无效。')
                        item['_transcript_method']=method;item['_subtitle_roi']=subtitle_roi(body.get('roi'))
                except (ValueError,TypeError):return self.send_bytes('所选时间范围或字幕区域无效，请检查后重试。'.encode(),'text/plain; charset=utf-8',400)
                item['_export_mode']={'download':'video','audio-export':'audio','transcript-export':'transcript','clip-export':'clip'}[route]
                accepted=owner.on_download(copy.deepcopy(session['source']),item)
                if accepted is False:return self.send_bytes(b'Busy','text/plain',409)
                return self.send_bytes('{"ok":true}'.encode(),'application/json')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.address=f'127.0.0.1:{self.server.server_port}'
        self.origin='http://'+self.address
        threading.Thread(target=self.server.serve_forever,daemon=True).start()

    def register(self,source,item):
        preview=copy.deepcopy(item.get('preview') or source.get('preview') or {})
        if item.get('kind')=='video':preview['video']=copy.deepcopy(item);preview['audio']=None
        if item.get('kind')=='image':preview={'cover':copy.deepcopy(item),'video':None,'audio':None}
        token=secrets.token_urlsafe(24)
        with self.lock:
            # Keep a small number of open tabs valid without retaining old sessions forever.
            while len(self.sessions)>=20:self.sessions.pop(next(iter(self.sessions)))
            self.sessions[token]={'title':source.get('title','素材预览'),'platform':source.get('platform',''),
                'source_url':source.get('source_url',''),'preview':preview,'source':copy.deepcopy(source),'item':copy.deepcopy(item),'assets':{}}
        return self.origin+'/'+token+'/'

    def close(self):
        self.server.shutdown();self.server.server_close()
        with self.lock:self.sessions.clear()

    def material_url(self,material_id):
        record=self.library.get(material_id)
        if not record['english'] and not self.marks.list(material_id):raise ValueError('素材未归入英语学习。')
        with self.lock:
            for token,session in reversed(list(self.sessions.items())):
                if session['source'].get('_material_id')==material_id:return self.origin+'/'+token+'/reading'
        video=next(({'file_path':a['path']} for a in record['assets'] if a['kind']=='video' and Path(a['path']).is_file()),None)
        preview={'video':video}
        cover=record.get('cover_path')
        if cover and Path(cover).is_file():preview['cover']={'file_path':cover}
        source={**record,'_material_id':material_id,'preview':preview,'_needs_refresh':not bool(video)}
        return self.register(source,{'kind':'ytdlp','preview':preview,'resolved_streams':bool(video)})+'reading'

    def restore_session(self,session):
        if not session['source'].get('_needs_refresh') or not self.on_restore:return
        with self.lock:lock=session.setdefault('restore_lock',threading.Lock())
        with lock:
            if not session['source'].get('_needs_refresh'):return
            source,item=self.on_restore(session['source']['_material_id'])
            with self.lock:
                session.update(title=source.get('title',session['title']),platform=source.get('platform',session['platform']),
                    source_url=source.get('source_url',session['source_url']),source=copy.deepcopy(source),item=copy.deepcopy(item),
                    preview=copy.deepcopy(item.get('preview') or source.get('preview') or {}),assets={})
