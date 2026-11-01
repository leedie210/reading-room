"""Shared timed extraction; screen text is never assumed to be a full transcript."""
import json
import re
import xml.etree.ElementTree as ET
from paths import DATA_ROOT
from learning_text import parse_cues, ocr_languages, glossary_line, complete_english, cue_metrics

def _chinese_complete(cues,duration):
    if not cues:return False
    if any(glossary_line(c['text']) for c in cues):return False
    if not duration or duration<45:return True
    chars=sum(len(re.findall(r'[\u4e00-\u9fff]',c['text'])) for c in cues)
    return chars/duration>=.6 and cue_metrics(cues,duration)['observed_ratio']>=.65

def extract_captions(source,item,work,log=lambda s:None,progress=lambda a,b:None,*,learning=False,roi=None,cancel=None):
    from media_exports import _fetch_track,_video_for_ocr,_audio_to
    from transcription import screen_text,transcribe_wav
    method=item.get('_transcript_method',source.get('_transcript_method','auto'))
    if method not in ('auto','speech','screen'):raise ValueError('字幕识别方式无效。')
    work.mkdir(parents=True,exist_ok=True)
    duration=float(source.get('duration') or 0);english=[];chinese=[];origins=[];artifacts=[];en_origin=zh_origin=''
    def check():
        if cancel is not None and cancel.is_set():raise InterruptedError('已停止，完成的字幕已保留。')
    def accept_screen(result,span):
        en,zh=ocr_languages(result.get('events',[]))
        return en,zh,bool(en and zh and complete_english(en,span) and _chinese_complete(zh,span))
    if method=='auto':
        for prefix in ('en','zh'):
            tracks=[t for t in source.get('subtitles',[]) if t.get('language','').lower().startswith(('zh','cmn') if prefix=='zh' else ('en',))]
            for track in sorted(tracks,key=lambda t:bool(t.get('automatic'))):
                check()
                try:
                    raw=_fetch_track(track);cues=parse_cues(raw,track.get('ext','vtt'))
                    valid=_chinese_complete(cues,duration) if prefix=='zh' else complete_english(cues,duration)
                    if not valid:continue
                    origin=('平台自动字幕' if track.get('automatic') else '平台字幕')+'（'+track.get('language',prefix)+'）'
                    artifacts.append({'label':'平台原字幕_'+prefix,'ext':track.get('ext','txt'),'text':raw.decode('utf-8-sig') if isinstance(raw,bytes) else raw})
                    if prefix=='en':english,en_origin=cues,origin
                    else:chinese,zh_origin=cues,origin
                    origins.append(origin);break
                except (ValueError,OSError,ET.ParseError):continue
    cached=None;partial=None
    mid=source.get('_material_id')
    cache_path=DATA_ROOT/'learning-work'/mid/'ocr-progress.json' if mid else None
    if method=='auto' and learning and cache_path and cache_path.exists():
        saved=json.loads(cache_path.read_text(encoding='utf-8'))
        if roi is None or saved.get('roi')==roi:
            if saved.get('complete'):cached=saved
            elif saved.get('version')==2:partial=saved
    need_screen=method=='screen' or method=='auto' and learning and not (english and chinese)
    if need_screen:
        check();result=None
        if cached:
            en,zh,valid=accept_screen(cached,duration)
            if valid:result=cached
            else:
                log('旧画面结果含注释或内容不足，重新定位真正的字幕区域。')
                partial=None
        if result is None:
            video=work/'subtitle-frames.mkv'
            try:
                if not video.exists():_video_for_ocr(source,item,video,log,progress)
                check()
                region=roi
                usable=True
                if method=='auto':
                    starts=[0]+([duration/2] if duration>120 else [])
                    usable=False
                    # A large middle title/glossary may outscore real subtitles below it.
                    # Retry the lower band before abandoning the video's original translation.
                    candidates=[region] if region is not None else [None,[0,.67,1,1]]
                    for candidate in candidates:
                        usable=True;region=candidate
                        for start in starts:
                            span=min(24,max(0,duration-start)) if duration else 24
                            try:
                                probe=screen_text(video,region,log,progress,bilingual=True,start_at=start,end_at=start+span,cancel=cancel)
                            except ValueError:
                                usable=False;break
                            en,zh,valid=accept_screen(probe,span)
                            if not valid or sum(len(c['text'].split()) for c in en)<min(12,span):usable=False;break
                            region=probe.get('roi')
                        if usable:break
                    if not usable:log('画面未通过连续中英字幕检查，跳过全片 OCR，改用原声。')
                if usable:
                    if partial and partial.get('roi')!=region:partial=None
                    def checkpoint(events,at,region):
                        if not cache_path:return
                        cache_path.parent.mkdir(parents=True,exist_ok=True)
                        state={'events':events,'at':at,'roi':region,'complete':duration>0 and at>=duration-.5,'version':2}
                        temporary=cache_path.with_suffix('.part');temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');temporary.replace(cache_path)
                    result=screen_text(video,region,log,progress,bilingual=True,checkpoint=checkpoint if learning else None,cancel=cancel,
                        previous=partial.get('events') if partial else None,start_at=max(0,partial.get('at',0)+.4) if partial else 0)
            except (ValueError,OSError,ImportError) as error:
                if method=='screen':raise
                log('画面字幕不可用，继续提取原声：'+str(error)[:100])
            finally:
                if cancel is None or not cancel.is_set():video.unlink(missing_ok=True)
        if result:
            en,zh,valid=accept_screen(result,duration)
            if method=='screen':
                valid=complete_english(en,duration) if en else _chinese_complete(zh,duration)
                if not valid:raise ValueError('框选区域的文字不足以代表完整字幕，请重选字幕区域或使用语音识别。')
            if valid:
                if not english and en:english,en_origin=en,'原视频英文画面字幕识别（需校对）'
                if not chinese and zh:chinese,zh_origin=zh,'原视频中文画面字幕识别（需校对）'
                artifacts.append({'label':'原视频画面字幕','ext':'json','text':json.dumps(result,ensure_ascii=False)})
                origins.append('原视频画面字幕识别（字幕检查通过，仍需校对）')
    check()
    speech=None
    if method=='speech' or method=='auto' and not english and (learning or not chinese):
        audio=work/'subtitle-speech.wav'
        try:
            _audio_to(source,item,audio,wav=True,log=log,progress=progress);check()
            speech=transcribe_wav(audio,source.get('title',''),log,progress,language='en' if learning else None)
            events=speech.get('events') or []
            if not events:raise ValueError('原声没有返回有效字幕，未标记为提取完成。')
            language=speech.get('language','en')
            origin='原声识别（'+speech.get('model','ASR')+'，需校对）'
            if language.startswith('zh'):
                if learning:raise ValueError('原声未识别为英语，请核对素材的英语学习分类。')
                chinese,zh_origin=events,origin
            else:english,en_origin=events,origin
            origins.append(origin)
            artifacts.append({'label':'原声识别','ext':'json','text':json.dumps(speech,ensure_ascii=False)})
        finally:audio.unlink(missing_ok=True)
    if not english and not chinese:raise ValueError('没有取得有效字幕，未生成成功结果。')
    primary=english or chinese
    quality={'complete':True,'basis':'full-audio' if speech else 'validated-captions','metrics':cue_metrics(primary,duration),'audio_processed_seconds':speech.get('processed_seconds',duration) if speech else None}
    return {'english':english,'chinese':chinese,'en_origin':en_origin,'zh_origin':zh_origin,'origins':origins,'artifacts':artifacts,
            'text':'\n'.join(c['text'] for c in primary),'language':'en' if english else 'zh','quality':quality,'method':method,'version':2}
