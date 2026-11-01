from paths import TOOLS_ROOT, DATA_ROOT
"""Timed caption parsing, conservative bilingual alignment and reference labels."""
import difflib
import functools
import hashlib
import html
import json
import math
import re
import xml.etree.ElementTree as ET
from media_core import ROOT

PHRASES = {
    'in light of': '鉴于；考虑到', 'take on board': '接受、认真考虑（意见或价值）',
    'be responsive to': '对……有反应／能随之调整', 'responsive to': '对……有反应／能随之调整',
    'competing theories': '对同一现象提出不同解释的理论', 'the stakes': '利害关系；这件事的重要意义',
    'at stake': '处于成败攸关的位置；受到影响', 'make sense of': '理解；弄清',
    'act against': '违背……而行动', 'the good': '善（哲学语境词，需结合原文）',
    'in the context of': '在……的语境或背景下', 'on the face of it': '乍看之下；表面上',
    'to the extent that': '在……的程度上；就……而言', 'for the sake of': '为了……',
    'take a long view': '从长远角度考虑', 'get off scot-free': '逃脱惩罚；未受责罚',
    'corrective rather than retributive': '以纠正为目的，而非以报应为目的',
    'in virtue of': '由于；凭借', 'give rise to': '引起；导致',
    'at odds with': '与……不一致或有冲突', 'take into account': '把……考虑在内',
    'on the grounds that': '以……为理由', 'fall short of': '未达到；不足',
    'come to terms with': '逐渐接受或学会面对', 'a case in point': '一个恰当的例子',
    'as opposed to': '与……相对；而非', 'draw a distinction': '作出区分',
    'in pursuit of': '追求……', 'go astray': '偏离方向；误入歧途',
    'take for granted': '想当然地认为；视为理所当然', 'account for': '解释；占……比例（依语境）',
}

def clean(text):
    return html.unescape(re.sub(r'<[^>]*>', '', str(text))).strip()

def _seconds(value):
    if value is None: return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError): return None

def _clock(value):
    parts = value.replace(',', '.').split(':')
    return sum(float(v) * 60 ** i for i, v in enumerate(reversed(parts)))

def parse_cues(raw, extension):
    if isinstance(raw, bytes): raw = raw.decode('utf-8-sig')
    extension = extension.lower()
    output = []
    def add(start, end, text):
        text = clean(text)
        start, end = _seconds(start), _seconds(end)
        if text: output.append({'start': start, 'end': end if end is not None else start, 'text': text})
    if extension in ('json', 'json3'):
        obj = json.loads(raw)
        if isinstance(obj, dict) and 'body' in obj:
            for cue in obj['body']: add(cue.get('from'), cue.get('to'), cue.get('content', ''))
        elif isinstance(obj, dict):
            for cue in obj.get('events', []):
                start = cue.get('tStartMs')
                add(start / 1000 if start is not None else None,
                    (start + cue.get('dDurationMs', 0)) / 1000 if start is not None else None,
                    ''.join(segment.get('utf8', '') for segment in cue.get('segs', [])))
    elif extension in ('ttml', 'srv1', 'srv2', 'srv3', 'xml'):
        for node in ET.fromstring(raw).iter():
            if node.tag.rsplit('}', 1)[-1] not in ('text', 'p'): continue
            begin = node.get('begin')
            start = _clock(begin.rstrip('s')) if begin and ':' in begin else _seconds((begin or node.get('start', '')).rstrip('s'))
            end = node.get('end')
            end = _clock(end.rstrip('s')) if end and ':' in end else _seconds((end or '').rstrip('s'))
            duration = _seconds(node.get('dur'))
            if end is None and start is not None and duration is not None: end = start + duration
            add(start, end, ''.join(node.itertext()))
    else:
        for block in re.split(r'\n\s*\n', raw.replace('\r', '')):
            lines = block.strip().splitlines()
            timing = next((i for i, line in enumerate(lines) if '-->' in line), None)
            if timing is None: continue
            match = re.match(r'\s*([\d:.,]+)\s*-->\s*([\d:.,]+)', lines[timing])
            if match: add(_clock(match[1]), _clock(match[2]), ' '.join(lines[timing + 1:]))
    return collapse(output)

def collapse(cues):
    result = []
    for original in cues:
        cue = {**original, 'text': re.sub(r'\s+', ' ', original['text']).strip()}
        if not cue['text']: continue
        if result and cue.get('start') is not None and result[-1].get('end') is not None and cue['start'] <= result[-1]['end'] + .55:
            previous = result[-1]
            similarity = difflib.SequenceMatcher(None, previous['text'], cue['text']).ratio()
            if previous['text'] == cue['text'] or similarity > .88 and (previous['text'] in cue['text'] or cue['text'] in previous['text']):
                if len(cue['text']) > len(previous['text']): previous['text'] = cue['text']
                previous['end'] = max(previous['end'], cue.get('end') or cue['start'])
                continue
        result.append(cue)
    return result

def glossary_line(text):
    return bool(re.search(r'(?:^|\s)\d{1,3}[.、)]?\s*[A-Za-z][A-Za-z -]*\s+(?:n|v|adj|adv|vt|vi|prep|pron|conj)\s*[.．:：]', text, re.I)
                or re.search(r'\b[A-Za-z-]+\s+(?:n|v|adj|adv|vt|vi|prep|pron|conj)\s*[.．:：]?\s*[\u4e00-\u9fff]',text,re.I))

def same_screen_caption(left,right):
    def parts(text):
        lines=[line.strip() for line in text.splitlines() if not glossary_line(line)]
        return [' '.join(line for line in lines if bool(re.search(r'[\u4e00-\u9fff]',line))==chinese) for chinese in (False,True)]
    for a,b in zip(parts(left),parts(right)):
        if a==b:continue
        if not a or not b:return False
        if min(len(a),len(b))>=20 and (a.startswith(b) or b.startswith(a)):continue
        if min(len(a),len(b))>=25 and difflib.SequenceMatcher(None,a,b).ratio()>=.97:continue
        return False
    return True

def cue_metrics(cues, duration):
    words=sum(len(re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?",c['text'])) for c in cues)
    intervals=sorted((max(0,c['start']),min(duration,c['end'])) for c in cues if c.get('start') is not None and c.get('end') is not None)
    covered=0;last=0
    for start,end in intervals:
        covered+=max(0,end-max(start,last));last=max(last,end)
    return {'words':words,'words_per_minute':words*60/duration if duration else 0,'observed_ratio':covered/duration if duration else 0,'cues':len(cues)}

def complete_english(cues, duration):
    if not cues:return False
    if not duration or duration<45:return True
    metrics=cue_metrics(cues,duration)
    # Screen time is not speech coverage: a static three-word title is not 30 s of transcript.
    return metrics['words_per_minute']>=60 and metrics['observed_ratio']>=.65 and len(cues)>=duration/18

def reusable_document(document, duration, method='auto'):
    if not document or not document.get('extraction_complete') or not document.get('rows'):return False
    if method=='speech' and document.get('extraction_method')!='speech':return False
    if document.get('extraction_version')==2:return bool(document.get('quality',{}).get('complete'))
    rows=document['rows']
    if any(glossary_line(r.get('zh','')) for r in rows):return False
    cues=[{'start':r.get('start'),'end':r.get('end'),'text':r.get('en','')} for r in rows]
    return complete_english(cues,duration) and not any((r.get('end') or 0)-(r.get('start') or 0)>45 for r in rows)

def translation_batches(rows):
    offset=0
    while offset<len(rows):
        batch=[];characters=0
        for row in rows[offset:offset+8]:
            if batch and characters+len(row['en'])>900:break
            batch.append(row);characters+=len(row['en'])
        yield offset,batch
        offset+=len(batch)

def ocr_languages(events):
    english, chinese = [], []
    for event in events:
        en, zh = [], []
        lines=event['text'].splitlines()
        vocabulary_panel=any(glossary_line(line) for line in lines)
        for line in lines:
            if glossary_line(line):continue
            if re.search(r'[\u4e00-\u9fff]', line): zh.append(line)
            elif re.search(r'[A-Za-z]', line): en.append(line)
        # A lexical panel is not evidence of translated subtitles (even its channel label).
        if vocabulary_panel:zh=[]
        for lines, output in ((en, english), (zh, chinese)):
            if lines: output.append({'start': event['start'], 'end': event['end'] + .4, 'text': ' '.join(lines)})
    return collapse(english), collapse(chinese)

def make_rows(english, chinese, english_origin, chinese_origin):
    merged, pending = [], None
    for cue in collapse(english):
        adjacent=pending and cue.get('start') is not None and pending.get('end') is not None and cue['start']-pending['end']<1.2
        bounded=pending and len(pending['text'])+len(cue['text'])<360 and (cue.get('end') is None or pending.get('start') is None or cue['end']-pending['start']<=18)
        if pending and adjacent and bounded:
            pending['text'] += ' ' + cue['text']; pending['end'] = cue.get('end')
        else:
            if pending: merged.append(pending)
            pending = dict(cue)
        if re.search(r'[.!?]["\u201d\u2019\)]?$', pending['text']) or len(pending['text']) > 1000:
            merged.append(pending); pending = None
    if pending: merged.append(pending)
    rows, matched = [], set()
    for cue in merged:
        selected = []
        if cue.get('start') is not None and cue.get('end') is not None:
            for index, zh in enumerate(chinese):
                if zh.get('start') is None or zh.get('end') is None: continue
                overlap = min(cue['end'], zh['end']) - max(cue['start'], zh['start'])
                if overlap > .12:
                    selected.append(index); matched.add(index)
        texts = []
        for index in selected:
            if not texts or texts[-1] != chinese[index]['text']: texts.append(chinese[index]['text'])
        en = re.sub(r'\s+', ' ', cue['text']).strip()
        stable = f'{cue.get("start")}:{cue.get("end")}:{en}'
        row = {'id': hashlib.sha256(stable.encode()).hexdigest()[:14], 'start': cue.get('start'), 'end': cue.get('end'),
               'en': en, 'zh': ' '.join(texts), 'en_origin': english_origin,
               'zh_origin': chinese_origin if texts else '', 'alignment': 'time-overlap' if texts else 'missing',
               'zh_cue_ids': selected}
        # One Chinese interval can correspond to multiple English sentences.
        # Keep this as one bilingual reading paragraph rather than repeating it.
        if rows and selected and rows[-1]['zh_cue_ids'] == selected and len(rows[-1]['en'])+len(en)<360 and (row['end'] or 0)-(rows[-1]['start'] or 0)<=18:
            previous = rows[-1]; previous['en'] += ' ' + en; previous['end'] = row['end']
            previous['alignment'] = 'paragraph'
        else: rows.append(row)
    unmatched = [cue for i, cue in enumerate(chinese) if i not in matched]
    return rows, unmatched

@functools.lru_cache(maxsize=1)
def wordlists():
    path = TOOLS_ROOT / 'wordlists/reference.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'words': {}, 'sources': []}

def annotations(text):
    output = []
    for phrase, meaning in PHRASES.items():
        for match in re.finditer(r'(?<![A-Za-z])' + re.escape(phrase) + r'(?![A-Za-z])', text, flags=re.I):
            output.append({'start': match.start(), 'end': match.end(), 'kind': 'phrase', 'word': match.group(),
                           'meaning': meaning, 'source': '可迁移表达参考表 v1；结合语境理解，不对应固定雅思分数'})
    dictionary = wordlists()['words']
    irregular = {'went': 'go', 'gone': 'go', 'thought': 'think', 'taught': 'teach', 'knew': 'know',
                 'known': 'know', 'brought': 'bring', 'bought': 'buy', 'sought': 'seek', 'made': 'make', 'took': 'take', 'taken': 'take'}
    for match in re.finditer(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)?", text):
        word = match.group().lower().replace('\u2019', "'")
        candidates = [word]
        if word in irregular: candidates.append(irregular[word])
        if word.endswith('ies'): candidates.append(word[:-3] + 'y')
        if word.endswith('ing'): candidates += [word[:-3], word[:-3] + 'e', word[:-4] if len(word) > 4 and word[-4] == word[-5] else word]
        if word.endswith('ed'): candidates += [word[:-2], word[:-1], word[:-3] if len(word) > 3 and word[-3] == word[-4] else word]
        if word.endswith('s') and not word.endswith('ss'): candidates += [word[:-1], word[:-2] if word.endswith('es') else word]
        lemma = next((candidate for candidate in candidates if candidate in dictionary), None)
        if lemma:
            entry = dictionary[lemma]
            output.append({'start': match.start(), 'end': match.end(), 'kind': 'word', 'word': match.group(),
                'lemma': lemma, 'inferred_form': lemma != word, 'tags': entry['tags'], 'meaning': '；'.join(entry['meanings']),
                'source': 'kajweb/dict 参考词表；词表释义需结合本句，非官方考试命中保证'})
    return output
