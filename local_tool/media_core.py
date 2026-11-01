from paths import TOOLS_ROOT, DATA_ROOT
"""Local public-page media extraction, independently implemented from observed flows.

Public-page adapters and yt-dlp; no proprietary application code is included.
"""
import gzip
import concurrent.futures
import html
import http.cookiejar
import json
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS_ROOT / 'python-packages'))
sys.path.insert(0, str(TOOLS_ROOT / 'video-packages'))
UA = 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36'

def engine_runtime_options():
    import shutil,os
    node=Path(os.environ.get('READING_ROOM_NODE') or shutil.which('node') or TOOLS_ROOT/'node-runtime/node.exe')
    return {'js_runtimes':{'node':{'path':str(node)}}} if node.exists() else {}

class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Cookie')
            redirected.remove_header('Authorization')
        return redirected

def media_opener(address):
    return urllib.request.build_opener(DownloadRedirect())


def extract_url(text):
    match = re.search(r'https?://[^\s<>\[\]"\u300b]+', text)
    if not match:
        raise ValueError('没有找到链接，请粘贴完整分享文案或 HTTP/HTTPS 链接。')
    return match.group().rstrip('，。；、！!）)')

def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

def json_assignment(page, name):
    m = re.search(r'(?:window\.)?' + re.escape(name) + r'\s*=\s*', page)
    if not m:
        return None
    text = page[m.end():].lstrip()
    # Replace JS undefined only outside strings; never eval page scripts.
    parts = re.split(r'("(?:\\.|[^"\\])*")', text)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'\bundefined\b', 'null', parts[i])
    try:
        return json.JSONDecoder().raw_decode(''.join(parts))[0]
    except ValueError:
        return None

def first_url(value):
    if isinstance(value, str) and value.startswith(('http://', 'https://')):
        return value
    if isinstance(value, dict):
        for key in ('url_list', 'url', 'src', 'masterUrl'):
            found = first_url(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = first_url(child)
            if found:
                return found
    return None

class PublicMedia:
    def __init__(self, cookie_file=None):
        self.jar = http.cookiejar.CookieJar()
        if cookie_file:
            self.jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
            self.jar.load(ignore_discard=True, ignore_expires=False)
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.cookie_file = cookie_file

    def page(self, url, referer=''):
        req = urllib.request.Request(url, headers={
            'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9', 'Referer': referer or url,
        })
        with self.client.open(req, timeout=30) as response:
            data = response.read(8_000_001)
            if len(data) > 8_000_000:
                raise ValueError('页面过大，已停止读取。')
            if response.headers.get('Content-Encoding') == 'gzip':
                data = gzip.decompress(data)
            return response.url, data.decode('utf-8', 'replace')

    def inspect(self, text, log=lambda s: None):
        url = extract_url(text)
        host = (urllib.parse.urlsplit(url).hostname or '').lower()
        if any(host == d or host.endswith('.'+d) for d in ('douyin.com','iesdouyin.com')):
            result=self.douyin(url, log)
        elif any(host == d or host.endswith('.'+d) for d in ('xiaohongshu.com','xhslink.com','xhslink.cn','rednote.com')):
            result=self.xiaohongshu(url)
        elif any(host == d or host.endswith('.'+d) for d in ('kuaishou.com','gifshow.com')):
            result=self.kuaishou(url)
        else:
            try:return self.ytdlp(url, log)
            except Exception:
                if any(host==d or host.endswith('.'+d) for d in ('bilibili.com','b23.tv')):
                    log('正在从 B 站公开接口读取视频…')
                    return self.bilibili(url)
                raise
        # Preserve only cookies scoped to each selected media host.
        for item in result['items']:
            request=urllib.request.Request(item['url'],headers=item.get('headers',{}))
            self.jar.add_cookie_header(request)
            if request.has_header('Cookie'):item.setdefault('headers',{})['Cookie']=request.get_header('Cookie')
        return result

    @staticmethod
    def result(url, title, platform, items):
        seen = set()
        result = []
        for item in items:
            if item.get('url') and item['url'] not in seen:
                seen.add(item['url'])
                result.append(item)
        if not result:
            raise ValueError('页面没有返回可下载媒体，可能需要正常登录、内容已失效或页面结构已改变。')
        videos=[i for i in result if i.get('kind')=='video']
        images=[i for i in result if i.get('kind')=='image']
        cover=next((i for i in images if i.get('name')=='封面'),next(iter(images),None))
        return {'source_url':url,'title':title or platform,'platform':platform,'items':result,
                'preview':{'video':next(iter(videos),None),'audio':None,'cover':cover}}

    def douyin(self, url, log):
        referer = 'https://www.iesdouyin.com/'
        final, page = self.page(url, referer)
        match = re.search(r'/(?:share/)?(?:video|note|slides)/(\d{6,})', final)
        if not match:
            raise ValueError('未识别到抖音作品编号。')
        ident = match.group(1)
        pages = [(final, page)]
        node = None
        for i in range(3):
            if i:
                if i == 1:
                    log('建立抖音匿名会话…')
                    self.page(referer)
                canonical = f'{referer}share/{"video" if i == 1 else "note"}/{ident}/'
                pages.append(self.page(canonical, referer))
            current, content = pages[-1]
            state = json_assignment(content, '_ROUTER_DATA')
            if state:
                node = next((n for n in walk(state) if str(n.get('aweme_id','')) == ident and ('video' in n or 'images' in n)), None)
            if node:
                break
        if not node:
            raise ValueError('抖音未返回媒体数据；可在浏览器正常访问后，导入你自己的 Cookie 文件重试。')
        items=[]
        video=node.get('video') or {}
        addresses=[(r.get('play_addr') or {},r.get('gear_name','视频'),r.get('bit_rate',0)) for r in (video.get('bit_rate') or [])]
        addresses.append((video.get('play_addr',{}),'视频',0))
        for addr,label,bitrate in addresses:
            media=first_url(addr)
            if media:
                # Same public playback route selection observed in the APK.
                parsed=urllib.parse.urlsplit(media)
                path=re.sub(r'/playwm(?=/|$)', '/play', parsed.path)
                query=urllib.parse.parse_qsl(parsed.query,keep_blank_values=True)
                if path != parsed.path:
                    query=[(k,v) for k,v in query if k != 'watermark']
                media=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,path,urllib.parse.urlencode(query),parsed.fragment))
                items.append({'name':str(label),'kind':'video','url':media,'ext':'mp4','bitrate':bitrate or 0,
                    'watermark':'公开无水印候选；需检查画面','headers':{'User-Agent':UA,'Referer':current}})
        items.sort(key=lambda x:x.get('bitrate',0),reverse=True)
        for i,image in enumerate(node.get('images') or []):
            media=first_url(image)
            if media:
                items.append({'name':f'图片 {i+1}','kind':'image','url':media,'ext':'webp','watermark':'未核验','headers':{'User-Agent':UA,'Referer':current}})
        cover=first_url(video.get('origin_cover')) or first_url(video.get('cover'))
        if cover:
            items.append({'name':'封面','kind':'image','url':cover,'ext':'jpg','watermark':'未核验','headers':{'User-Agent':UA,'Referer':current}})
        result=self.result(url,node.get('desc'), '抖音',items)
        result.update(video_id=ident,canonical_url='https://www.douyin.com/video/'+ident)
        result['duration']=(video.get('duration') or 0)/1000 or None
        return result

    def xiaohongshu(self,url):
        final,page=self.page(url,'https://www.xiaohongshu.com/')
        state=json_assignment(page,'__INITIAL_STATE__')
        ident=urllib.parse.urlsplit(final).path.rstrip('/').split('/')[-1]
        candidates=[n for n in walk(state) if isinstance(n.get('note'),dict)]
        note=next((n['note'] for n in candidates if str(n['note'].get('noteId'))==ident),None)
        if not note:
            note=next((n for n in walk(state) if str(n.get('noteId'))==ident and ('video' in n or 'imageList' in n)),None)
        if not note:
            raise ValueError('小红书页面未返回当前作品数据；可能需要正常登录。')
        items=[]
        for n in walk(note.get('video',{})):
            key=n.get('originVideoKey')
            if isinstance(key,str) and key:
                items.append({'name':'页面原始视频','kind':'video','url':'https://sns-video-bd.xhscdn.com/'+key.lstrip('/'),'ext':'mp4','watermark':'页面原始源候选；需检查画面'})
            media=n.get('masterUrl')
            if isinstance(media,str) and media.startswith('http'):
                items.append({'name':'公开视频','kind':'video','url':media,'ext':'mp4','watermark':'未核验'})
        for i,image in enumerate(note.get('imageList') or []):
            media=first_url(image.get('urlDefault')) or first_url(image.get('urlPre')) or first_url(image.get('infoList'))
            if media: items.append({'name':f'图片 {i+1}','kind':'image','url':media,'ext':'webp','watermark':'未核验'})
        cover=first_url((note.get('video') or {}).get('cover'))
        if cover: items.append({'name':'封面','kind':'image','url':cover,'ext':'jpg','watermark':'未核验'})
        for item in items: item['headers']={'User-Agent':UA,'Referer':final}
        result=self.result(url,note.get('title') or note.get('desc'),'小红书',items)
        result.update(video_id=ident,canonical_url='https://www.xiaohongshu.com/explore/'+ident)
        return result

    def kuaishou(self,url):
        final,page=self.page(url,'https://www.kuaishou.com/')
        state=json_assignment(page,'INIT_STATE')
        node=next((n['photo'] for n in walk(state) if isinstance(n.get('photo'),dict)),None)
        if not node: raise ValueError('快手页面未返回媒体；可能需要正常登录。')
        items=[]
        for n in walk(node.get('mainMvUrls',[])):
            media=first_url(n)
            if media: items.append({'name':n.get('qualityLabel','公开视频'),'kind':'video','url':media,'ext':'mp4','watermark':'未核验'})
        for n in walk(node.get('manifest',{})):
            if n.get('url') and n.get('mimeType','video').startswith('video'):
                media=first_url(n)
                if media: items.append({'name':'公开视频','kind':'video','url':media,'ext':'mp4','watermark':'未核验'})
        cover=first_url(node.get('coverUrls')) or first_url(node.get('webpCoverUrls'))
        if cover: items.append({'name':'封面','kind':'image','url':cover,'ext':'jpg','watermark':'未核验'})
        for item in items: item['headers']={'User-Agent':UA,'Referer':final}
        return self.result(url,node.get('caption'),'快手',items)

    def ytdlp(self,url,log):
        from yt_dlp import YoutubeDL
        opts={'quiet':True,'no_warnings':True,'noplaylist':True,'socket_timeout':25,'retries':1,
              'writesubtitles':True,'writeautomaticsub':True,'subtitleslangs':['en.*','zh.*','cmn.*'],**engine_runtime_options()}
        log('使用本地 yt-dlp 解析…')
        with YoutubeDL(opts) as ydl:
            for cookie in self.jar: ydl.cookiejar.set_cookie(cookie)
            info=ydl.extract_info(url,download=False)
            preview=preview_from_info(info,ydl.cookiejar)
        if info.get('is_live'): raise ValueError('当前入口只处理点播作品。')
        if info.get('has_drm'): raise ValueError('媒体受 DRM 保护，当前工具不支持。')
        item={'name':'最佳可用视频（自动合并音轨）','kind':'ytdlp','url':url,'ext':'mp4','watermark':'未核验','preview':preview}
        result=self.result(url,info.get('title'),info.get('extractor_key','yt-dlp'),[item])
        result.update(preview=preview,duration=info.get('duration'),author=info.get('uploader'),video_id=info.get('id'),canonical_url=info.get('webpage_url') or url,
            subtitles=caption_tracks(info,ydl.cookiejar))
        return result

    def bilibili(self,url):
        match=re.search(r'(BV[0-9A-Za-z]+)',url)
        if not match:
            final,_=self.page(url,'https://www.bilibili.com/')
            match=re.search(r'(BV[0-9A-Za-z]+)',final)
        if not match:raise ValueError('未识别到 B 站视频编号，请使用单个视频分享链接。')
        bvid=match.group(1)
        headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36','Referer':'https://www.bilibili.com/'}
        def api(path,params):
            req=urllib.request.Request('https://api.bilibili.com/'+path+'?'+urllib.parse.urlencode(params),headers=headers)
            with self.client.open(req,timeout=25) as r:data=json.loads(r.read(8*1024*1024))
            if data.get('code')!=0:raise ValueError('B 站未返回可用公开媒体：'+str(data.get('message','请求失败')))
            return data['data']
        view=api('x/web-interface/view',{'bvid':bvid})
        page=int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('p',['1'])[0])
        pages=view.get('pages') or []
        if not 1<=page<=len(pages):raise ValueError('B 站分 P 编号无效。')
        cid=pages[page-1]['cid']
        play=api('x/player/playurl',{'bvid':bvid,'cid':cid,'qn':64,'fnval':16,'fourk':0})
        formats=[]
        for v in (play.get('dash') or {}).get('video') or []:
            formats.append({'url':v.get('baseUrl') or v.get('base_url'),'ext':'mp4','vcodec':v.get('codecs','avc1'),'acodec':'none','height':v.get('height'), 'tbr':v.get('bandwidth',0)})
        for a in (play.get('dash') or {}).get('audio') or []:
            formats.append({'url':a.get('baseUrl') or a.get('base_url'),'ext':'m4a','vcodec':'none','acodec':a.get('codecs','mp4a'),'abr':a.get('bandwidth',0)})
        if not formats and len(play.get('durl') or [])==1:
            formats.append({'url':play['durl'][0]['url'],'ext':'mp4','vcodec':'avc1','acodec':'mp4a'})
        preview=preview_from_info({'formats':formats,'thumbnail':view.get('pic'),'http_headers':headers},self.jar)
        if not preview.get('video'):raise ValueError('B 站没有返回当前权限下可播放的视频。')
        item={'name':'公开视频（自动合并音轨）','kind':'ytdlp','url':url,'ext':'mp4','preview':preview,'resolved_streams':True,'watermark':'未核验'}
        result=self.result(url,view['title'],'B站',[item])
        tracks=[]
        try:
            player=api('x/player/wbi/v2',{'bvid':bvid,'cid':cid})
            for caption in (player.get('subtitle') or {}).get('subtitles') or []:
                address=caption.get('subtitle_url');language=caption.get('lan','')
                if not address:continue
                if address.startswith('//'):address='https:'+address
                request=urllib.request.Request(address,headers=headers);self.jar.add_cookie_header(request)
                selected=dict(headers)
                if request.has_header('Cookie'):selected['Cookie']=request.get_header('Cookie')
                tracks.append({'language':language.removeprefix('ai-'),'automatic':language.startswith('ai-'),
                               'url':address,'ext':'json','headers':selected})
        except (ValueError,OSError):pass
        result.update(preview=preview,duration=pages[page-1].get('duration'),subtitles=tracks,video_id=bvid,author=(view.get('owner') or {}).get('name'))
        return result

def caption_tracks(info,jar=None):
    tracks=[]
    for field in ('subtitles','automatic_captions'):
        available=info.get(field) or {}
        for language,entries in available.items():
            if not entries or language in ('live_chat','danmaku'):continue
            for entry in sorted(entries,key=lambda e:({'json3':0,'json':1,'vtt':2,'srt':3,'ttml':4}.get(e.get('ext'),9))):
                if not (entry.get('url') or entry.get('data')):continue
                headers={**(info.get('http_headers') or {}),**(entry.get('http_headers') or {})}
                if entry.get('url') and jar:
                    request=urllib.request.Request(entry['url'],headers=headers);jar.add_cookie_header(request)
                    if request.has_header('Cookie'):headers['Cookie']=request.get_header('Cookie')
                tracks.append({'language':language,'automatic':field=='automatic_captions','ext':entry.get('ext','vtt'),
                    'url':entry.get('url'),'data':entry.get('data'),'headers':headers})
    def rank(track):
        lang=track['language'].lower()
        return (0 if lang.startswith(('zh','cmn')) else 1 if lang.startswith('en') else 2,track['automatic'])
    return sorted(tracks,key=rank)

def preview_from_info(info,jar=None):
    """Keep only playable streams and a poster; URLs/headers remain in memory."""
    def resource(fmt):
        if not fmt or not fmt.get('url'):return None
        headers={**(info.get('http_headers') or {}),**(fmt.get('http_headers') or {})}
        if jar:
            request=urllib.request.Request(fmt['url'],headers=headers)
            jar.add_cookie_header(request)
            if request.has_header('Cookie'):headers['Cookie']=request.get_header('Cookie')
        return {'url':fmt['url'],'headers':headers,'ext':fmt.get('ext','mp4')}
    formats=[f for f in (info.get('formats') or []) if f.get('url') and not f.get('has_drm')
             and f.get('protocol','https') in ('http','https') and f.get('ext') in ('mp4','webm')]
    videos=[f for f in formats if f.get('vcodec','none')!='none']
    def rank(f):
        h=f.get('height') or 0
        return (str(f.get('vcodec','')).startswith(('avc1','h264')),h<=720,f.get('acodec','none')!='none',h,f.get('tbr') or 0)
    video=max(videos,key=rank,default=None)
    if video is None and info.get('url') and info.get('ext') in ('mp4','webm'):
        video=info
    audio=None
    if video and video.get('acodec','none')=='none':
        audios=[f for f in (info.get('formats') or []) if f.get('vcodec')=='none' and f.get('acodec','none')!='none'
                and f.get('protocol','https') in ('http','https') and f.get('url') and not f.get('has_drm')]
        audio=max(audios,key=lambda f:(f.get('ext')=='m4a',f.get('abr') or f.get('tbr') or 0),default=None)
    thumbnail=info.get('thumbnail') or next((t['url'] for t in reversed(info.get('thumbnails') or []) if t.get('url')),None)
    cover=resource({'url':thumbnail,'ext':'jpg'}) if thumbnail else None
    return {'video':resource(video),'audio':resource(audio),'cover':cover,
            'note':'' if video else '该站点没有返回浏览器可播放的直链，可使用“打开原网页”。'}

def safe_name(text):
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',text).strip(' .')[:90] or '素材'
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}: name='_'+name
    return name

def download_ranges(item,partial,total,progress,chunk_size=1024*1024,workers=4):
    """Fetch bounded byte ranges; publish only after every verified range lands."""
    lock=threading.Lock();stopped=threading.Event();done=0
    with partial.open('xb'):pass
    def chunk(start):
        nonlocal done
        if stopped.is_set():return
        end=min(start+chunk_size,total)-1;expected=end-start+1
        for attempt in range(2):
            try:
                request=urllib.request.Request(item['url'],headers={'User-Agent':UA,**item.get('headers',{}),
                    'Range':f'bytes={start}-{end}','Accept-Encoding':'identity'})
                with media_opener(item['url']).open(request,timeout=25) as response:
                    if response.status!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{total}':
                        raise ValueError('服务器返回的文件分段不匹配，未保存为完整文件。')
                    data=response.read(expected+1)
                    if len(data)!=expected:raise OSError('文件分段不完整')
                if stopped.is_set():return
                with partial.open('r+b',buffering=0) as target:
                    target.seek(start)
                    if target.write(data)!=expected:raise OSError('文件写入不完整')
                with lock:done+=expected;progress(done,total)
                return
            except (OSError,urllib.error.URLError):
                if attempt==1:stopped.set();raise
            except Exception:stopped.set();raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(chunk,start) for start in range(0,total,chunk_size)]
        try:
            for future in concurrent.futures.as_completed(futures):future.result()
        except Exception:
            stopped.set()
            for future in futures:future.cancel()
            raise
    if done!=total or partial.stat().st_size!=total:raise ValueError('分段下载不完整，临时文件已保留。')

def download(item,title,directory,progress=lambda done,total:None,cookie_file=None):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    if item.get('kind')=='ytdlp':
        from yt_dlp import YoutubeDL
        import imageio_ffmpeg
        def hook(d):
            if d.get('status')=='downloading': progress(d.get('downloaded_bytes',0),d.get('total_bytes') or d.get('total_bytes_estimate') or 0)
        opts={'outtmpl':str(directory/'%(title).90s [%(id)s].%(ext)s'),'format':'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best','merge_output_format':'mp4',**engine_runtime_options(),
            'ffmpeg_location':imageio_ffmpeg.get_ffmpeg_exe(),'noplaylist':True,'quiet':True,'progress_hooks':[hook],'socket_timeout':30,'retries':2}
        with YoutubeDL(opts) as ydl:
            if cookie_file:
                jar=http.cookiejar.MozillaCookieJar(str(cookie_file));jar.load(ignore_discard=True,ignore_expires=False)
                for cookie in jar: ydl.cookiejar.set_cookie(cookie)
            info=ydl.extract_info(item['url'],download=True)
            return Path(ydl.prepare_filename(info))
    filename=safe_name(item['filename']) if item.get('filename') else safe_name(title)+' - '+safe_name(item.get('name','文件'))+'.'+re.sub('[^a-zA-Z0-9]','',item.get('ext','bin'))
    target=directory/filename
    i=1
    while target.exists() or target.with_suffix(target.suffix+'.part').exists():
        target=directory/(Path(filename).stem+f' ({i})'+Path(filename).suffix); i+=1
    partial=target.with_suffix(target.suffix+'.part')
    expected=item.get('expected_size') or 0
    headers={'User-Agent':UA,**item.get('headers',{})}
    req=urllib.request.Request(item['url'],headers=headers)
    with media_opener(item['url']).open(req,timeout=35) as response:
        content_type=response.headers.get('Content-Type','').lower()
        if any(t in content_type for t in ('text/html','application/json')):
            raise ValueError('服务器返回网页或错误数据，未保存为视频；请重新解析或正常登录。')
        total=int(response.headers.get('Content-Length') or 0)
        if expected and total and total!=expected:
            raise ValueError('下载响应大小与所选原文件不一致，未保存。')
        if response.status==206:
            interval=re.fullmatch(r'bytes 0-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            if not interval or int(interval[1])+1!=int(interval[2]):
                raise ValueError('服务器仅返回部分文件，未将片段保存为完整文件。')
        done=0
        with partial.open('xb') as dst:
            while block:=response.read(256*1024):
                dst.write(block); done+=len(block); progress(done,total)
    if total and done!=total: raise ValueError('下载不完整，临时文件已保留。')
    if expected and done!=expected:raise ValueError('下载大小与原文件不一致，临时文件已保留。')
    if not done: raise ValueError('服务器返回空文件。')
    partial.replace(target)
    return target
