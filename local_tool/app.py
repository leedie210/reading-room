from paths import DOWNLOADS_ROOT
"""Local video preview, downloads, clips, audio and simplified transcripts."""
import http.cookiejar
import base64
import os
import queue
import threading
import tkinter as tk
import urllib.parse
import webbrowser
from pathlib import Path
from tkinter import ttk,filedialog
from media_core import ROOT,PublicMedia,extract_url,download
from preview_server import PreviewServer,cover_png
from media_exports import export_audio,export_transcript,export_video,export_clip
from media_ranges import clip_range
from media_library import Library
from learning_jobs import LearningService
from learning_model import load_config,save_config

METHODS={'自动（优先字幕原文）':'auto','高精度语音识别':'speech','读取画面字幕':'screen'}
CLOUD_HOSTS={'pan.baidu.com','pan.quark.cn','drive.quark.cn','drive.uc.cn','www.alipan.com','www.aliyundrive.com',
    'cloud.189.cn','pan.xunlei.com','www.123pan.com','www.123865.com','115.com','www.115.com','pan.115.com'}

class App:
    def __init__(self,root,auto_restore=None,library=None):
        self.root=root;root.title('本地视频工具 · 素材库与英语学习');root.geometry('1140x'+str(min(850,max(680,root.winfo_screenheight()-100))));root.minsize(1000,650)
        self.library=library or Library()
        self.learning=LearningService(self.library)
        self.events=queue.Queue();self.busy=False;self.rows=[];self.current=None;self.cookie_file=None
        self.preview_server=None;self.cover_generation=0;self.cover_photo=None
        self.cookie_path=tk.StringVar(value='未导入视频平台登录文件')
        self.destination=tk.StringVar(value=str(DOWNLOADS_ROOT))
        self.status=tk.StringVar(value='粘贴视频链接或分享文案，点击“解析视频”。')
        self.title=tk.StringVar(value='尚未解析');self.method=tk.StringVar(value=next(iter(METHODS)))
        self.clip_start=tk.StringVar(value='0');self.clip_end=tk.StringVar()
        self.english=tk.BooleanVar(value=False)
        style=ttk.Style();style.theme_use('clam');style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',19,'bold'))
        outer=ttk.Frame(root,padding=16);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='本地视频工具',style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer,text='先预览，再保存视频、片段、音频或文案',foreground='#546477').pack(anchor='w',pady=(5,14))
        self.input=tk.Text(outer,height=2,font=('Microsoft YaHei UI',11),wrap='word',relief='solid',borderwidth=1)
        self.input.pack(fill='x')
        action=ttk.Frame(outer);action.pack(fill='x',pady=10)
        self.parse_button=ttk.Button(action,text='解析视频',command=self.parse);self.parse_button.pack(side='left')
        ttk.Button(action,text='粘贴剪贴板',command=self.paste).pack(side='left',padx=8)
        ttk.Label(action,text='文案来源：').pack(side='left',padx=(14,0))
        self.method_choice=ttk.Combobox(action,textvariable=self.method,values=tuple(METHODS),state='readonly',width=24)
        self.method_choice.pack(side='left')
        ttk.Button(action,text='素材历史',command=self.open_history).pack(side='right')
        ttk.Button(action,text='英语书架',command=self.open_bookshelf).pack(side='right',padx=8)
        dest=ttk.Frame(outer);dest.pack(fill='x',pady=(0,8))
        ttk.Label(dest,text='保存位置：').pack(side='left')
        ttk.Entry(dest,textvariable=self.destination).pack(side='left',fill='x',expand=True,padx=5)
        ttk.Button(dest,text='选择文件夹',command=self.choose_destination).pack(side='left')
        ttk.Button(dest,text='打开文件夹',command=self.open_destination).pack(side='left',padx=(5,0))
        cookies=ttk.Frame(outer);cookies.pack(fill='x',pady=(0,12))
        ttk.Button(cookies,text='导入视频平台登录文件',command=self.choose_cookies).pack(side='left')
        ttk.Label(cookies,textvariable=self.cookie_path,foreground='#637082').pack(side='left',padx=8)
        ttk.Button(cookies,text='清除',command=self.clear_cookies).pack(side='right')
        ttk.Label(outer,textvariable=self.title,wraplength=940).pack(anchor='w',pady=(0,8))
        learningbar=ttk.Frame(outer);learningbar.pack(fill='x',pady=(0,10))
        ttk.Checkbutton(learningbar,text='作为英语学习素材',variable=self.english,command=self.classify).pack(side='left')
        self.reading_button=ttk.Button(learningbar,text='双语阅读',command=self.open_reading);self.reading_button.pack(side='left',padx=12)
        ttk.Button(learningbar,text='翻译与解析设置',command=self.model_settings).pack(side='right')
        treeframe=ttk.Frame(outer);treeframe.pack(fill='both',expand=True)
        panel=ttk.Frame(treeframe,width=250);panel.pack(side='left',fill='y',padx=(0,16));panel.pack_propagate(False)
        ttk.Label(panel,text='封面预览',font=('Microsoft YaHei UI',11,'bold')).pack(anchor='w',pady=(0,8))
        self.cover_label=ttk.Label(panel,text='解析视频后显示封面',anchor='center',wraplength=230)
        self.cover_label.pack(fill='both',expand=True)
        self.preview_hint=tk.StringVar(value='预览页可选时间范围和字幕位置')
        ttk.Label(panel,textvariable=self.preview_hint,wraplength=230,foreground='#637082').pack(fill='x',pady=8)
        self.tree=ttk.Treeview(treeframe,columns=('name','type','detail'),show='headings',selectmode='browse')
        for name,label,width in [('name','资源',330),('type','类型',70),('detail','说明',220)]:
            self.tree.heading(name,text=label);self.tree.column(name,width=width,minwidth=60)
        self.tree.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(treeframe,orient='vertical',command=self.tree.yview);scroll.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Double-1>',lambda _:self.preview_selected());self.tree.bind('<<TreeviewSelect>>',self.selection_changed)
        nav=ttk.Frame(outer);nav.pack(fill='x',pady=10)
        self.preview_button=ttk.Button(nav,text='播放预览 / 选择片段',command=self.preview_selected);self.preview_button.pack(side='left')
        self.download_button=ttk.Button(nav,text='下载完整视频',command=self.save);self.download_button.pack(side='left',padx=6)
        self.audio_button=ttk.Button(nav,text='只提取音频',command=lambda:self.save('audio'));self.audio_button.pack(side='left')
        self.transcript_button=ttk.Button(nav,text='只提取字幕',command=lambda:self.save('transcript'));self.transcript_button.pack(side='left',padx=6)
        ttk.Button(nav,text='打开原视频',command=self.open_source).pack(side='right')
        clips=ttk.Frame(outer);clips.pack(fill='x',pady=(0,10))
        ttk.Label(clips,text='截取：从').pack(side='left');ttk.Entry(clips,textvariable=self.clip_start,width=15).pack(side='left',padx=5)
        ttk.Label(clips,text='到').pack(side='left');ttk.Entry(clips,textvariable=self.clip_end,width=15).pack(side='left',padx=5)
        self.clip_button=ttk.Button(clips,text='下载这一段',command=lambda:self.save('clip'));self.clip_button.pack(side='left',padx=5)
        ttk.Label(clips,text='支持秒数或 时:分:秒，也可在预览中设置',foreground='#637082').pack(side='left',padx=8)
        self.bar=ttk.Progressbar(outer,mode='determinate',maximum=100);self.bar.pack(fill='x')
        ttk.Label(outer,textvariable=self.status,wraplength=940).pack(anchor='w',pady=(6,10))
        ttk.Label(outer,text='中文统一简体。英语学习优先保留原视频译文；画面字幕可在预览页框选。',foreground='#637082',wraplength=1000).pack(anchor='w')
        ttk.Button(outer,text='打开使用说明',command=lambda:os.startfile(str(ROOT/'README.md'))).pack(anchor='w',pady=(8,0))
        root.after(100,self.poll);root.protocol('WM_DELETE_WINDOW',self.close)

    @property
    def task_buttons(self):return (self.parse_button,self.preview_button,self.download_button,self.audio_button,self.transcript_button,self.clip_button,self.reading_button)

    def close(self):
        self.learning.close()
        if self.preview_server:threading.Thread(target=self.preview_server.close,daemon=True).start()
        self.root.destroy()

    def clear_preview(self):
        self.cover_generation+=1;self.cover_photo=None;self.cover_label.configure(image='',text='解析视频后显示封面')

    def selection_changed(self,event=None):
        self.clear_preview();item=self.selected()
        if not item or not self.current:return
        self.download_button.configure(text='保存图片' if item.get('kind')=='image' else '下载完整视频')
        preview=item.get('preview') or self.current.get('preview') or {}
        resource=item if item.get('kind')=='image' else preview.get('cover')
        if not resource:self.cover_label.configure(text='暂无封面，可尝试播放预览');return
        generation=self.cover_generation;self.cover_label.configure(text='正在加载封面…')
        def work():
            try:self.events.put(('cover',(generation,cover_png(resource),None)))
            except Exception:self.events.put(('cover',(generation,None,'封面暂不可用，可尝试播放预览')))
        threading.Thread(target=work,daemon=True).start()

    def selected(self):
        selected=self.tree.selection();return self.rows[int(selected[0])] if selected else None

    def preview_selected(self):
        if self.busy or not self.current:return
        item=self.selected()
        if not item:self.status.set('请先选择视频资源。');return
        source={**self.current,'_transcript_method':METHODS[self.method.get()]}
        self.open_preview_page(source,item)

    def open_preview_page(self,source,item,reading=False):
        self.ensure_preview_server()
        webbrowser.open(self.preview_server.register(source,item)+('reading' if reading else ''))
        self.status.set('已打开预览。可设置截取起止时间，或框选画面字幕位置。')

    def ensure_preview_server(self):
        if not self.preview_server:
            def request_download(s,r):
                if self.busy:return False
                self.events.put(('preview_download',(s,r)));return True
            def restore(material_id):
                record=self.library.get(material_id)
                result=PublicMedia(self.cookie_file).inspect(record['source_url'],lambda text:self.events.put(('status',text)))
                self.library.remember(result)
                item=next((r for r in result.get('items',[]) if r.get('kind') in ('video','ytdlp')),None)
                if not item:raise ValueError('未找到视频资源，请重新解析。')
                return result,item
            self.preview_server=PreviewServer(request_download,library=self.library,learning=self.learning,on_restore=restore)

    def open_bookshelf(self):
        self.ensure_preview_server()
        webbrowser.open(self.preview_server.register({'title':'英语书架'},{})+'shelf')

    def paste(self):
        if self.busy:return
        try:self.input.delete('1.0','end');self.input.insert('1.0',self.root.clipboard_get())
        except tk.TclError:self.status.set('剪贴板里没有文字。')

    def choose_destination(self):
        path=filedialog.askdirectory(initialdir=self.destination.get())
        if path:self.destination.set(path)

    def open_destination(self):
        path=Path(self.destination.get());path.mkdir(parents=True,exist_ok=True);os.startfile(str(path))

    def choose_cookies(self):
        if self.busy:return
        path=filedialog.askopenfilename(title='选择视频平台的 Netscape cookies.txt',filetypes=[('Cookie 文件','*.txt'),('所有文件','*.*')])
        if not path:return
        try:
            jar=http.cookiejar.MozillaCookieJar(path);jar.load(ignore_discard=True)
            self.cookie_file=path;self.cookie_path.set(Path(path).name+'（仅本次会话）')
        except Exception:self.status.set('登录文件无法读取，请使用 Netscape 格式的 Cookie 文件。')

    def clear_cookies(self):
        if self.busy:return
        self.cookie_file=None;self.cookie_path.set('未导入视频平台登录文件')

    def job(self,fn,on_done):
        if self.busy:return
        self.busy=True;self.bar['value']=0
        for button in self.task_buttons:button.state(['disabled'])
        def work():
            try:self.events.put(('done',(on_done,fn())))
            except Exception as e:
                text=str(e)
                if self.cookie_file:text=text.replace(self.cookie_file,'[登录文件]')
                self.events.put(('error',text[:700]))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        try:
            while True:
                event,payload=self.events.get_nowait()
                if event=='status':self.status.set(payload)
                elif event=='cover':
                    generation,data,error=payload
                    if generation==self.cover_generation:
                        if data:
                            self.cover_photo=tk.PhotoImage(data=data);self.cover_label.configure(image=self.cover_photo,text='')
                            if self.current and self.current.get('_material_id'):self.library.set_cover(self.current['_material_id'],base64.b64decode(data))
                        else:self.cover_label.configure(image='',text=error or '暂无封面')
                elif event=='preview_download':self.save_resource(*payload)
                elif event=='progress':
                    done,total=payload;self.bar['value']=min(done/total*100,100) if total else 0
                    self.status.set(f'下载中：{done/1048576:.1f} MB'+(f' / {total/1048576:.1f} MB' if total else ''))
                elif event=='export_progress':
                    done,total=payload;self.bar['value']=min(done/total*100,100) if total else 0
                elif event in ('done','error'):
                    self.busy=False
                    for button in self.task_buttons:button.state(['!disabled'])
                    if event=='done':payload[0](payload[1])
                    else:self.status.set(payload)
        except queue.Empty:pass
        self.root.after(100,self.poll)

    def parse(self):
        if self.busy:return
        text=self.input.get('1.0','end').strip()
        try:
            url=extract_url(text)
            if urllib.parse.urlsplit(url).hostname in CLOUD_HOSTS:raise ValueError('工具已移除网盘功能，请粘贴视频平台的作品链接。')
        except ValueError as e:self.status.set(str(e));return
        self.current=None;self.rows=[];self.tree.delete(*self.tree.get_children());self.clear_preview()
        self.status.set('正在解析视频、封面与字幕信息…');cookie=self.cookie_file
        self.job(lambda:PublicMedia(cookie).inspect(text,lambda s:self.events.put(('status',s))),self.show_media)

    def show_media(self,result):
        record=self.library.remember(result)
        self.english.set(record['english'])
        self.current=result;self.rows=result['items'];self.title.set(result['title']);self.tree.delete(*self.tree.get_children())
        self.clip_start.set('0');self.clip_end.set(str(result.get('duration') or ''))
        for index,item in enumerate(self.rows):
            self.tree.insert('','end',iid=str(index),values=(item['name'],'图片' if item.get('kind')=='image' else '视频',item.get('watermark','')))
        if self.rows:self.tree.selection_set('0')
        self.status.set(f'解析完成，共 {len(self.rows)} 项。可先预览，或只保存所选片段。')

    def open_source(self):
        if self.current:webbrowser.open(self.current['source_url'])

    def save(self,mode='video'):
        if self.busy or not self.current:return
        item=self.selected()
        if not item:self.status.set('请先选择一个资源。');return
        if mode!='video' and item.get('kind') not in ('video','ytdlp'):self.status.set('请选择视频后再操作。');return
        item={**item,'_export_mode':mode,'_transcript_method':METHODS[self.method.get()]}
        if mode=='clip':
            try:item['_clip_range']=clip_range(self.clip_start.get(),self.clip_end.get(),self.current.get('duration'))
            except ValueError as e:self.status.set(str(e));return
        self.save_resource(dict(self.current),item)

    def save_resource(self,source,item):
        if self.busy:self.status.set('当前任务仍在处理，请稍后再操作。');return
        mode=item.get('_export_mode','video');destination=self.destination.get();cookies=self.cookie_file
        material_id=source.get('_material_id')
        if mode=='transcript':item={**item,'_english_learning':bool(material_id and self.library.get(material_id)['english'])}
        self.status.set('正在准备处理视频…')
        def work():
            log=lambda text:self.events.put(('status',text));progress=lambda a,b:self.events.put(('export_progress',(a,b)))
            if mode=='clip':return export_clip(source,item,destination,log,progress)
            if mode=='audio':return export_audio(source,item,destination,log,progress)
            if mode=='transcript':return export_transcript(source,item,destination,log,progress)
            if item.get('resolved_streams'):return export_video(source,item,destination,log,progress)
            return download(item,source.get('title') or '视频',destination,lambda a,b:self.events.put(('progress',(a,b))),cookies)
        def done(result):
            self.bar['value']=100
            if material_id:
                path=result.get('path') if isinstance(result,dict) else result
                self.library.add_asset(material_id,mode,path)
                if mode=='transcript' and self.library.get(material_id)['english']:
                    self.library.archive_metadata(material_id)
            if isinstance(result,dict):
                warning=f'；有 {result["uncertain_count"]} 个低置信度片段建议对照原视频' if result.get('uncertain_count') else ''
                self.status.set(result['origin']+'，字幕已保存：'+str(result['path'])+warning)
                if mode=='transcript' and material_id and self.library.get(material_id)['english'] and result.get('captions'):
                    try:
                        self.learning.start(source,item,item.get('_subtitle_roi'),captions=result['captions'])
                        self.status.set('字幕已独立保存；学习材料正在后台归档，可稍后从英语书架查看。')
                    except ValueError as error:self.status.set('字幕已保存；学习归档未启动：'+str(error))
            else:self.status.set('已保存：'+str(result))
        self.job(work,done)

    def classify(self):
        if not self.current:
            self.english.set(False);self.status.set('先解析一条视频，再选择是否作为英语学习素材。');return
        material_id=self.current.get('_material_id')
        if not material_id:return
        if not self.english.get():self.learning.cancel(material_id)
        self.library.classify(material_id,self.english.get())
        self.status.set('已归入英语学习；提取字幕会先保存文件，再后台归档到知识库，不自动打开网页。' if self.english.get() else '已取消英语分类，已有学习文件保留。')

    def open_reading(self):
        if not self.current:return
        item=self.selected() or next(iter(self.current.get('items',[])),{'kind':'ytdlp'})
        self.open_preview_page(self.current,item,reading=True)

    def open_material(self,material_id,reading=False):
        if self.busy:return
        record=self.library.get(material_id)
        local=next((asset['path'] for asset in reversed(record['assets']) if asset['kind']=='video' and Path(asset['path']).is_file()),None)
        def work():
            preview={'video':{'file_path':local,'ext':'mp4'} if local else None,'audio':None,
                     'cover':{'file_path':record['cover_path']} if record.get('cover_path') and Path(record['cover_path']).is_file() else None}
            source={'title':record['title'],'platform':record['platform'],'video_id':record['video_id'],
                    'source_url':record['source_url'],'duration':record['duration'],'preview':preview}
            item={'name':'已保存的视频' if local else '原视频','kind':'ytdlp','url':record['source_url'],'preview':preview,'resolved_streams':True}
            source['items']=[item]
            if not local and record['source_url']:
                try:source=PublicMedia(self.cookie_file).inspect(record['source_url'],lambda text:self.events.put(('status',text)))
                except Exception:
                    if not reading or not self.library.document(material_id):raise
            return source
        def done(source):
            self.show_media(source);self.open_preview_page(source,source['items'][0],reading=reading)
        self.status.set('正在从素材历史打开…');self.job(work,done)

    def open_history(self):
        window=tk.Toplevel(self.root);window.title('素材历史');window.geometry('950x550')
        search=tk.StringVar();english_only=tk.BooleanVar()
        bar=ttk.Frame(window,padding=12);bar.pack(fill='x')
        ttk.Label(bar,text='搜索标题或链接').pack(side='left');ttk.Entry(bar,textvariable=search,width=40).pack(side='left',padx=8)
        ttk.Checkbutton(bar,text='只看英语素材',variable=english_only).pack(side='left')
        tree=ttk.Treeview(window,columns=('title','platform','english','visited'),show='headings',selectmode='browse')
        for key,label,width in [('title','视频',470),('platform','平台',80),('english','分类',100),('visited','上次打开',160)]:
            tree.heading(key,text=label);tree.column(key,width=width)
        tree.pack(fill='both',expand=True,padx=12)
        def refresh(*args):
            tree.delete(*tree.get_children())
            for row in self.library.list(search.get(),english_only.get()):
                tree.insert('','end',iid=row['id'],values=(row['title'],row['platform'],'英语学习' if row['english'] else '视频素材',row['visited'][:16].replace('T',' ')))
        def selected():
            chosen=tree.selection();return chosen[0] if chosen else None
        def open_selected(reading=False):
            key=selected()
            if key:window.destroy();self.open_material(key,reading)
        def open_folder():
            key=selected()
            if not key:return
            record=self.library.get(key)
            folder=self.library.folder(key) if record['english'] else next((Path(asset['path']).parent for asset in record['assets'] if Path(asset['path']).exists()),None)
            if folder and folder.exists():os.startfile(str(folder))
        footer=ttk.Frame(window,padding=12);footer.pack(fill='x')
        ttk.Button(footer,text='打开视频',command=open_selected).pack(side='left')
        ttk.Button(footer,text='打开阅读',command=lambda:open_selected(True)).pack(side='left',padx=8)
        ttk.Button(footer,text='打开文件夹',command=open_folder).pack(side='left')
        ttk.Label(footer,text='已下载的文件可离线打开；其余视频会重新取得播放地址。',foreground='#637082').pack(side='right')
        tree.bind('<Double-1>',lambda event:open_selected())
        search.trace_add('write',refresh);english_only.trace_add('write',refresh);refresh()

    def model_settings(self):
        config=load_config();window=tk.Toplevel(self.root);window.title('翻译与解析设置');window.geometry('680x410')
        frame=ttk.Frame(window,padding=20);frame.pack(fill='both',expand=True)
        mode=tk.StringVar(value='在线服务' if config['mode']=='online' else '全部离线')
        endpoint=tk.StringVar(value=config.get('endpoint',''));model=tk.StringVar(value=config.get('model',''));key=tk.StringVar()
        ttk.Label(frame,text='缺少原中文字幕时，使用这里选择的模型；长难句按需生成。',wraplength=620).pack(anchor='w')
        ttk.Combobox(frame,textvariable=mode,values=('全部离线','在线服务'),state='readonly',width=30).pack(anchor='w',pady=10)
        for label,var,hidden in [('服务基础地址（HTTPS）',endpoint,False),('模型名称',model,False),('密钥（本机账户加密保存；留空保留原配置）',key,True)]:
            ttk.Label(frame,text=label).pack(anchor='w');ttk.Entry(frame,textvariable=var,show='•' if hidden else '').pack(fill='x',pady=(0,8))
        status=tk.StringVar(value='离线模型需要单独配置，结果需校对。选择在线后会发送相应字幕文字给指定服务，可能产生费用。')
        ttk.Label(frame,textvariable=status,wraplength=620,foreground='#637082').pack(anchor='w',pady=5)
        def save():
            try:save_config('online' if mode.get()=='在线服务' else 'local',endpoint.get(),model.get(),key.get())
            except ValueError as error:status.set(str(error));return
            window.destroy();self.status.set('翻译与解析设置已保存；原视频中文仍优先使用。')
        ttk.Button(frame,text='保存设置',command=save).pack(anchor='e',pady=8)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--self-check',action='store_true');parser.add_argument('--link',default='')
    args=parser.parse_args();root=tk.Tk();app=App(root)
    if args.link:app.input.insert('1.0',args.link)
    if args.self_check:root.update();print('Video UI initialized',root.winfo_width(),root.winfo_height());root.destroy()
    else:
        if args.link:root.after(300,app.parse)
        root.mainloop()
