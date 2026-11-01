from paths import TOOLS_ROOT, DATA_ROOT
"""Persistent metadata and learning records. Signed streams and cookies stay in memory."""
import hashlib
import json
import re
import sqlite3
import threading
import urllib.parse
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from media_core import ROOT, safe_name

from paths import VAULT_ROOT
DEFAULT_VAULT = VAULT_ROOT

def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')

def identity(source):
    address = source.get('canonical_url') or source.get('source_url', '')
    parsed = urllib.parse.urlsplit(address)
    query = urllib.parse.parse_qs(parsed.query)
    ident = str(source.get('video_id') or '')
    platform = source.get('platform', '视频')
    if platform=='小红书':address=source.get('source_url') or address
    if 'bilibili.com' in (parsed.hostname or ''):
        match = re.search(r'BV[0-9A-Za-z]+', parsed.path)
        if match:
            ident = match.group()
            part = query.get('p', ['1'])[0]
            if part != '1': ident += '-p' + part
            platform = 'B站'
            address = 'https://www.bilibili.com/video/' + match.group() + '/' + ('?p=' + part if part != '1' else '')
    elif (parsed.hostname or '').endswith(('youtube.com', 'youtu.be')):
        ident = ident or (query.get('v') or [parsed.path.rstrip('/').split('/')[-1]])[0]
        platform = 'YouTube'
        address = 'https://www.youtube.com/watch?v=' + ident
    elif ident and platform == '抖音':
        address = 'https://www.douyin.com/video/' + ident
    if not ident:
        ident = hashlib.sha256(address.encode()).hexdigest()[:20]
    key = platform.lower() + ':' + ident
    return key, platform, ident, address

class Library:
    def __init__(self, path=None, vault=None):
        self.path = Path(path or DATA_ROOT / 'materials.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.vault = Path(vault or DEFAULT_VAULT)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS materials(
                  id TEXT PRIMARY KEY, platform TEXT NOT NULL, video_id TEXT NOT NULL,
                  title TEXT NOT NULL, source_url TEXT NOT NULL, duration REAL,
                  author TEXT, english INTEGER NOT NULL DEFAULT 0,
                  added TEXT NOT NULL, visited TEXT NOT NULL, cover_path TEXT);
                CREATE TABLE IF NOT EXISTS assets(
                  material_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
                  added TEXT NOT NULL, UNIQUE(material_id,kind,path));
                CREATE TABLE IF NOT EXISTS learning(
                  material_id TEXT PRIMARY KEY, document TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cache(
                  key TEXT PRIMARY KEY, value TEXT NOT NULL, added TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def remember(self, source):
        key, platform, ident, address = identity(source)
        material_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        stamp = now()
        with self.lock, self.connect() as db:
            db.execute('''INSERT INTO materials(id,platform,video_id,title,source_url,duration,author,added,visited)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,source_url=excluded.source_url,visited=excluded.visited,
              duration=coalesce(excluded.duration,materials.duration),author=coalesce(excluded.author,materials.author)''',
              (material_id, platform, ident, source.get('title') or platform, address,
               source.get('duration'), source.get('author'), stamp, stamp))
        source['_material_id'] = material_id
        return self.get(material_id)

    def get(self, material_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM materials WHERE id=?', (material_id,)).fetchone()
            if not row: raise ValueError('素材记录不存在，请重新解析。')
            result = dict(row)
            result['english'] = bool(result['english'])
            result['assets'] = [dict(a) for a in db.execute('SELECT kind,path,added FROM assets WHERE material_id=? ORDER BY added', (material_id,))]
            return result

    def list(self, search='', english_only=False, limit=300):
        with self.connect() as db:
            rows = db.execute('''SELECT m.*, EXISTS(SELECT 1 FROM learning l WHERE l.material_id=m.id) AS has_reading
              FROM materials m WHERE (title LIKE ? OR source_url LIKE ?)
              AND (?=0 OR english=1) ORDER BY visited DESC LIMIT ?''',
              ('%' + search + '%', '%' + search + '%', int(english_only), limit)).fetchall()
        return [dict(row) for row in rows]

    def classify(self, material_id, english):
        if not isinstance(english, bool): raise ValueError('分类值无效。')
        with self.lock, self.connect() as db:
            db.execute('UPDATE materials SET english=? WHERE id=?', (int(english), material_id))
        if english:
            self.archive_metadata(material_id)
        return self.get(material_id)

    def add_asset(self, material_id, kind, path):
        path = str(Path(path).resolve())
        with self.lock, self.connect() as db:
            db.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?,?)', (material_id, kind, path, now()))

    def set_cover(self, material_id, png):
        target = self.path.parent / 'covers' / (material_id + '.png')
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(png)
        with self.lock, self.connect() as db:
            db.execute('UPDATE materials SET cover_path=? WHERE id=?', (str(target), material_id))

    def folder(self, material_id):
        record = self.get(material_id)
        # Existing pilot directories retain their original names.
        base = self.vault / '04_视频精读'
        matches = sorted(base.glob(safe_name(record['platform'] + '_' + record['video_id']) + '_*')) if base.exists() else []
        if matches: return matches[0]
        return base / safe_name(record['platform'] + '_' + record['video_id'] + '_' + record['title'][:45])

    def archive_metadata(self, material_id):
        record = self.get(material_id)
        folder = self.folder(material_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / '素材信息（工具生成）.md'
        body = (f'# {record["title"]}\n\n- 来源：{record["source_url"]}\n'
                f'- 平台编号：{record["video_id"]}\n- 收录时间：{record["added"]}\n'
                '- 学习目标：雅思总分 8.5\n- 收录不代表已学习或已掌握。\n\n'
                '阅读正文与字幕另存；你自行撰写的学习笔记保持原样。\n')
        if not target.exists(): target.write_text(body, encoding='utf-8')
        self.add_asset(material_id, 'note', target)
        return folder

    def document(self, material_id):
        with self.connect() as db:
            row = db.execute('SELECT document FROM learning WHERE material_id=?', (material_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_document(self, material_id, document):
        document = {**document, 'updated': now()}
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO learning VALUES(?,?,?) ON CONFLICT(material_id) DO UPDATE SET document=excluded.document,updated=excluded.updated',
                       (material_id, json.dumps(document, ensure_ascii=False), document['updated']))

    def cache_get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key, value):
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (key, json.dumps(value, ensure_ascii=False), now()))

    def archive_document(self, material_id, document):
        if not self.get(material_id)['english']: return None
        folder = self.archive_metadata(material_id)
        lines = ['# ' + self.get(material_id)['title'], '', '- 状态：' + document.get('status_label', '待校对'),
                 '- 字幕来源：' + '；'.join(document.get('origins', [])), '', '']
        for row in document.get('rows', []):
            seconds = row.get('start')
            stamp = f'{int(seconds)//60:02}:{int(seconds)%60:02}' if seconds is not None else '无时间轴'
            lines.extend([f'## {stamp} · {row["id"]}', '', row.get('en', ''), '', row.get('zh') or '（中文待补充）', ''])
            if row.get('zh_origin'): lines.extend(['> 中文来源：' + row['zh_origin'], ''])
            if row.get('analysis'): lines.extend(['### 句子解析', '', row['analysis'], ''])
        if document.get('unmatched_chinese'):
            lines+=['## 尚未对齐的原中文字幕','','以下原译文单独保留，不按行号强行对应英文。','']
            for cue in document['unmatched_chinese']:lines+=[cue['text'],'']
        body = '\n'.join(lines)
        target=self.write_generated(material_id,'阅读正文（工具生成）.md',body,'reading')
        self.archive_vocabulary(material_id,document)
        return target

    def write_generated(self,material_id,name,body,kind):
        folder=self.folder(material_id);folder.mkdir(parents=True,exist_ok=True)
        target = folder / name
        digest = hashlib.sha256(body.encode()).hexdigest()
        key='export-hash:'+material_id+':'+name
        last_hash = self.cache_get(key)
        previous_path=self.cache_get('export-path:'+key)
        # Text-mode writes on Windows used CRLF; compare normalized text so our
        # own incremental exports are not mistaken for user edits on every sentence.
        def text_hash(path): return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
        if previous_path:
            previous=Path(previous_path)
            if previous.parent==folder and previous.is_file() and text_hash(previous)==last_hash:
                target=previous
        if digest==last_hash and target.is_file() and text_hash(target)==digest:
            return target
        if target.exists() and text_hash(target) not in (digest, last_hash):
            # The previous export was edited in Obsidian. Never replace it.
            target = folder / (Path(name).stem+'（新生成_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '）.md')
        target.write_text(body, encoding='utf-8', newline='\n')
        self.cache_put(key, digest)
        self.cache_put('export-path:'+key,str(target))
        self.add_asset(material_id, kind, target)
        return target

    def archive_vocabulary(self,material_id,document):
        from learning_text import annotations,wordlists
        phrases={};words={}
        for row in document.get('rows',[]):
            for mark in annotations(row['en']):
                if mark['kind']=='phrase':phrases.setdefault(mark['word'].lower(),mark['meaning'])
                else:words.setdefault(mark['lemma'],mark)
        lines=['# 词汇与短语参考','','标记供本素材学习使用，不代表已掌握；词表未命中也不代表不属于考试范围。','',
               '## 可迁移短语与语境词','']
        for phrase,meaning in sorted(phrases.items()):lines.append('- **'+phrase+'**：'+meaning)
        lines+=['','## 参考词汇','','| 原形／词条 | 词表标签 | 词表释义（结合本句理解） |','|---|---|---|']
        labels={'ielts':'雅思参考','cet6':'六级参考','tem8':'专八参考'}
        for lemma,mark in sorted(words.items()):
            lines.append('| '+lemma.replace('|','\\|')+' | '+'／'.join(labels[tag] for tag in mark['tags'])+' | '+mark['meaning'].replace('|','\\|').replace('\n','；')+' |')
        lines+=['','## 来源与版本','']
        for source in wordlists()['sources']:
            lines.append('- '+source['book']+' · '+source['revision']+' · '+source['source'])
        self.write_generated(material_id,'词汇与短语（工具生成）.md','\n'.join(lines)+'\n','vocabulary')

