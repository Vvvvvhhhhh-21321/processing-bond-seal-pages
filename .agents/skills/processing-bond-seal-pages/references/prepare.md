# 阶段一：生成处理批次

## 输入与命令

准备两个互相独立的目录：底稿文件目录和处理批次目录。处理批次目录宜与底稿文件目录并列，避免把生成物混入原文件树。

先完成 `SKILL.md` 的 `prepare` 前置检查，再运行：

```text
"<python>" "<skill-root>/scripts/run_bond_seal_pages.py" prepare "<底稿文件目录>" "<处理批次目录>"
```

## 脚本行为

- 递归读取 `.doc` 与 `.docx`，不修改原 Word。
- Windows 调用 Microsoft Word，macOS 调用 LibreOffice 无界面转换。
- 保存每份完整底稿 PDF，并逐份提取最后一页。
- 同标题盖章页全部保留，不去重。
- 生成 `seal-pages.pdf` 与 `manifest.json`；单文件转换失败写入清单，其他文件继续。
- 再次运行时覆盖本流程生成物，同时保留处理批次中的无关文件。

## 验证

核对 JSON 与文件：

- `seal_pages_pdf` 和 `manifest` 均存在。
- `succeeded + failed` 等于本次发现的底稿文件数。
- `seal-pages.pdf` 页数等于 `succeeded`。
- `manifest.json` 中每份底稿都有 `ready` 或 `failed` 状态；成功项包含完整 PDF、标题、合集页码、页数和校验值。

向用户报告成功数、失败数、待盖章页合集位置和处理批次位置。让用户把 `seal-pages.pdf` 发给客户，并完整保留处理批次供第二阶段使用。

完成标准：上述文件与数量一致。JSON `status` 为 `partial` 时，批次仍已生成；逐项报告失败后结束。
