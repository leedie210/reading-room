from paths import TOOLS_ROOT, DATA_ROOT
"""Audio-only export, real caption text, and local speech transcription."""
import html
import json
import re
import subprocess
import tempfile
import threading
import wave
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from media_core import ROOT,UA,DownloadRedirect,safe_name
from preview_server import PreviewServer
from media_ranges import clip_range

def _work_dir():
    path=DATA_ROOT/'video-work';path.mkdir(parents=True,exist_ok=True);return path


def unique_target(directory,title,suffix):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    stem=safe_name(title)
    target=directory/(stem+suffix)
    i=1
    while target.exists() or target.with_suffix(target.suffix+'.part').exists():
        target=directory/(stem+f' ({i})'+suffix);i+=1
    return target

def _audio_to(source,item,target,wav=False,log=lambda s:None,progress=lambda a,b:None):
    import imageio_ffmpeg
    if item.get('kind') not in ('video','ytdlp'):raise ValueError('请选择视频资源后提取音频或字幕。')
    preview=item.get('preview') or source.get('preview') or {}
    if item.get('kind')=='video':preview={**preview,'video':item,'audio':None}
    role='audio' if preview.get('audio') else 'video'
    if not preview.get(role):raise ValueError('没有可读取的音轨地址，请重新解析或打开原网页。')
    server=PreviewServer(range_chunk_size=0)
    try:
        selected={**item,'preview':preview}
        url=server.register({**source,'preview':preview},selected)+role
        codec=['-ac','1','-ar','16000','-c:a','pcm_s16le','-f','wav'] if wav else ['-c:a','libmp3lame','-q:a','2','-f','mp3']
        args=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-nostdin','-y','-rw_timeout','30000000','-i',url,
              '-map','0:a:0','-vn',*codec,'-progress','pipe:1','-nostats',str(target)]
        log('正在读取音轨并提取音频…')
        with tempfile.TemporaryFile(dir=_work_dir()) as errors:
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,text=True,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        value=line.strip().split('=',1)[1]
                        if value.isdigit():progress(int(value)/1_000_000,float(source.get('duration') or 0))
                code=process.wait()
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdout.close()
            if code:
                raise ValueError('音轨读取失败，视频可能没有声音、媒体地址已过期或网络不可用；请重新解析后重试。')
        if not target.exists() or target.stat().st_size<44:raise ValueError('没有提取到有效音频。')
    finally:server.close()

def export_audio(source,item,directory,log=lambda s:None,progress=lambda a,b:None):
    target=unique_target(directory,source.get('title','素材'),' - 音频.mp3')
    partial=target.with_suffix(target.suffix+'.part')
    try:
        _audio_to(source,item,partial,log=log,progress=progress)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return target

def export_video(source,item,directory,log=lambda s:None,progress=lambda a,b:None):
    """Save already-resolved video/audio streams without a second site lookup."""
    import imageio_ffmpeg
    preview=item.get('preview') or source.get('preview') or {}
    if not preview.get('video'):raise ValueError('视频地址不可用，请重新解析。')
    server=PreviewServer(range_chunk_size=0)
    target=unique_target(directory,source.get('title','素材'),' - 视频.mp4')
    partial=target.with_suffix(target.suffix+'.part')
    try:
        url=server.register(source,item)
        args=[imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-nostdin','-y','-rw_timeout','30000000','-i',url+'video']
        if preview.get('audio'):args+=['-rw_timeout','30000000','-i',url+'audio','-map','0:v:0','-map','1:a:0']
        args+=['-c','copy','-movflags','+faststart','-f','mp4','-progress','pipe:1','-nostats',str(partial)]
        log('正在保存视频并合并音轨…')
        with tempfile.TemporaryFile(dir=_work_dir()) as errors:
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        value=line.strip().split('=',1)[1]
                        if value.isdigit():progress(int(value)/1_000_000,float(source.get('duration') or 0))
                code=process.wait()
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdout.close()
            if code or not partial.exists() or partial.stat().st_size<100:raise ValueError('视频保存失败，请重新解析或检查网络。')
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True);raise
    finally:server.close()

def export_clip(source,item,directory,log=lambda s:None,progress=lambda a,b:None):
    """Decode from the requested time, avoiding keyframe-rounded stream copies."""
    import imageio_ffmpeg
    requested=item.get('_clip_range') or {}
    selected=clip_range(requested.get('start'),requested.get('end'),source.get('duration'))
    start,end=selected['start'],selected['end'];length=end-start
    preview=item.get('preview') or source.get('preview') or {}
    if item.get('kind')=='video':preview={**preview,'video':item,'audio':None}
    if not preview.get('video'):raise ValueError('当前资源没有可截取的视频，请重新解析。')
    suffix=f' - 片段 {start:.3f}-{end:.3f}秒.mp4'
    target=unique_target(directory,source.get('title','视频'),suffix);partial=target.with_suffix('.mp4.part')
    server=PreviewServer()
    try:
        address=server.register({**source,'preview':preview},{**item,'preview':preview})
        args=[imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-nostdin','-y','-rw_timeout','30000000','-ss',str(start),'-i',address+'video']
        if preview.get('audio'):
            args+=['-rw_timeout','30000000','-ss',str(start),'-i',address+'audio','-map','0:v:0','-map','1:a:0']
        else:args+=['-map','0:v:0','-map','0:a:0?']
        args+=['-t',str(length),'-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
            '-c:a','aac','-b:a','192k','-movflags','+faststart','-f','mp4','-progress','pipe:1','-nostats',str(partial)]
        log(f'正在截取 {start:.3f}–{end:.3f} 秒，只保存所选片段…')
        with tempfile.TemporaryFile(dir=_work_dir()) as errors:
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:progress(min(float(line.split('=',1)[1])/1e6,length),length)
                        except ValueError:pass
                code=process.wait()
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdout.close()
        if code or not partial.exists() or partial.stat().st_size<100:raise ValueError('片段保存失败，请重新解析，或检查所选时间范围。')
        partial.replace(target);return target
    except Exception:partial.unlink(missing_ok=True);raise
    finally:server.close()

def caption_text(data,extension):
    text=data.decode('utf-8-sig','replace') if isinstance(data,bytes) else data
    lines=[]
    if extension in ('json','json3') or text.lstrip().startswith('{'):
        parsed=json.loads(text)
        if isinstance(parsed.get('body'),list):lines=[part.get('content','') for part in parsed['body']]
        elif isinstance(parsed.get('events'),list):
            lines=[''.join(segment.get('utf8','') for segment in event.get('segs',[])) for event in parsed['events']]
    elif extension in ('ttml','srv1','srv2','srv3','xml') or text.lstrip().startswith('<'):
        root=ET.fromstring(text)
        lines=[''.join(node.itertext()) for node in root.iter() if node.tag.rsplit('}',1)[-1] in ('text','p')]
    else:
        skip=False
        for line in text.splitlines():
            line=line.strip()
            if not line:skip=False;continue
            if line.startswith(('WEBVTT','Kind:','Language:','X-TIMESTAMP-MAP')):continue
            if line.startswith(('NOTE','STYLE','REGION')):skip=True;continue
            if skip or '-->' in line or line.isdigit():continue
            lines.append(line)
    cleaned=[]
    for line in lines:
        line=html.unescape(re.sub(r'<[^>]*>','',str(line))).strip()
        if line and (not cleaned or line!=cleaned[-1]):cleaned.append(line)
    return '\n'.join(cleaned).strip()

def _fetch_track(track):
    if track.get('data') is not None:return track['data']
    req=urllib.request.Request(track['url'],headers={'User-Agent':UA,**track.get('headers',{})})
    with urllib.request.build_opener(DownloadRedirect()).open(req,timeout=25) as r:
        data=r.read(8*1024*1024+1)
    if len(data)>8*1024*1024:raise ValueError('字幕数据过大。')
    return data

def _video_for_ocr(source,item,target,log,progress):
    import imageio_ffmpeg
    preview=item.get('preview') or source.get('preview') or {}
    if item.get('kind')=='video':preview={**preview,'video':item,'audio':None}
    if not preview.get('video'):raise ValueError('没有可读取的视频画面。')
    server=PreviewServer()
    try:
        address=server.register({**source,'preview':preview},{**item,'preview':preview})+'video'
        args=[imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-nostdin','-y','-rw_timeout','30000000','-i',address,
            '-map','0:v:0','-an','-vf',"fps=2.5,scale=w='min(1280,iw)':h=-2",'-c:v','libx264','-preset','ultrafast','-crf','20','-g','1','-f','matroska','-progress','pipe:1','-nostats',str(target)]
        log('正在临时读取视频画面，识别结束后自动清理…')
        with tempfile.TemporaryFile(dir=_work_dir()) as errors:
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for line in process.stdout:
                    if line.startswith('out_time_us='):
                        try:progress(float(line.split('=',1)[1])/1e6,float(source.get('duration') or 0))
                        except ValueError:pass
                code=process.wait()
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdout.close()
        if code or not target.exists() or target.stat().st_size<100:raise ValueError('画面读取失败，请重新解析视频链接后重试。')
    finally:server.close()

def export_transcript(source,item,directory,log=lambda s:None,progress=lambda a,b:None):
    from subtitle_pipeline import extract_captions
    from transcription import simplified
    from media_ranges import subtitle_roi
    if item.get('kind') not in ('video','ytdlp'):raise ValueError('请选择视频资源后提取字幕。')
    with tempfile.TemporaryDirectory(prefix='subtitle-',dir=_work_dir()) as temporary:
        result=extract_captions(source,item,Path(temporary),log,progress,learning=bool(item.get('_english_learning')),roi=subtitle_roi(item.get('_subtitle_roi')))
    target=unique_target(directory,source.get('title','视频'),' - 字幕文本.txt')
    target.write_text(simplified(result['text'])+'\n',encoding='utf-8-sig')
    return {'path':target,'origin':'；'.join(result['origins']),'language':result['language'],'captions':result}
