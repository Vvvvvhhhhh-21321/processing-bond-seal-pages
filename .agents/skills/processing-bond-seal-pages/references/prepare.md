# 阶段一：生成处理批次

## 输入与命令

准备两个互相独立的目录：底稿文件目录和处理批次目录。处理批次目录宜与底稿文件目录并列，避免把生成物混入原文件树。

先完成 `SKILL.md` 的 `prepare` 前置检查，再运行：

```text
"<python>" "<skill-root>/scripts/run_bond_seal_pages.py" prepare "<底稿文件目录>" "<处理批次目录>" --duplicate-policy keep
"<python>" "<skill-root>/scripts/run_bond_seal_pages.py" prepare "<底稿文件目录>" "<处理批次目录>" --duplicate-policy deduplicate
```

只运行与用户选择对应的一条命令。不得省略 `--duplicate-policy`：

- `keep`：保留全部 Word。
- `deduplicate`：在任何 Word 转换前计算全部源文件 SHA-256；每组只保留路径排序最前的一份，其他重复文件完全退出后续流程。

## 脚本行为

- 递归读取 `.doc` 与 `.docx`，不修改原 Word。
- 在 Word 转换前计算源文件哈希；文件名、标题和页面外观不参与去重判断。
- Windows 调用 Microsoft Word，macOS 调用 LibreOffice 无界面转换。
- 只为筛选后保留的底稿保存完整 PDF，并逐份提取最后一页。
- 同标题但哈希不同的 Word 全部保留；哈希相同的 Word 是否排除只取决于用户选择。
- 生成 `seal-pages.pdf` 与 `manifest.json`；单文件转换失败写入清单，其他文件继续。
- 再次运行时覆盖本流程生成物，同时保留处理批次中的无关文件。

## 验证

核对 JSON 与文件：

- `seal_pages_pdf` 和 `manifest` 均存在。
- `succeeded + failed + excluded_duplicates` 等于本次发现的底稿文件数。
- `seal-pages.pdf` 页数等于 `succeeded`。
- `manifest.json` 的 `duplicate_policy` 与用户选择一致；`items` 只包含参与流程的底稿，成功项包含完整 PDF、标题、合集页码、页数和校验值；排除项只记录在 `excluded_duplicates`。

向用户报告成功数、失败数、排除的重复文件数、待盖章页合集位置和处理批次位置。若有排除项，明确说明这些文件不会参与匹配或生成最终文件。让用户把 `seal-pages.pdf` 发给客户，并完整保留处理批次供第二阶段使用。

完成标准：上述文件与数量一致。JSON `status` 为 `partial` 时，批次仍已生成；逐项报告失败后结束。
