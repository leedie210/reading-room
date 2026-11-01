"""Create synthetic text-only material; no external video or personal data."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'local_tool'))
from media_library import Library
library=Library()
for number,en,zh in [(1,'We learn by doing, reflecting, and trying again.','我们通过实践、反思和再次尝试来学习。'),(2,'Curiosity gives us a reason to keep trying.','好奇心给了我们继续尝试的理由。')]:
 source={'platform':'合成演示','video_id':'demo-'+str(number),'title':'阅读书房 · 合成演示 '+str(number),'source_url':'https://example.org/reading-room-demo-'+str(number)}
 mid=library.remember(source)['id']
 if library.document(mid):continue
 library.classify(mid,True)
 library.save_document(mid,{'status':'ready','status_label':'自编文字演示，不含可播放视频','extraction_complete':True,'origins':['自编合成示例'],'rows':[{'id':'demo-row','en':en,'zh':zh,'start':None,'en_origin':'合成示例','zh_origin':'自编对照'}]})
print('Created synthetic reading materials. Launch run.py and open 英语书架.')
