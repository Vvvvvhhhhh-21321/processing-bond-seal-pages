# Windows 桌面版

## 使用

本程序需要 Windows 11 x64 和本机安装的 Microsoft Word，不需要单独安装 Python，也不需要代码签名证书或管理员权限。

1. 从 GitHub Actions 下载 BondSealPages-Windows-x64.zip，解压后双击 Install.cmd。
2. 在同一文件夹中选中一批 .doc 或 .docx 文件。
3. 右键选择“显示更多选项 → 生成签署页合集”。程序只启动一次，显示进度，成功后在文件旁生成 PDF 与处理数据目录。

选中的文件必须都在同一文件夹。取消或任一文件转换失败时，不会交付正式合集 PDF。已有合集不会被覆盖。

Agent 或脚本可调用安装目录的 %LOCALAPPDATA%\Programs\BondSealPages\bondseal.cmd。命令示例：

    & "$env:LOCALAPPDATA\Programs\BondSealPages\bondseal.cmd" collect --request-file C:\path\request.json --json

请求 JSON v1 使用 version: 1、绝对路径 files 列表及可选的 output_directory。project-import-batch 使用同一个 CLI 导入既有 Skill 项目。双击安装目录下当前版本的 Uninstall.cmd 可移除右键菜单和应用文件。

## 构建

在仓库根目录使用 Python 3.13 x64、uv、Visual Studio 2022 C++ Build Tools 和 Windows SDK：

    .\windows\scripts\build-portable.ps1

输出为 artifacts/BondSealPages-Windows-x64.zip，内含 GUI、CLI、独立 Word 工作进程与本机 Explorer 扩展。GitHub Actions 运行 Python 回归测试、构建和便携包布局检查。

## 安装机制与边界

安装脚本将程序复制到当前用户的 %LOCALAPPDATA%\Programs\BondSealPages，在 HKCU\Software\Classes 注册本机 IExplorerCommand，并保留版本目录供升级。资源管理器扩展只校验选中路径、写入请求 JSON 并启动 GUI；文档转换不在资源管理器进程内执行。未打包的传统菜单位于 Windows 11 的“显示更多选项”中。

本包没有数字签名，因此 Windows 下载保护可能提示未知发布者；安装时无需信任测试证书。安装、升级、卸载后的菜单显示和真实多选仍需在 Windows 11 资源管理器实机验收。
