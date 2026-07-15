# 07 — 完成跨平台前置检查与 macOS 转换

**What to build:** 让 Skill 在 Windows 和 macOS 上先发现可用的 Python、Conda 与 Word 转换器，条件满足时使用正确方案，条件不足时清楚提示并在用户同意后帮助安装。

**Blocked by:** 02 — 从 Windows Word 生成处理批次。

**Status:** resolved

- [x] 动态发现系统 Python、Python 启动器、当前 Conda/Anaconda 环境和当前执行环境，不写死机器路径。
- [x] 多个 Python 可用时优先当前激活的 Conda 环境，其次当前执行环境；仍有歧义时列出候选让用户选择。
- [x] 检查 Python 版本、架构、RapidOCR、ONNX Runtime、PDF 依赖、OCR 模型和平台转换器。
- [x] Windows 检测并使用 Microsoft Word；macOS 检测并使用 LibreOffice 无界面转换。
- [x] 前置条件不足时先说明缺失项和安装影响，得到用户同意后再帮助安装。
- [x] 不静默创建私有回退环境，不擅自向多个 Python 或 Conda 环境安装依赖。
- [x] 前置检查失败时不启动业务处理，也不生成半成品批次。
- [x] 两个平台的转换器遵循相同输入输出契约，并分别具备契约测试与本机冒烟验证说明。


## 实施结果

已完成跨平台前置检查与转换器选择。系统会动态发现当前 Conda/Anaconda、当前解释器、Windows Python 启动器及系统 Python，按既定优先级选择；若候选歧义可显式指定，若目标解释器与当前业务解释器不同则硬性停止并要求用目标 Python 重新运行。检查覆盖 64 位 Python 3.10+、RapidOCR、ONNX Runtime、PDF 依赖、PP-OCRv6 small 离线模型，以及 Windows Microsoft Word 或 macOS LibreOffice。

任何安装方案都必须先获得用户同意，只针对唯一选中的 Python；机器完全没有 Python 时可提供 winget、Homebrew 或人工安装步骤，不创建私有回退环境。前置检查失败不会创建处理批次。macOS LibreOffice 使用独立无界面配置并以同目录原子替换输出；Windows 与 macOS 均有契约测试及条件式真实文件冒烟测试。
