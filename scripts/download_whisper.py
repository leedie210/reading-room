"""Install the official CTranslate2 large-v3 model, verifying published hashes."""
import hashlib,json,time,urllib.request
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local_tool'))
from paths import TOOLS_ROOT
repo='Systran/faster-whisper-large-v3'
root=TOOLS_ROOT/'models/faster-whisper-large-v3'
root.mkdir(parents=True,exist_ok=True)
headers={'User-Agent':'Local-Video-Tool/2.0'}
with urllib.request.urlopen(urllib.request.Request(f'https://huggingface.co/api/models/{repo}?blobs=true',headers=headers),timeout=40) as r:meta=json.load(r)
revision=meta['sha'];records=[]
for name in ('config.json','tokenizer.json','vocabulary.json','vocabulary.txt','preprocessor_config.json','model.bin'):
    entry=next((v for v in meta['siblings'] if v['rfilename']==name),None)
    if entry is None:continue
    expected=(entry.get('lfs') or {}).get('sha256');target=root/name
    if not (target.exists() and (not expected or hashlib.file_digest(target.open('rb'),'sha256').hexdigest()==expected)):
        part=target.with_suffix(target.suffix+'.part');last=0
        print('Downloading',name,flush=True)
        req=urllib.request.Request(f'https://huggingface.co/{repo}/resolve/{revision}/{name}',headers=headers)
        with urllib.request.urlopen(req,timeout=90) as response,part.open('wb') as dst:
            while block:=response.read(1024*1024):
                dst.write(block)
                if dst.tell()-last>=100*1024*1024:last=dst.tell();print(name,round(last/1048576),'MiB',flush=True)
        if expected and hashlib.file_digest(part.open('rb'),'sha256').hexdigest()!=expected:raise ValueError('Published model hash mismatch')
        part.replace(target)
    records.append({'name':name,'bytes':target.stat().st_size,'sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest(),'expected_sha256':expected})
(root/'source.json').write_text(json.dumps({'source':'https://huggingface.co/'+repo,'revision':revision,'files':records},indent=2),encoding='utf-8')
print('Verified large-v3 ready',flush=True)
