# 麦芽 Meya v0.2.0

麦芽 v0.2 是首个同时提供 macOS 与 Windows 发行包的版本。本版本把语音输入拆成平台原生宿主、共享会话协议和可插拔识别 worker：系统快捷键、录音、浮窗和文字提交由平台适配器负责，模型、词库、学习和 IPC v2 契约保持一致。

## 主要更新

- **跨平台架构落地**：共享 .NET Core/UI、IPC v2 二进制帧协议、能力协商和 session golden trace；macOS 与 Windows 分别使用原生系统集成。
- **Windows x64 首发预览**：.NET 8 + Avalonia + WASAPI；长按右 Ctrl（约 250 ms）开始录音，Paraformer Streaming 持续刷新浮窗，松开后由最终识别 worker 定稿并写入原输入框；安装后支持当前用户自动启动。
- **macOS 输入体验增强**：保留系统拼音/ABC 作为键盘输入源，长按 Fn 使用麦芽语音；实时草稿和最终文本优先写入当前输入框。
- **Codex/Electron 兼容性修复**：针对 AX 节点重建和 AX 写入“返回成功但实际无变化”的情况，增加焦点节点刷新、UTF-16 草稿范围校验，以及 Codex 专用 Unicode 输入兜底。
- **模型角色保持可插拔**：实时识别与最终识别可独立选择，worker 通过能力上报决定 native streaming、windowed preview 和 final rescore 路径。
- **品牌与菜单更新**：统一麦芽 Meya v0.2 版本显示，更新兔头状态图标与 Windows 托盘图标。
- **构建与安全**：macOS 输入法使用本地签名证书构建，Windows/macOS 发行包均为自包含运行时；继续保持本地识别、词库和反馈学习，不上传音频或文本。

## 平台说明

### macOS

- Apple Silicon，macOS 14 或更高版本。
- 安装 `.pkg` 后，在“麦克风”“辅助功能”“输入监控”中允许麦芽 Meya。
- 键盘输入源继续使用 macOS 简体拼音或 ABC；长按 Fn 开始，松开结束。

### Windows

- Windows x64，内置 .NET 8 运行时，无需另装 .NET。
- 首次使用前运行模型与 Python 环境初始化脚本；需要麦克风权限。
- v0.2 的 Windows 版是预览版：实时结果展示在鼠标穿透浮窗中，最终结果提交到录音开始时捕获的输入窗口；若窗口在录音期间变化，会降级为剪贴板，避免误写。

## 已知限制

- Windows 当前发行包只提供 x64；ARM64、TSF 原生组合输入和 MSIX 签名将在后续版本完善。
- macOS 共享 Avalonia 管理窗口的自动 UI smoke 在部分 macOS 图形环境会触发 Avalonia Native RenderTimer 错误；Core/UI 编译和契约测试通过，系统输入法宿主不受影响。
- 识别效果仍受所选模型、麦克风质量和本地词库影响；建议为行业专名配置标准写法与发音别名。

## 验证摘要

- Swift 输入法类型检查通过。
- macOS 原生输入法签名与深度验证通过。
- 共享 .NET Core 契约测试通过。
- Windows 契约测试与自包含发布通过（发行构建时执行）。
- Python ASR、词库、热词、流式协议、输入安全和训练数据测试通过；模型评测脚本需要额外安装 `soundfile` 才能运行。

## 文件

- `Meya-v0.2.0-macos-arm64.pkg`：macOS 安装包。
- `Meya-v0.2.0-windows-x64.zip`：Windows x64 自包含预览版。

