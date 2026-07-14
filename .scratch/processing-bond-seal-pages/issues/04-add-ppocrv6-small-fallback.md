# 04 — 支持 PP-OCRv6 small 扫描页识别

**What to build:** 让纯扫描或文字层不足的回章页能够通过本机 RapidOCR 与 PP-OCRv6 `small` 识别标题，并进入与文字型页面相同的高置信度回拼流程。

**Blocked by:** 03 — 完成文字层回章页的自动回拼。

**Status:** resolved

- [x] 页面存在足够文字层时不调用 OCR，文字层缺失或不足时自动回退到 OCR。
- [x] OCR 使用 RapidOCR、ONNX Runtime 和 PP-OCRv6 `small` 中文模型。
- [x] OCR 结果只用于标题识别和定位，不写回、不重绘、不改变回章页 PDF。
- [x] 首次准备完成后，模型能够从本机缓存离线复用。
- [x] 单页 OCR 失败时该页进入未匹配状态，其他页面继续识别和回拼。
- [x] 常规自动化测试使用固定图片和可替换引擎边界，不在每次测试时下载模型。
- [x] 集成测试覆盖纯扫描标题、混合文字层与扫描页、OCR 错字归一化及识别失败场景。

## Answer

已为 `complete_processing_batch(..., ocr_engine=None)` 增加扫描页识别。文字标题与目标标题相似度达到统一的 90 分自动回拼阈值时直接使用文字层；文字层缺失、无关或只有 60–89 分的部分残留时，含栅格内容的页面自动进入 OCR。

OCR 适配器通过 PyMuPDF 以 200 DPI 渲染完整 PDF 页面，再调用 RapidOCR、ONNX Runtime 与 PP-OCRv6 `small` 中文模型。模型目录按操作系统写入稳定的用户缓存，初始化后的引擎在批次内复用。OCR 只产出标题候选，成功回拼仍直接使用原回章 PDF 页面，不写回渲染图。

单页 OCR 异常会保留为 `OCRPageFailure(page, reason)`，通过 `CompletionBatchResult.ocr_failures` 暴露；该页进入未匹配，其他页面继续处理。测试通过固定扫描 PDF、可注入 OCR 引擎及 RapidOCR/PyMuPDF 替身覆盖纯扫描、混合页面、残留文字、OCR 错字、整页渲染、缓存复用和页级失败，测试过程不下载模型。

实现提交：`ffebaa8`、`541b97e`、`e364ed5`。完整测试共 65 项：64 项通过；1 项真实 Word 冒烟测试按既有前置条件跳过。标准与规格双轴复审均无剩余阻断发现。
