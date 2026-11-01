import hashlib,json,urllib.request
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local_tool'))
from paths import TOOLS_ROOT
repo='csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
root=TOOLS_ROOT/'models/sensevoice-small';root.mkdir(parents=True,exist_ok=True)
with urllib.request.urlopen('https://huggingface.co/api/models/'+repo+'?blobs=true',timeout=40) as response:meta=json.load(response)
records=[]
for name in ('model.int8.onnx','tokens.txt','LICENSE'):
    entry=next(v for v in meta['siblings'] if v['rfilename']==name);expected=(entry.get('lfs') or {}).get('sha256');path=root/name
    if not path.exists() or expected and hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=expected:
        print('Downloading',name,flush=True)
        with urllib.request.urlopen('https://huggingface.co/'+repo+'/resolve/'+meta['sha']+'/'+name,timeout=90) as response,path.with_suffix(path.suffix+'.part').open('wb') as target:
            while block:=response.read(1024*1024):target.write(block)
        part=path.with_suffix(path.suffix+'.part')
        if expected and hashlib.file_digest(part.open('rb'),'sha256').hexdigest()!=expected:raise ValueError('Model digest mismatch')
        part.replace(path)
    records.append({'name':name,'bytes':path.stat().st_size,'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()})
(root/'source.json').write_text(json.dumps({'source':'https://huggingface.co/'+repo,'revision':meta['sha'],'files':records},indent=2),encoding='utf-8')
print('Verified SenseVoice ready',flush=True)
