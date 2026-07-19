
# 阶段二：日期确认后回拼

本阶段拆成 `date-review` 和 `finalize` 两个独立命令。内部处理顺序是 OCR、匹配、落日期；用户看到的业务顺序是取得日期确认稿、确认或修改、再回拼。

## 2A：生成日期确认稿并暂停

输入必须包含第一阶段原处理批次和一个回章页合集 PDF。先完成 `date-review` 前置检查，再运行；仅在用户提供统一落款日期时追加日期参数：

```text
"<python>" "<skill-root>/scripts/run_bond_seal_pages.py" date-review "<处理批次目录>" "<回章页合集.pdf>" "<日期确认目录>"
"<python>" "<skill-root>/scripts/run_bond_seal_pages.py" date-review "<处理批次目录>" "<回章页合集.pdf>" "<日期确认目录>" --signing-date YYYY-MM-DD
```

脚本会：

- 校验处理批次中的路径、页数与哈希，并复用第一阶段完整底稿 PDF。
- 优先读取回章页文字层；不足时调用本机 PP-OCRv6 small。OCR 只识别标题，不负责猜测被印章遮挡的日期位置，也不改变 PDF 页面。
- 独立匹配乱序页面；相似度不低于 90 的唯一候选进入日期确认稿。低置信度、歧义、无标题和缺页仍跳过，不阻塞整批。
- 同标题先逐页配对；回章页数量不足时允许复用已匹配页面。
- 匹配成功后，从对应完整底稿 PDF 的最后一页提取完整日期锚点；同时计算图像特征配准与灰度相关性仿射配准，并用日期区域和整页对齐效果选择较可靠结果。
- 日期定位不依赖回章扫描件还能识别出完整“年、月、日”，因此印章覆盖日期字样不会直接导致失败。两套结果一致时正常填写；结果分歧或只有一套可用时采用较可靠结果填写，并标记 `filled_needs_review`；只有两套都无法产生可用坐标时才保留原页并记录失败。
- 对已匹配页面按需补齐空缺的年、月、日；已有组成部分保持原样，无法安全落位时记录日期异常。
- 保持回章页合集的原页数和原顺序，生成唯一的 `dated-returned-pages.pdf`；未匹配页面原样保留。
- 保存 `review-manifest.json`，记录首次匹配产生的“回章页码 → 底稿文件”映射，并为需重点核对的页面保存 `filled_needs_review` 与原因。此命令绝不创建 `completed-pdfs`，也不回拼底稿。

核对 JSON：`status` 必须是 `awaiting_confirmation`，`review_pdf` 和 `manifest` 必须存在，`items` 数量等于 `total`。`review_result=partial` 只表示存在低置信度、未匹配、OCR、日期异常或需重点核对页，确认稿仍已生成。若 `review_pages_requiring_attention` 非空，必须逐页提示用户重点检查。

然后原样输出 JSON 中的 `confirmation_message`，不要自行改写或遗漏 `review_pages_requiring_attention` 中的页码，并结束当前回复等待用户。无重点核对页时，提示仍会要求检查日期、允许使用金山 PDF 修改，同时强调不要增删页面或改变页面顺序。

即使所有页面都匹配成功，也不得在同一次处理中继续执行 `finalize`。只有用户在看到确认稿后明确回复确认，才进入 2B。

## 2B：用户确认后回拼

用户明确确认后，运行 `finalize` 前置检查，再执行：

```text
"<python>" "<skill-root>/scripts/run_bond_seal_pages.py" finalize "<处理批次目录>" "<日期确认目录>" "<输出目录>" --confirmed
```

脚本会直接读取用户可能已用金山 PDF 修改过的 `dated-returned-pages.pdf`。允许页面内容和 PDF 内部结构变化，不校验文件哈希；但页数必须与首次确认稿一致。页数变化时停止回拼，请用户恢复相同页数和顺序，或重新运行 `date-review`。

`finalize` 复用 `review-manifest.json` 中已保存的页码映射，不重新 OCR、不重新匹配，也不再次落日期。它直接用日期确认稿中的对应页面对象替换完整底稿 PDF 的最后一页，不缩放、裁切或重新栅格化；覆盖 `completed-pdfs` 与 `processing-report.html` 等本流程生成物，保留输出目录中的无关文件。

完成后核对：

- `total` 等于处理批次中的全部底稿记录数。
- `outcomes`、`date_outcomes`、`unused_pages` 与 `ocr_failures` 都已报告。
- 每个 `completed` 项都有对应盖章版 PDF。
- `items` 数量等于 `total`；逐项核对 `status`、`output_path`、`reason` 和 `date_status`。
- `report` 指向存在的 `processing-report.html`，且清单逐份显示底稿文件、待盖章页码、日期确认稿页码、标题、相似度、日期结果、状态、原因和输出位置。
- HTML 不含缩略图或嵌入页面图片；需要核对时直接打开对应文件链接。

向用户报告成功数、各异常状态数量、盖章版 PDF 目录和处理清单位置。`partial` 表示批次已尽可能完成，不等于整批失败。

## 兼容命令边界

统一入口保留旧版 `complete` 命令只是为了不破坏已有外部调用。本 skill 不得调用它，因为它会连续完成匹配、落日期和回拼，绕过本阶段要求的用户确认。
