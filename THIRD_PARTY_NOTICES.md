# 第三方组件与数据

自有代码：Copyright © 2026 leedie210 and contributors，GPL-3.0-only。下面组件不是重新许可为 GPL 的自有代码；各自许可仍适用。

## 仓库内实际随附的第三方代码

**hls.js 1.7.3**：Apache-2.0；使用发布包中的构建产物。完整许可与版权声明位于 `tools/hls/LICENSE`，包来源与上游完整性摘要位于 `tools/hls/source.json`。文件未作功能修改。

## 通过包管理器安装的依赖（未随本仓库分发其代码或二进制）

| 组件 | 上游 | 上游许可说明 |
|---|---|---|
| yt-dlp | https://github.com/yt-dlp/yt-dlp | Unlicense；打包附加依赖各有许可 |
| imageio-ffmpeg | https://github.com/imageio/imageio-ffmpeg | Python 包 BSD-2-Clause；FFmpeg 二进制另按其构建配置许可 |
| NumPy | https://github.com/numpy/numpy | BSD-3-Clause |
| OpenCC Python reimplementation | https://github.com/yichen0831/opencc-python | Apache-2.0；转换数据遵循其上游声明 |
| pypinyin | https://github.com/mozillazg/python-pinyin | MIT |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | MIT；模型权重另行核对 |
| sherpa-onnx | https://github.com/k2-fsa/sherpa-onnx | Apache-2.0；模型另行核对 |
| RapidOCR | https://github.com/RapidAI/RapidOCR | Apache-2.0；OCR 模型、引擎及转依赖另行核对 |
| llama.cpp | https://github.com/ggml-org/llama.cpp | MIT；用户自行安装，GGUF 权重另行核对 |

最终安装版本及其转依赖以安装产物中的 LICENSE/NOTICE 为准。本仓库不分发语音/语言模型、第三方词典、APK、反编译代码、NVIDIA 库、FFmpeg 二进制、个人字幕或视频。

D 阅读界面仅参考杂志排版的视觉方向，未复制 Aeon、Readwise 或 Butterick 的源码、字体文件、标志或文章。演示文字为本项目自编；用户下载内容的权利不因本项目开源而改变。
