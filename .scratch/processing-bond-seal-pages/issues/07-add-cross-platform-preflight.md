# 07 — 完成跨平台前置检查与 macOS 转换

**What to build:** 让 Skill 在 Windows 和 macOS 上先发现可用的 Python、Conda 与 Word 转换器，条件满足时使用正确方案，条件不足时清楚提示并在用户同意后帮助安装。

**Blocked by:** 02 — 从 Windows Word 生成处理批次。

**Status:** claimed

- [ ] 动态发现系统 Python、Python 启动器、当前 Conda/Anaconda 环境和当前执行环境，不写死机器路径。
- [ ] 多个 Python 可用时优先当前激活的 Conda 环境，其次当前执行环境；仍有歧义时列出候选让用户选择。
- [ ] 检查 Python 版本、架构、RapidOCR、ONNX Runtime、PDF 依赖、OCR 模型和平台转换器。
- [ ] Windows 检测并使用 Microsoft Word；macOS 检测并使用 LibreOffice 无界面转换。
- [ ] 前置条件不足时先说明缺失项和安装影响，得到用户同意后再帮助安装。
- [ ] 不静默创建私有回退环境，不擅自向多个 Python 或 Conda 环境安装依赖。
- [ ] 前置检查失败时不启动业务处理，也不生成半成品批次。
- [ ] 两个平台的转换器遵循相同输入输出契约，并分别具备契约测试与本机冒烟验证说明。
