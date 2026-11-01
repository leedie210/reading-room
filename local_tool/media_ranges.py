"""Shared validation for user-selected video time ranges and subtitle regions."""
import math
import re

def parse_time(value):
    if isinstance(value,bool):raise ValueError('时间格式无效。')
    if isinstance(value,(int,float)):seconds=float(value)
    else:
        text=str(value).strip()
        if not re.fullmatch(r'\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?',text):
            raise ValueError('时间请填写秒数，或 分:秒 / 时:分:秒，可精确到毫秒。')
        parts=[float(p) for p in text.split(':')]
        if len(parts)>1 and any(v>=60 for v in parts[1:]):raise ValueError('分、秒应小于 60。')
        seconds=sum(v*60**i for i,v in enumerate(reversed(parts)))
    if not math.isfinite(seconds) or seconds<0:raise ValueError('时间必须是大于等于零的有限数值。')
    return seconds

def clip_range(start,end,duration=None):
    start,end=parse_time(start),parse_time(end)
    if end<=start:raise ValueError('结束时间必须晚于开始时间。')
    if duration is not None:
        try:duration=float(duration)
        except (ValueError,TypeError):duration=0
        if math.isfinite(duration) and duration>0 and end>duration+.1:raise ValueError('截取范围超过了视频时长。')
    return {'start':start,'end':end}

def subtitle_roi(value):
    if value is None:return None
    if not isinstance(value,(list,tuple)) or len(value)!=4:raise ValueError('字幕区域格式无效。')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in value):raise ValueError('字幕区域坐标无效。')
    left,top,right,bottom=map(float,value)
    if not (0<=left<right<=1 and 0<=top<bottom<=1) or right-left<.03 or bottom-top<.015:
        raise ValueError('请框选有效的字幕区域。')
    return [left,top,right,bottom]
