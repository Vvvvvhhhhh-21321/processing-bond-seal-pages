# 按操作系统选择 Word 转 PDF 方案

为了兼顾 Windows 与 macOS，同时尽量保持底稿分页，Windows 使用本机 Microsoft Word 转换 `.doc` 和 `.docx`，macOS 使用 LibreOffice 无界面转换。转换能力通过平台适配边界隔离；系统只检测 Python、Conda、转换器和 OCR 前置条件并帮助用户安装，不静默创建回退环境或写死当前机器路径。
