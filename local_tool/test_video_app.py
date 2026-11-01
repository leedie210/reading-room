import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from media_ranges import parse_time,clip_range,subtitle_roi
from transcription import simplified,cross_check
from app import App,tk
from media_library import Library

class Checks(unittest.TestCase):
    def test_times_and_regions(self):
        self.assertEqual(parse_time('01:02.125'),62.125)
        self.assertEqual(parse_time('1:02:03.5'),3723.5)
        for value in ('-1','nan','1:60','0:00:61',False,float('inf')):
            with self.assertRaises(ValueError):parse_time(value)
        with self.assertRaises(ValueError):clip_range(2,2)
        with self.assertRaises(ValueError):clip_range(0,9,8)
        with self.assertRaises(ValueError):subtitle_roi([0,0,float('nan'),1])

    def test_simplified_and_evidence_based_corrections(self):
        self.assertEqual(simplified('這是繁體字幕與軟體。'),'这是繁体字幕与软体。')
        speech='把这种现象叫作金属皮肤，它的内部精力会产生裂纹。最后没有显示的口述也要保留。'
        screen='把这种现象叫作金属疲劳，它的内部晶粒会产生裂纹。'
        result,count=cross_check(speech,screen)
        self.assertIn('金属疲劳',result);self.assertIn('晶粒',result);self.assertIn('没有显示的口述',result)
        self.assertGreaterEqual(count,2)
        result,_=cross_check('这是原来的文案。','背景广告这是原来的文案。')
        self.assertNotIn('背景广告',result)
        result,_=cross_check('这是无用之用的设立术。是的，他不会发热。','这是无用之用的社栎树。它不会发热。')
        self.assertIn('社栎树。是的，它',result)

    def test_desktop_has_video_actions_and_no_cloud_login(self):
        folder=tempfile.TemporaryDirectory();root=tk.Tk();root.withdraw();app=App(root,library=Library(Path(folder.name)/'test.sqlite3',Path(folder.name)/'vault'))
        def finish():
            deadline=time.monotonic()+4
            while app.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
            root.update();self.assertFalse(app.busy)
        try:
            labels=[]
            def walk(widget):
                for child in widget.winfo_children():
                    try:labels.append(str(child.cget('text')))
                    except tk.TclError:pass
                    walk(child)
            walk(root);self.assertFalse(any('登录百度' in s or '登录夸克' in s or '网盘提取码' in s for s in labels))
            with patch('app.PublicMedia') as parser:
                app.input.insert('1.0','https://pan.quark.cn/s/abc');app.parse();parser.assert_not_called()
            app.current={'title':'样例','source_url':'https://example.org/video','duration':10,'preview':{'video':{'url':'https://example.org/media.mp4'}}}
            app.rows=[{'name':'视频','kind':'ytdlp','url':'https://example.org/video','preview':app.current['preview']}]
            app.tree.insert('','end',iid='0',values=('视频','视频',''));app.tree.selection_set('0')
            app.clip_start.set('1.125');app.clip_end.set('4.5')
            with patch('app.export_clip',return_value=Path('clip.mp4')) as export:
                app.save('clip');finish();self.assertEqual(export.call_args.args[1]['_clip_range'],{'start':1.125,'end':4.5})
        finally:
            for task in root.tk.call('after','info'):root.after_cancel(task)
            root.destroy()
            folder.cleanup()

if __name__=='__main__':unittest.main(verbosity=2)
