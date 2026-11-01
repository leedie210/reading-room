from paths import TOOLS_ROOT, DATA_ROOT
"""Local high-accuracy ASR and hard-subtitle OCR; no generative rewriting."""
import difflib
import functools
import gc
import json
import os
import re
import threading
import wave
from collections import Counter
from pathlib import Path
from media_core import ROOT
from media_ranges import subtitle_roi

_converter=None
_speech=None
_chinese=None
_ocr=None
_lock=threading.RLock()
_dll_handles=[]

def simplified(text):
    global _converter
    if _converter is None:
        from opencc import OpenCC
        _converter=OpenCC('t2s')
    return _converter.convert(str(text)).strip()

@functools.lru_cache(maxsize=4096)
def _sound(character):
    from pypinyin import lazy_pinyin,Style
    return lazy_pinyin(character,style=Style.NORMAL,strict=False)[0].lower()

def cross_check(speech,screen):
    """Use nearby printed subtitle evidence for short word corrections only.

    Keep spoken passages missing from the subtitle band; do not rewrite them
    or insert long screen-only passages such as background presentations.
    """
    a=simplified(speech);b=simplified(screen)
    positions=lambda t:[i for i,c in enumerate(t) if c.isalnum()]
    pa,pb=positions(a),positions(b)
    if not pa or not pb:return a,0
    ca=''.join(a[i].lower() for i in pa);cb=''.join(b[i].lower() for i in pb)
    operations=difflib.SequenceMatcher(None,ca,cb,autojunk=False).get_opcodes();pieces=[a[:pa[0]]];corrections=0
    def span(text,indexes,start,end):return text[indexes[start] if start<len(indexes) else len(text):indexes[end] if end<len(indexes) else len(text)]
    for n,(kind,i,j,k,l) in enumerate(operations):
        old=span(a,pa,i,j)
        left=operations[n-1] if n else None;right=operations[n+1] if n+1<len(operations) else None
        anchored=(left is not None and left[0]=='equal' and left[2]-left[1]>=3) or (right is not None and right[0]=='equal' and right[2]-right[1]>=3)
        if kind=='replace' and anchored and 0<j-i<=16 and 0<l-k<=16 and .3<=(l-k)/(j-i)<=3:
            # Keep punctuation and unmatched spoken words when a caption omits
            # fillers or sentence boundaries. Replace aligned characters only.
            printed=[b[pb[x]] for x in range(k,l)]
            offsets=[x for x,c in enumerate(old) if c.isalnum()];revised=list(old)
            sounds=difflib.SequenceMatcher(None,[_sound(old[x]) for x in offsets],[_sound(c) for c in printed],autojunk=False)
            for change,x,y,u,v in sounds.get_opcodes():
                if change=='equal' or change=='replace' and y-x==v-u and y-x<=4:
                    for offset,character in zip(offsets[x:y],printed[u:v]):revised[offset]=character
            pieces.append(''.join(revised));corrections+=1
        elif kind=='insert':
            # An omission of a few printed characters can be restored safely
            # between matching words; larger screen-only blocks are ignored.
            bounded=left is not None and right is not None and left[0]=='equal' and right[0]=='equal' and left[2]-left[1]>=3 and right[2]-right[1]>=3
            if bounded and l-k<=6:pieces.append(re.sub(r'\s+','',span(b,pb,k,l)));corrections+=1
        else:pieces.append(old)
    return ''.join(pieces).strip(),corrections

def prepare_cuda():
    if os.name!='nt' or _dll_handles:return
    for root in (TOOLS_ROOT/'video-packages/nvidia',TOOLS_ROOT/'python-packages/nvidia'):
        if not root.exists():continue
        folders=sorted({str(p.parent) for p in root.rglob('*.dll')})
        for folder in folders:
            _dll_handles.append(os.add_dll_directory(folder))
        if folders:os.environ['PATH']=os.pathsep.join(folders)+os.pathsep+os.environ.get('PATH','')

def release_models():
    global _speech,_chinese
    with _lock:
        _speech=None;_chinese=None;gc.collect()


def transcribe_wav(path,title='',log=lambda s:None,progress=lambda a,b:None,language=None):
    global _speech
    import numpy as np
    with wave.open(str(path),'rb') as audio:
        if audio.getnchannels()!=1 or audio.getframerate()!=16000 or audio.getsampwidth()!=2:raise ValueError('识别音频应为单声道 16 kHz PCM。')
        samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.float32)/32768.0
    if language is None and (TOOLS_ROOT/'models/sensevoice-small/model.int8.onnx').exists():
        # Detect the spoken language; a Chinese video title says nothing about it.
        global _chinese
        import sherpa_onnx
        with _lock:
            if _chinese is None:
                model=TOOLS_ROOT/'models/sensevoice-small'
                _chinese=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model/'model.int8.onnx'),tokens=str(model/'tokens.txt'),num_threads=4,language='auto',use_itn=True)
            languages=[]
            for fraction in (.1,.5,.8):
                begin=max(0,min(int(len(samples)*fraction),len(samples)-16000*5))
                stream=_chinese.create_stream();stream.accept_waveform(16000,samples[begin:begin+16000*5]);_chinese.decode_stream(stream)
                detected=(getattr(stream.result,'lang','') or '').replace('<|','').replace('|>','')
                if detected:languages.append(detected)
            if languages:language=Counter(languages).most_common(1)[0][0]
            if language not in ('zh','en','ja','ko'):language=None
    if language=='zh' and (TOOLS_ROOT/'models/sensevoice-small/model.int8.onnx').exists():
        return _chinese_speech(samples,log,progress)
    model_path=TOOLS_ROOT/'models/faster-whisper-large-v3'
    if not (model_path/'model.bin').exists():raise ValueError('高精度语音模型尚未准备好，请运行 scripts/download_models.py whisper。')
    with _lock:
        if _speech is None:
            prepare_cuda()
            from faster_whisper import WhisperModel
            import ctranslate2
            device='cuda' if ctranslate2.get_cuda_device_count()>0 else 'cpu'
            log('正在加载高精度语音模型…')
            try:_speech=WhisperModel(str(model_path),device=device,compute_type='int8_float16' if device=='cuda' else 'int8',cpu_threads=6,local_files_only=True)
            except RuntimeError:
                log('显卡暂不可用，改用处理器识别，耗时会更长…')
                _speech=WhisperModel(str(model_path),device='cpu',compute_type='int8',cpu_threads=6,local_files_only=True)
        log('正在按实际语音语言识别；中文统一输出简体…')
        keywords=simplified(re.sub(r'#[^\s#]+','',title))[:100]
        segments,info=_speech.transcribe(samples,task='transcribe',language=language,beam_size=5,best_of=5,
            vad_filter=True,vad_parameters={'min_silence_duration_ms':400,'speech_pad_ms':200},
            condition_on_previous_text=True,initial_prompt='Transcribe the English speech faithfully with punctuation.' if language=='en' else None,
            hotwords=keywords if language=='zh' else None,temperature=(0.0,.2,.4),no_speech_threshold=.6)
        lines=[];uncertain=[];events=[]
        for segment in segments:
            text=simplified(segment.text)
            if text:
                lines.append(text)
                events.append({'start':segment.start,'end':segment.end,'text':text})
                if segment.avg_logprob<-.65:uncertain.append({'start':segment.start,'end':segment.end,'text':text})
            log(f'识别语音：{segment.end:.0f} / {info.duration:.0f} 秒')
            progress(segment.end,info.duration)
    text='\n'.join(lines).strip()
    if not text:raise ValueError('未识别出清晰语音，可尝试“读取画面字幕”并框选字幕区域。')
    return {'text':text,'language':info.language,'events':events,'uncertain':uncertain,'model':'Whisper large-v3'}

def _chinese_speech(samples,log,progress):
    global _chinese
    import sherpa_onnx
    from faster_whisper.vad import get_speech_timestamps,VadOptions
    with _lock:
        if _chinese is None:
            log('正在加载中文专用语音模型…');model=TOOLS_ROOT/'models/sensevoice-small'
            _chinese=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(model/'model.int8.onnx'),tokens=str(model/'tokens.txt'),num_threads=4,language='auto',use_itn=True)
        segments=get_speech_timestamps(samples,VadOptions(max_speech_duration_s=30,min_silence_duration_ms=500,speech_pad_ms=400))
        lines=[];events=[];language='zh'
        for segment in segments:
            stream=_chinese.create_stream();stream.accept_waveform(16000,samples[segment['start']:segment['end']]);_chinese.decode_stream(stream)
            text=simplified(re.sub(r'<\|[^>]+\|>','',stream.result.text))
            if text:
                lines.append(text);events.append({'start':segment['start']/16000,'end':segment['end']/16000,'text':text})
            language=(getattr(stream.result,'lang',language) or language).replace('<|','').replace('|>','')
            done=segment['end']/16000;duration=len(samples)/16000
            log(f'中文语音识别：{done:.0f} / {duration:.0f} 秒');progress(done,duration)
    if not lines:raise ValueError('没有识别出清晰语音，可改用画面字幕。')
    return {'text':'\n'.join(lines),'language':language,'events':events,'uncertain':[],'model':'SenseVoiceSmall'}

def ocr_engine():
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR
        _ocr=RapidOCR(params={'Global.text_score':.8,'Global.log_level':'error','Det.limit_type':'max','Det.limit_side_len':960,
            'EngineConfig.onnxruntime.intra_op_num_threads':4,'EngineConfig.onnxruntime.inter_op_num_threads':1})
    return _ocr

def locate_subtitles(cap,engine,duration):
    """Locate a changing text line shared by multiple sampled frames."""
    import cv2
    candidates=[];samples=min(16,max(4,int(duration/3)))
    for index in range(samples):
        cap.set(cv2.CAP_PROP_POS_MSEC,(index+.5)*duration/samples*1000);ok,frame=cap.read()
        if not ok:continue
        h,w=frame.shape[:2];crop=frame[int(h*.35):];scale=min(1,1280/w)
        if scale<1:crop=cv2.resize(crop,None,fx=scale,fy=scale)
        output=engine(crop)
        if output.txts is None:continue
        for text,score,box in zip(output.txts,output.scores,output.boxes):
            text=simplified(text)
            if score<.85 or len(re.sub(r'\W','',text))<3 or re.fullmatch(r'[\d\s.,%]+',text):continue
            xs=[p[0]/scale/w for p in box];ys=[p[1]/scale/h+.35 for p in box]
            height=max(ys)-min(ys);center=(sum(xs)/4,sum(ys)/4)
            if height<.012 or abs(center[0]-.5)>.3:continue
            candidates.append({'sample':index,'text':text,'y':center[1],'height':height,'score':float(score)})
    best=[];best_score=0
    for candidate in candidates:
        group=[c for c in candidates if abs(c['y']-candidate['y'])<.028]
        support=len({c['sample'] for c in group});unique=len({c['text'] for c in group})
        if support<max(3,samples*.3) or unique<3:continue
        score=sum(max(c['score']*min(c['height']/.025,2) for c in group if c['sample']==i) for i in {c['sample'] for c in group})
        if score>best_score:best,best_score=group,score
    if not best:return None
    ys=sorted(c['y'] for c in best);heights=sorted(c['height'] for c in best);pad=heights[len(heights)//2]*.7+.008
    return [0,float(max(0,ys[len(ys)//10]-pad)),1,float(min(1,ys[min(len(ys)-1,len(ys)*9//10)]+pad))]

def screen_text(path,roi=None,log=lambda s:None,progress=lambda a,b:None,*,bilingual=False,start_at=0,previous=None,checkpoint=None,cancel=None):
    """Read only the selected subtitle band and merge repeated subtitle frames."""
    import cv2
    region=subtitle_roi(roi)
    cap=cv2.VideoCapture(str(path))
    if not cap.isOpened():raise ValueError('视频画面无法读取，请重新解析。')
    fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if fps<=0 or count<=0:cap.release();raise ValueError('无法取得视频的有效时长。')
    duration=count/fps;frames=[];frequencies=Counter();step=.4;last_emphasis=-10
    last_mask=None;last_entries=[];last_ocr=-10;last_checkpoint=start_at
    def grouped():
        groups=[dict(group) for group in (previous or [])]
        stationary={text for text,n in frequencies.items() if roi is None and len(frames)>=8 and n>len(frames)*.65}
        for frame in frames:
            entries=[entry for entry in frame['entries'] if entry['text'] not in stationary]
            if not entries:continue
            text='\n'.join(e['text'] for e in sorted(entries,key=lambda v:v['y']));score=sum(e['score'] for e in entries)/len(entries)
            if groups and frame['time']-groups[-1]['end']<=step*2 and (difflib.SequenceMatcher(None,text,groups[-1]['text']).ratio()>=.82 or text in groups[-1]['text'] or groups[-1]['text'] in text):
                old=groups[-1];old['end']=frame['time']
                if score*len(text)>old['score']*len(old['text']):old.update(text=text,score=score)
            else:groups.append({'start':frame['time'],'end':frame['time'],'text':text,'score':score})
        return groups
    try:
        with _lock:
            engine=ocr_engine()
            if region is None:
                log('正在定位画面中的字幕行…');region=locate_subtitles(cap,engine,duration)
                if region is None:raise ValueError('没有检测到稳定的画面字幕，请在预览页框选字幕位置。')
                if bilingual:region=[region[0],max(0,region[1]-.025),region[2],min(1,region[3]+.115)]
            for index in range(max(0,int(start_at/step)),int(duration/step)+1):
                if cancel is not None and cancel.is_set():
                    if checkpoint:checkpoint(grouped(),max(start_at,index*step-step),region)
                    raise InterruptedError('已停止，已完成的字幕进度会保留。')
                stamp=index*step;cap.set(cv2.CAP_PROP_POS_MSEC,stamp*1000);ok,frame=cap.read()
                if not ok:continue
                height,width=frame.shape[:2]
                left,top,right,bottom=region
                image=frame[int(top*height):max(int(top*height)+1,int(bottom*height)),int(left*width):max(int(left*width)+1,int(right*width))]
                if image.shape[1]>1280:image=cv2.resize(image,None,fx=1280/image.shape[1],fy=1280/image.shape[1])
                reuse=False
                if bilingual:
                    mask=cv2.resize((cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)>205).astype('uint8'),(640,60))
                    reuse=last_mask is not None and stamp-last_ocr<1.2 and float(cv2.absdiff(mask,last_mask).mean())<.006
                result=None if reuse else engine(image);entries=list(last_entries) if reuse else []
                if result is not None and result.txts is not None:
                    for text,score,box in zip(result.txts,result.scores,result.boxes):
                        text=simplified(text)
                        if score<.85 or len(re.sub(r'\W','',text))<3 or re.fullmatch(r'[\d\s.,%]+',text):continue
                        center=float(sum(point[1] for point in box)/len(box))/image.shape[0]
                        entries.append({'text':text,'score':float(score),'y':center})
                if bilingual and not reuse:last_mask=mask;last_entries=list(entries);last_ocr=stamp
                if not bilingual and not entries and roi is None and stamp-last_emphasis>=.7:
                    # Emphasis cards can replace the normal subtitle line.
                    last_emphasis=stamp;middle=frame[int(height*.25):int(height*.78)]
                    scale=min(1,1280/width)
                    if scale<1:middle=cv2.resize(middle,None,fx=scale,fy=scale)
                    alternate=engine(middle)
                    if alternate.txts is not None:
                        for text,score,box in zip(alternate.txts,alternate.scores,alternate.boxes):
                            text=simplified(text);ys=[p[1]/scale/height+.25 for p in box]
                            if score<.88 or len(re.sub(r'\W','',text))<3 or max(ys)-min(ys)<max(.025,(bottom-top)*.35):continue
                            entries.append({'text':text,'score':float(score),'y':sum(ys)/len(ys),'emphasis':True})
                frequencies.update({e['text'] for e in entries})
                frames.append({'time':stamp,'entries':entries})
                if index%10==0:log(f'读取画面字幕：{stamp:.0f} / {duration:.0f} 秒')
                progress(min(stamp,duration),duration)
                if checkpoint and stamp-last_checkpoint>=60:
                    checkpoint(grouped(),stamp,region);last_checkpoint=stamp
    finally:cap.release()
    # Ignore stationary marks; with a user-drawn region retain all subtitle rows.
    static={text for text,n in frequencies.items() if roi is None and len(frames)>=8 and n>len(frames)*.65}
    dominant=None
    if roi is None:
        bands=Counter()
        for frame in frames:
            for entry in frame['entries']:
                if entry['text'] not in static and not entry.get('emphasis'):bands[round(entry['y']*20)]+=min(len(entry['text']),25)*entry['score']
        if bands:dominant=bands.most_common(1)[0][0]/20
    if bilingual:
        groups=grouped()
        text='\n'.join(group['text'] for group in groups).strip()
        if not text:raise ValueError('没有读到清晰的字幕，请框住中英两行字幕后重试。')
        if checkpoint:checkpoint(groups,duration,region)
        return {'text':text,'language':'multi','events':groups,'model':'RapidOCR','uncertain':[],'roi':region}
    groups=[]
    for frame in frames:
        entries=[e for e in frame['entries'] if e['text'] not in static and (e.get('emphasis') or dominant is None or abs(e['y']-dominant)<.25)]
        if not entries:continue
        text='\n'.join(e['text'] for e in sorted(entries,key=lambda v:v['y']));score=sum(e['score'] for e in entries)/len(entries)
        if groups and frame['time']-groups[-1]['end']<=step*2 and (
            difflib.SequenceMatcher(None,text,groups[-1]['text']).ratio()>=.82 or text in groups[-1]['text'] or groups[-1]['text'] in text):
            previous=groups[-1];previous['end']=frame['time']
            if score*len(text)>previous['score']*len(previous['text']):previous.update(text=text,score=score)
        else:groups.append({'start':frame['time'],'end':frame['time'],'text':text,'score':score})
    lines=[]
    for group in groups:
        text=group['text']
        if not lines or text!=lines[-1]:lines.append(text)
    text='\n'.join(lines).strip()
    if not text:raise ValueError('没有找到清晰的画面字幕。请在预览页框选字幕位置，或改用语音识别。')
    return {'text':simplified(text),'language':'zh','events':groups,'model':'RapidOCR','uncertain':[],'roi':region}
