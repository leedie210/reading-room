import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from media_library import Library
from reading_marks import ReadingMarks
from learning_jobs import LearningService
from preview_server import PreviewServer

class ReadingRoomChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.library=Library(self.root/'materials.sqlite3',self.root/'vault');self.sources=[]
        for name in ('one','two','other'):
            source={'title':name,'platform':'test','source_url':'https://example.org/'+name}
            mid=self.library.remember(source)['id'];self.sources.append(source)
            self.library.classify(mid,name!='other')
            self.library.save_document(mid,{'rows':[{'id':'r1','en':'We learn 😀 together.','zh':'一起学习。','start':12}]})
        self.mid=self.sources[0]['_material_id'];self.marks=ReadingMarks(self.library)
    def tearDown(self):self.temp.cleanup()
    def selection(self,**extra):return {'row':'r1','start':3,'end':8,'text':'learn','kind':'word',**extra}
    def test_dedup_restart_and_cross_material_contexts(self):
        first=self.marks.add(self.mid,self.selection());again=self.marks.add(self.mid,self.selection())
        self.assertEqual(first['id'],again['id']);self.marks.add(self.sources[1]['_material_id'],self.selection())
        reopened=ReadingMarks(Library(self.library.path,self.library.vault))
        self.assertEqual(len(reopened.list()),2)
        self.assertEqual({m['material_id'] for m in reopened.list()},{s['_material_id'] for s in self.sources[:2]})
    def test_unicode_offsets_and_overlapping_sentence(self):
        self.marks.add(self.mid,self.selection(start=9,end=10,text='😀'))
        self.marks.add(self.mid,self.selection(start=0,end=20,text='We learn 😀 together.',kind='sentence'))
        self.assertEqual(len(self.marks.list(self.mid)),2)
    def test_changed_original_retains_old_context_without_reattaching(self):
        self.marks.add(self.mid,self.selection())
        self.library.save_document(self.mid,{'rows':[{'id':'r1','en':'We never learn together.','start':10}]})
        saved=self.marks.list()[0];self.assertFalse(saved['valid']);self.assertEqual(saved['context'],'We learn 😀 together.')
        with self.assertRaises(ValueError):self.marks.add(self.mid,self.selection())
    def test_validation_and_non_english_material(self):
        for data in [self.selection(start=True),self.selection(start=-1),self.selection(text='other'),self.selection(kind='unknown')]:
            with self.assertRaises(ValueError):self.marks.add(self.mid,data)
        with self.assertRaises(ValueError):self.marks.add(self.sources[2]['_material_id'],self.selection())
    def test_export_failure_preserves_database_and_reports_failure(self):
        with patch.object(self.marks,'export',side_effect=OSError('disk unavailable')):
            result=self.marks.add(self.mid,self.selection())
        self.assertFalse(result['exported']);self.assertEqual(len(self.marks.list()),1)
    def test_markdown_export_keeps_manual_edits_and_stable_blocks(self):
        self.marks.add(self.mid,self.selection());folder=self.library.vault/'04_视频精读/我的标记'
        path=folder/'重点词汇（工具生成）.md';text=path.read_text(encoding='utf-8')
        self.assertIn('^mark-',text);self.assertIn('#001',text)
        path.write_text('My personal notes',encoding='utf-8')
        self.marks.add(self.sources[1]['_material_id'],self.selection())
        self.marks.add(self.mid,self.selection(start=0,end=2,text='We'))
        self.assertEqual(path.read_text(encoding='utf-8'),'My personal notes')
        self.assertEqual(len(list(folder.glob('重点词汇*新生成*.md'))),1)
    def test_http_shelf_origin_marks_and_cross_book_reading(self):
        learning=LearningService(self.library);restores=[]
        def restore(mid):
            restores.append(mid);source={**self.sources[1],'preview':{'video':{'url':'https://example.org/video.mp4'}}}
            return source,{'kind':'ytdlp','preview':source['preview']}
        server=PreviewServer(library=self.library,learning=learning,on_restore=restore)
        base=server.register(self.sources[0],{'kind':'ytdlp'})
        def read(route):
            with urllib.request.urlopen(base+route) as response:return json.load(response)
        try:
            shelf=read('shelf-data');self.assertEqual(len(shelf),2)
            request=urllib.request.Request(base+'save-mark',data=json.dumps(self.selection()).encode())
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
            self.assertEqual(error.exception.code,403)
            request.add_header('Origin',server.origin)
            with urllib.request.urlopen(request) as response:self.assertTrue(json.load(response)['exported'])
            self.assertEqual(len(read('reading-data')['marks']),1)
            mid=self.sources[1]['_material_id'];url=read('library-open?material='+mid)['url']
            with urllib.request.urlopen(url) as response:self.assertIn('THE READING ROOM',response.read().decode())
            self.assertEqual(restores,[],'Reading saved text should not refresh the video stream')
            media_base=url.removesuffix('reading')
            for _ in range(2):
                with urllib.request.urlopen(media_base+'info') as response:self.assertTrue(json.load(response)['video'])
            self.assertEqual(restores,[mid])
            with urllib.request.urlopen(base+'marks-export?kind=word') as response:self.assertIn('^mark-',response.read().decode())
            with urllib.request.urlopen(base+'reading-room.css') as response:self.assertIn('--paper',response.read().decode())
        finally:server.close();learning.close()

    def test_shelf_local_video_remains_exportable_without_network_restore(self):
        video=self.root/'saved.mp4';video.write_bytes(b'0123456789abcdef')
        self.library.add_asset(self.mid,'video',video)
        calls=[]
        server=PreviewServer(library=self.library,on_restore=lambda mid:calls.append(mid))
        try:
            address=server.material_url(self.mid).removesuffix('reading')
            token=address.rstrip('/').split('/')[-1]
            self.assertTrue(server.sessions[token]['item']['resolved_streams'])
            with urllib.request.urlopen(address+'info') as response:self.assertTrue(json.load(response)['video'])
            with urllib.request.urlopen(urllib.request.Request(address+'video',headers={'Range':'bytes=3-6'})) as response:
                self.assertEqual(response.read(),b'3456')
            self.assertEqual(calls,[])
        finally:server.close()

if __name__=='__main__':unittest.main(verbosity=2)
