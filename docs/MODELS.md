# 模型、运行时与词表

这些组件不包含在 GPL 源码授权范围内。下载前核对上游许可与硬件要求；仓库不会在启动时自动下载大型模型。

## 语音识别

- Whisper：`Systran/faster-whisper-large-v3`，脚本读取 Hugging Face 元数据、固定实际下载 revision，并校验已发布的 LFS SHA-256；输出在工具目录 `models/faster-whisper-large-v3/`。
- 中文可选：`csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`，下载量化模型和上游 LICENSE，写入 `models/sensevoice-small/`。
- CUDA 不是基础要求；GPU 运行库由用户按上游指引安装。本仓库不分发 NVIDIA 运行库。

模型下载脚本仅由显式命令触发，网络中断可重新运行；没有断点续传保证。

## 离线翻译与解析

从 [llama.cpp](https://github.com/ggml-org/llama.cpp) 取得适配机器的 `llama-server`。自行选择有权使用的 GGUF 文件，启动前设置：

```powershell
$env:READING_ROOM_LLAMA_SERVER = 'D:\AI\llama\llama-server.exe'
$env:READING_ROOM_GGUF = 'D:\AI\models\your-model.gguf'
# 如需 GPU，自行设置合适层数；默认 0
$env:READING_ROOM_GPU_LAYERS = '24'
```

这些是说明用路径，不是本机文件。历史开发环境使用 Qwen3-8B 推理模式，现有提示词也依此设计；其他 GGUF 模型未逐一验证兼容性。默认 CPU 模式可能很慢。输出仍需校对，不能保证语法判断或翻译正确。

在线服务在桌面设置中配置。DPAPI 密钥保护绑定 Windows 账户，搬移数据不等于可在另一个账户解密；重新输入凭据，不要提交配置文件。

## 词表

公开发行版不包含个人词库或来自第三方商业词典的抓取数据。可以自行准备**有权使用**的 UTF-8 JSON，放到工具目录 `wordlists/reference.json`：

```json
{
  "sources": [],
  "words": {
    "example": {"tags": [], "meanings": ["自编释义：例子"]}
  }
}
```

如需标签与来源列表，支持 `ielts`、`cet6`、`tem8`，`sources` 中提供 `tag`、`book`、`count`、`revision`、`source`。没有数据时只保留自编短语参考与个人标记，不宣称命中考试词表。历史的 `kajweb/dict` 来源标识只是兼容旧数据，不代表该数据可随本项目再分发。
