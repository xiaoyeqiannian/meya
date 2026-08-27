# 麦芽 Meya · Mac 本地语音输入法

麦芽是面向 Apple Silicon 的**语音输入法**：键盘继续用系统拼音或 ABC，语音是另一种输入形式。二者并存，互不抢输入源，类似 iPhone 上语音与中文键盘可以切换、互不影响。

识别模型是可插拔的。实时识别和最终定稿可分别选择 MLX Whisper 或 FunASR Paraformer；推荐由 Paraformer Streaming 输出低延迟中文草稿，松开 Fn 后由 SeACo-Paraformer 结合个人词库重新定稿，再由 CT-Punc-C 补全标点。行业黑话、论文专名、代码标识符由个人词库提供。模型下载后可以完全断网运行。

## 安装

```bash
git clone https://github.com/xiaoyeqiannian/meya.git
cd meya
./bootstrap.sh
./build_app.sh
./install_input_method.sh
```

安装后：

- 键盘输入源仍是 macOS 简体拼音或 ABC；
- 短按 `Fn` 仍由 macOS 处理；
- 长按 `Fn` 开始说话，松开后结束并提交；
- 临时识别文字会直接出现在当前输入框；
- 结束说话后，整段重新识别的最终文字会替换临时文字。

输入法安装在：

```text
~/Library/Input Methods/麦芽 Meya.app
```

菜单栏小芽图标只用于管理词库和识别模型，不负责开始或结束说话。

第一次启动需要允许三项 macOS 权限：

1. `隐私与安全性 → 麦克风`：用于本地录音；
2. `隐私与安全性 → 辅助功能`：用于把文字写入当前应用；
3. `隐私与安全性 → 输入监控`：用于在其他应用中监听长按 Fn。

如果没有辅助功能权限，最终文本依然会放入剪贴板。每次的最终录音保存在 `recordings/voice-input`，便于日后用作个人微调数据。

## 隐私与公开仓库

- 识别、词库纠正和反馈学习都在本机完成；运行时没有遥测或上传接口；
- `bootstrap.sh` 和模型下载脚本只在用户明确执行时联网下载依赖或模型；
- 录音、转写报告、个人词库、反馈记录、模型、证书和本机构建产物均被 `.gitignore` 排除；
- 反馈学习只在本机 SQLite 中持久化局部替换证据、确认/命中次数和激活结果，不保存完整识别文本、音频路径或应用名；
- 发布或提交代码前运行 `python3 scripts/audit_public.py`，它会扫描当前 Git 历史中的常见密钥、私人邮箱、本机路径和已知项目私密标记。

音频仍会按上文说明保存在本机 `recordings/voice-input`。不需要积累个人数据时，可以定期删除该目录。

重新构建应用：

```bash
./build_app.sh
```

## 直接试用麦克风

可以在 Finder 中双击 `开始语音识别测试.command`，或者在终端执行：

```bash
cd /path/to/meya
./run.sh 10
```

`10` 是录音秒数。首次使用会触发 macOS 麦克风授权。请先允许权限，然后重新执行。

## 转写已有 WAV

```bash
./.venv/bin/python transcribe.py /path/to/audio.wav --offline
```

音频需为 16-bit PCM WAV；采样率和声道数可以不是 16 kHz/单声道，脚本会本地转换。

## 管理个人关键词库

点击菜单栏中的麦芽 Meya 图标，选择“管理个人词库…”。可以：

- 为每个术语分别维护“标准写法”“发音/近音别名”和“常见识别错词”；
- 导入 UTF-8 编码的旧版 `.txt`，或三列 `.tsv` 文件；
- 点击“保存并立即生效”，无需训练或重启模型。

标准写法是最终要输出的内容，例如 `K8s`、`NovaKit`、`main`。实际发音形式用于帮助
SeACo 解码，例如标准写法 `NovaKit` 可以填写一次用户真正说出的 `诺瓦套件`；常见识别错词
只作少量最终兜底，不应穷举模型可能产生的错误文本。

词库窗口会根据当前 SeACo 模型的真实 tokenizer 显示状态：

- “已生效”：至少一个标准写法或发音形式可被完整编码并已进入模型热词；
- “部分无效”：仍含未知 token，不会将该无效形式送入模型；
- “需要发音”：标准拼写无法编码，需要填写一次实际说法。

完整词库的 tokenizer 检测结果与每轮识别动态选中的热词分别保存；打开或保存词库、学习新纠正及切换模型后，界面会重新检测全部术语，不会被某一次语音的局部结果覆盖。

对 `K8s` 这类规则明确的字母数字组合，系统会自动尝试 `k 八 s` 和 `k eight s`，但只有
通过当前模型 tokenizer 检测的形式才会启用。对 `NovaKit` 这类词典外专名，界面只会给出
可点击采纳的发音建议；未经用户确认的建议不会写入词库或参与解码。

结构化个人词库保存在 `~/Library/Application Support/Meya/glossary.tsv`，与应用安装包分开，
重装输入法不会覆盖。最多保存 100 个标准术语；识别时总计选取最多 100 个“标准术语 + 别名”，
并始终优先保留标准术语。短语会以“一行一个完整热词”的形式交给 SeACo，因此 `Acme CLI`
不会再被空格拆成两个无关热词。`terms.txt` 仍会在保存时自动生成，供旧版脚本兼容使用。

三列 TSV 的格式如下，多个别名或错词用顿号分隔：

```text
# 标准写法    发音/近音别名    常见识别错词
K8s           K 八 S           K八S
NovaKit       诺瓦套件         nova cat
main
```

也可以继续导入旧版 `terms.txt`，每行一个专有名词：

```text
FunASR
Whisper
持续集成
语音识别
```

如果要做 A/B 测试，可以禁用术语提示：

```bash
./.venv/bin/python transcribe.py sample.wav --offline --no-prompt
```

脚本会用简单的音量门避免静音“幻听”成术语。如果录音音量特别小，可以降低门限：

```bash
./.venv/bin/python transcribe.py sample.wav --offline --min-rms 0.003
```

旧版 `corrections.tsv` 仍兼容，每行是“常见错词 + TAB + 正确写法”：

```text
Maya	Meya
Parafomer	Paraformer
```

首次打开个人词库时，旧版术语和纠错会自动迁移到结构化词典。转写时会同时展示模型原文和
纠错结果，避免把规则纠错误当成模型能力。对照测试可用 `--no-corrections`。

### 学习刚才的修改

完成一次语音输入后：

- 麦芽会保留最终识别原文，并在学习菜单打开时按原应用进程重新获取当前输入框；未发送时修改了文字，点击“学习刚才的修改”会直接对比学习，不再弹出二次编辑框；
- 如果内容已经发送，或当前网页/应用完全不允许读取文本，麦芽会显示“识别原文 / 正确文本”双栏对照框；
- 已有标准词会增加错识别形式；用户明确点击学习时，新标准词也可自动进入个人词库；
- 系统会明确展示实际学到的映射，例如 `诺瓦 → NovaKit`。
- 菜单中的“管理已学规则…”可查看每条规则的触发证据、确认次数和实际命中次数，并可单条撤销；
- 近期热词权重每 30 天半衰；90 天未再激活、权重过低或超出 128 个近期词时会退出自动候选。输入框正文明确出现的手工术语仍可激活。

旧版 `feedback-candidates.json` 和 `hotword-usage.json` 会在首次启动时无损导入 `learning.sqlite3`，原文件保留作为本地备份。

读取失败或取消确认不会销毁上一句记录，可以再次点击学习。

## 构建本机安装包

```bash
./package_macos.sh
```

安装包输出到 `dist/`。这是当前 Mac 的离线安装包，复用
当前仓库中已经下载好的模型和 Python 运行环境；安装过程不会联网。

## 切换识别模型

点击菜单栏中的麦芽 Meya 图标，选择“管理识别模型…”。模型管理窗口内分别
选择“实时识别模型”和“最终识别模型”，也可以添加兼容的本地模型目录。
菜单栏不再展开模型列表。切换只会重启识别服务，不会重新安装输入法。

当前选择保存在 `model-config.json`。默认值是：

```json
{
  "preview_model": "paraformer:iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
  "final_model": "paraformer:iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
}
```

旧版只有 `model` 字段的配置会自动迁移：原模型作为最终识别模型，实时识别
模型优先选择本机已缓存的快速模型。

模型下载与输入法程序分离；添加新模型后，重新打开模型管理窗口即可发现。

当前识别后端支持 MLX Whisper、MLX Qwen3-ASR，以及 FunASR Paraformer 本地模型。三种架构都使用同一套实时/最终模型选择和 JSONL 控制协议。Paraformer Streaming 会建立持续会话，从 macOS 录音回调直接推送 480 ms PCM16 音频块并保留编码/解码缓存，不经过定时轮询或临时 WAV 文件；非流式模型仍自动使用滚动窗口兼容路径。常用兼容模型：

- `mlx-community/whisper-base-mlx`：资源占用最低，准确率较低；
- `mlx-community/whisper-small-mlx`：轻量、延迟低；
- `mlx-community/whisper-medium-mlx`：准确率和占用居中；
- `mlx-community/whisper-large-v3-turbo-4bit`：同架构 4bit 压缩版，体积小、精度略损；
- `mlx-community/whisper-large-v3-turbo`：推荐，未量化 Turbo，速度和精度较均衡；
- `mlx-community/whisper-large-v3-mlx`：完整大模型，准确率上限更高，但更慢、更占内存。
- `qwen:mlx-community/Qwen3-ASR-0.6B-4bit`：速度和内存优先的 Qwen3-ASR；
- `qwen:mlx-community/Qwen3-ASR-1.7B-4bit`：速度与准确率较均衡，支持原生热词提示；
- `paraformer:funasr/paraformer-zh`：中英文 Paraformer，本机模型约 0.85 GB，中文出字快。
- `paraformer:iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`：流式中文模型，推荐用于实时草稿；
- `paraformer:iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`：支持热词增强的 SeACo 模型，推荐用于最终定稿。

安装麦芽推荐的 Paraformer 流水线（Streaming 实时 + SeACo 热词定稿 + CT-Punc-C 标点）：

```bash
cd /path/to/meya
./download_paraformer.sh
```

下载完成后重新打开“管理识别模型…”，列表会显示 `Paraformer · Streaming 实时` 和 `Paraformer · SeACo 热词定稿`。最终识别进程会自动加载本机 CT-Punc-C，实时草稿不加标点，松开 Fn 后的定稿会补全标点。也可以用“添加本地模型目录…”选择包含 `config.yaml` 和 `model.pt` 的 Paraformer 目录。模型标识使用 `paraformer:` 前缀，旧的 Whisper 配置无需修改。

应用以严格离线模式运行，不会在切换时自动联网下载。先把模型下载到项目的
Hugging Face 缓存，再打开“管理识别模型…”选择它：

```bash
cd /path/to/meya
export HF_HOME="$PWD/models/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
./.venv/bin/hf download mlx-community/whisper-large-v3-turbo-4bit
./.venv/bin/hf download mlx-community/whisper-large-v3-mlx
./.venv/bin/hf download mlx-community/Qwen3-ASR-0.6B-4bit
./.venv/bin/hf download mlx-community/Qwen3-ASR-1.7B-4bit
```

模型管理只展示已实现适配器并通过本地结构检查的缓存。Qwen 会显示为
`Qwen3-ASR · ...`，不会再被标成 Whisper；旧版曾保存的无前缀 Qwen 配置会自动迁移。

## 确认已经离线

`--offline` 会设置 Hugging Face 离线模式。模型缓存位于：

```text
./models/huggingface
```

删除整个 `meya` 目录即可移除虚拟环境、模型和录音，没有安装系统级 Python 包。

## 切换到更大的模型

32GB 内存可以运行 `large-v3`，它的模型下载约 3GB：

```bash
./.venv/bin/python transcribe.py sample.wav \
  --model mlx-community/whisper-large-v3-mlx
```

## 重建环境

```bash
./bootstrap.sh
```

## 许可证

本项目采用 [MIT License](LICENSE)。
