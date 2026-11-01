"""Explicit personal selections, durable source anchors and Obsidian exports."""
import hashlib
import json
import re
from pathlib import Path
from media_library import now

class ReadingMarks:
    def __init__(self, library):
        self.library=library
        with library.lock,library.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS reading_marks(
              id TEXT PRIMARY KEY,material_id TEXT NOT NULL,row_id TEXT NOT NULL,
              start INTEGER NOT NULL,end INTEGER NOT NULL,text TEXT NOT NULL,kind TEXT NOT NULL,
              context TEXT NOT NULL,sequence INTEGER NOT NULL,video_time REAL,created TEXT NOT NULL)''')

    def list(self, material_id=None):
        with self.library.connect() as db:
            result=[dict(r) for r in db.execute('''SELECT r.*,m.title,m.source_url,m.english FROM reading_marks r
                JOIN materials m ON m.id=r.material_id WHERE (? IS NULL OR r.material_id=?) ORDER BY r.created DESC,r.id''',(material_id,material_id))]
        documents={}
        for mark in result:
            mid=mark['material_id']
            if mid not in documents:documents[mid]={r['id']:r['en'] for r in (self.library.document(mid) or {}).get('rows',[])}
            mark['valid']=documents[mid].get(mark['row_id'])==mark['context']
        return result

    def add(self, material_id, data):
        if not self.library.get(material_id)['english']:raise ValueError('请先将素材归为英语学习。')
        rows=(self.library.document(material_id) or {}).get('rows',[])
        index=next((i for i,r in enumerate(rows) if r['id']==data.get('row')),None)
        if index is None:raise ValueError('原文句段不存在，请刷新后重试。')
        row=rows[index];start,end=data.get('start'),data.get('end');kind=data.get('kind')
        if kind not in ('word','sentence') or type(start)!=int or type(end)!=int or not 0<=start<end<=len(row['en']):raise ValueError('选区无效，请重新选择。')
        text=row['en'][start:end]
        if not text.strip() or text!=data.get('text'):raise ValueError('原文已变化，请刷新后重新选择。')
        identity=[material_id,row['id'],row['en'],start,end,kind]
        ident=hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()[:24]
        with self.library.lock,self.library.connect() as db:
            db.execute('INSERT OR IGNORE INTO reading_marks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (ident,material_id,row['id'],start,end,text,kind,row['en'],index+1,row.get('start'),now()))
        # The database commit precedes export. A failed export must never lose the selection.
        try:
            paths=self.export()
            return {'id':ident,'marks':self.list(material_id),'exported':True,'message':'已标红，已归档到 Obsidian。'}
        except (OSError,ValueError) as error:
            return {'id':ident,'marks':self.list(material_id),'exported':False,'message':'标记已保存；Obsidian 导出未完成：'+str(error)[:180]}

    def _write(self, folder, name, text):
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/name;key='marks-export:'+str(folder)+':'+name
        saved=self.library.cache_get(key) or {};digest=hashlib.sha256(text.encode()).hexdigest()
        def fingerprint(path):return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
        previous=Path(saved.get('path',''))
        if previous.parent==folder and previous.is_file() and fingerprint(previous)==saved.get('hash'):target=previous
        if target.is_file() and fingerprint(target) not in (digest,saved.get('hash')):
            from datetime import datetime
            target=folder/(Path(name).stem+'（新生成_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'）.md')
        if not target.is_file() or fingerprint(target)!=digest:target.write_text(text,encoding='utf-8',newline='\n')
        self.library.cache_put(key,{'path':str(target),'hash':digest})
        return target

    def export(self):
        with self.library.lock:
            marks=self.list();folder=self.library.vault/'04_视频精读'/'我的标记';paths={}
            def entry(mark):
                # Literal selection/context: HTML is escaped so subtitles cannot inject embeds.
                literal=lambda s:str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
                time=mark['video_time'];stamp=f'{int(time)//60:02}:{int(time)%60:02}' if time is not None else '无时间轴'
                source=self.library.folder(mark['material_id'])
                link=source.relative_to(self.library.vault).as_posix()+'/阅读正文（工具生成）'
                return [literal(mark['context']),'',f"来源：[[{link}|{mark['title']}]] · #{mark['sequence']:03} · {stamp}",
                    f"原视频：{mark['source_url']}",'',('原文已更新，以上保留标记时上下文。\n' if not mark['valid'] else '')+f"^mark-{mark['id']}",'']
            for kind,name in [('word','重点词汇'),('sentence','标记句段')]:
                chosen=[m for m in marks if m['kind']==kind];groups={}
                for mark in chosen:
                    key=re.sub(r'\s+',' ',mark['text'].strip()).casefold() if kind=='word' else mark['id']
                    groups.setdefault(key,[]).append(mark)
                lines=['# '+name,'','由主动标记收录；词形和语境分别保留，不代表已掌握。','']
                for group in groups.values():
                    lines+=['## '+group[0]['text'].replace('\n',' '),'']
                    for mark in group:lines+=entry(mark)
                paths[kind]=str(self._write(folder,name+'（工具生成）.md','\n'.join(lines)))
            for mid in {m['material_id'] for m in marks}:
                lines=['# 我的词句标记','']
                for mark in marks:
                    if mark['material_id']==mid:lines+=['## '+mark['text'].replace('\n',' '),'']+entry(mark)
                self._write(self.library.folder(mid),'我的标记（工具生成）.md','\n'.join(lines))
            self._write(folder,'README.md','# 我的标记\n\n- [[重点词汇（工具生成）]]：跨素材词汇与短语，保留每个来源语境。\n- [[标记句段（工具生成）]]：框选的整句或段落。\n\n数据库保存结构化记录；这些文件是 Obsidian 可读视图。手写笔记不覆盖。\n')
            return paths
