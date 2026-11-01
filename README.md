# Reading Room · 阅读书房

把视频变成可以反复阅读、标记和积累的英语学习材料。

A local-first video reading workspace: preview, clip export, bilingual transcripts,
personal highlights, a cross-material vocabulary collection, and Obsidian exports.

**GPL-3.0-only · Windows desktop · Python 3.12/3.13 · Local storage**

## 可以做什么

- 解析视频链接，预览封面和视频，保存完整视频、指定片段或 MP3 音频。
- 提取平台字幕；可选画面 OCR、语音识别和模型补译，来源分别标明。
- 用 D「杂志长文」阅读界面显示英文与中文；目录可收起，句段有编号。
- 从一句原文跳到视频位置，再返回原文句段。
- 主动归为英语学习的素材进入书架；框选单词、短语或句段标红。
- 总词汇库聚合相同文字，保留每次来源语境；句段收藏可以回到原文。
- 批量提前生成解析，逐句保存，中断后跳过已完成内容继续生成。
- 导出 Obsidian Markdown，保留来源与稳定块 ID；不覆盖手写笔记。

平台接口、登录状态、链接有效期和格式都会影响实际支持。抖音、B 站、小红书有页面适配，其他来源由 yt-dlp 尝试处理；这不代表所有链接都可用，也不保证自动去除画面内水印。项目不包含网盘解析或会员限速绕过服务。

## 快速开始

首发验证平台为 **Windows x64、Python 3.13**。推荐 Python 3.12 或 3.13，安装时包含 Tcl/Tk。其他操作系统尚未完成桌面验收；密钥保存使用 Windows DPAPI。

```powershell
git clone https://github.com/leedie210/reading-room.git
cd reading-room
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

之后可双击 `start.cmd`。本地工具窗口要保持运行，网页由本机临时服务提供；它不是 GitHub Pages 云端应用。

不准备下载视频时，可以先体验自编文字示例：

```powershell
.\.venv\Scripts\python.exe scripts/create_demo.py
.\.venv\Scripts\python.exe run.py
```

点击 **英语书架**，选择标有“合成演示”的素材。示例不含真实视频，不会伪装为用户已收录的内容。

## 使用流程

1. 粘贴视频链接或分享文案，点击“解析视频”，先预览再决定保存什么。视频已加载后，左方向键每次后退 5 秒；编辑输入框时保留正常光标操作。
2. 勾选“作为英语学习素材”，进入双语阅读。优先使用原视频字幕和译文，缺失部分才识别或补译。
3. 阅读页的“字幕与解析”内可生成阅读文本、提前生成本篇解析；关闭解析弹窗不会取消后台工作。
4. 框选同一英文句段里的文字，选择“存为词汇”或“存为句段”。默认收起目录，顶部可展开。
5. 在“我的书架／重点词汇／标记句段”切换。标记直接保存在本机，不依赖语言模型。

## Obsidian 和保存位置

默认所有个人状态位于项目下 `user_data/`，默认导出库为 `user_data/Obsidian/`。源码目录和个人数据分开。

要连接已有 Obsidian 库，**启动前**设置环境变量：

```powershell
$env:READING_ROOM_VAULT = Join-Path $HOME 'Documents\EnglishVault'
.\.venv\Scripts\python.exe run.py
```

可配置的环境变量：

| 变量 | 用途 | 默认 |
|---|---|---|
| `READING_ROOM_DATA` | 素材数据库、任务缓存与个人状态 | `user_data/` |
| `READING_ROOM_VAULT` | Obsidian 导出根目录 | `user_data/Obsidian/` |
| `READING_ROOM_DOWNLOADS` | 默认下载位置 | 数据目录下 `downloads/` |
| `READING_ROOM_TOOLS` | 模型与本地工具目录 | `tools/` |
| `READING_ROOM_NODE` | 可选 Node.js 路径 | 系统 `node` |

导出到 `04_视频精读/`，包括每素材阅读稿和“我的标记”下的总词句库。数据库是结构化记录的真源，Markdown 是可读视图；不做自动双向覆盖。编辑过的生成稿会保留，新结果另存版本。备份时同时备份数据目录、知识库和媒体原件。

## 可选能力

基础安装可使用书架、标记、平台字幕、视频预览和媒体导出；没有配置模型时不会自动下载大型模型。

| 能力 | 另外准备 |
|---|---|
| 画面字幕 OCR | `pip install -r requirements-ocr.txt`；RapidOCR 的模型准备取决于其上游版本，首次运行可能联网 |
| 语音识别 | `pip install -r requirements-speech.txt`；按下面命令下载相应模型 |
| 离线翻译／解析 | 另行安装 llama.cpp 的 `llama-server` 与兼容 GGUF 模型 |
| 在线翻译／解析 | 在工具“翻译与解析设置”填写自己选择的 HTTPS 服务、模型及密钥 |
| YouTube JavaScript 挑战 | 安装上游推荐的 Node.js 等运行时；可用 `READING_ROOM_NODE` 指定 |
| 参考考试词表 | 可自备有权使用的词表，格式见 [模型与词表](docs/MODELS.md)；本仓库不分发第三方词典数据 |

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-speech.txt
.\.venv\Scripts\python.exe scripts/download_models.py whisper
# 可选：中文识别模型
.\.venv\Scripts\python.exe scripts/download_models.py sensevoice
```

Whisper large-v3 是大型下载。离线模型和在线模型都可能误译或误判语法，需对照原文；没有“所有视频零错字”的保证。原中文可用时优先保留。

## 隐私与网络

- 默认只监听 `127.0.0.1`，页面带临时访问 token，写操作检查同源。
- 解析、预览和下载会请求相应视频平台；登录文件由用户主动选择，本仓库不附带任何账号或 Cookie。
- 选择在线模型后，会把相应字幕文字与标题发送到用户配置的服务，可能产生费用。
- 数据、下载、字幕、登录信息、日志与模型不提交 Git；不要在 Issue 中粘贴密钥、Cookie、私有素材或完整个人数据库。
- 仅处理你有权使用的素材。项目许可不赋予第三方视频、字幕、词典或模型的使用权。

## 项目拆解与贡献

[架构与数据流](docs/ARCHITECTURE.md) · [模型与词表](docs/MODELS.md) · [第三方组件](THIRD_PARTY_NOTICES.md) · [贡献说明](CONTRIBUTING.md)

```powershell
cd local_tool
..\.venv\Scripts\python.exe -m unittest test_learning test_preview test_video_app test_analysis_jobs test_reading_marks -q
```

测试使用合成素材、临时数据库和本地媒体，模型调用以替身验证排队与持久化；不把此类测试描述为语言准确度验证。真实平台可用性、模型质量及人工界面验收另行进行。锁定的基础验证环境见 `requirements-tested.txt`。

版权 © 2026 leedie210 与贡献者。自有代码采用 [GNU GPL v3.0 only](LICENSE)。第三方组件按各自许可使用。
