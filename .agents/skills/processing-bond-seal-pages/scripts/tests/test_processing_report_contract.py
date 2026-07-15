import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bond_seal_pages.completion import (  # noqa: E402
    CompletionItem,
    complete_processing_batch,
)
from bond_seal_pages.date_completion import (  # noqa: E402
    SigningDateResult,
    SigningDateStatus,
)
from bond_seal_pages.processing_report import write_processing_report  # noqa: E402
from bond_seal_pages.returned_page_titles import OCRPageFailure  # noqa: E402
from completion_test_support import create_processing_batch, write_pdf_pages  # noqa: E402


class ProcessingReportContractTests(unittest.TestCase):
    def _write_status_report(self, root):
        batch_root = root / "处理批次"
        batch_root.mkdir()
        (batch_root / "seal-pages.pdf").write_bytes(b"seal pages")
        returned_pdf = root / "回章页合集.pdf"
        returned_pdf.write_bytes(b"returned pages")
        records = [
            {"working_paper_path": "低置信度.docx", "title": "低置信度标题", "seal_page": 1},
            {"working_paper_path": "歧义.docx", "title": "歧义标题", "seal_page": 2},
            {"working_paper_path": "缺页.docx", "title": "缺页标题", "seal_page": 3},
            {"working_paper_path": "日期失败.docx", "title": "日期标题", "seal_page": 4},
            {"working_paper_path": "写入失败.docx", "title": "写入标题", "seal_page": 5},
        ]
        items = (
            CompletionItem("low", "low_confidence", 1, 82, reason="相似度不足"),
            CompletionItem("ambiguous", "ambiguous", 2, 95, reason="候选并列"),
            CompletionItem("missing", "unmatched", reason="没有可匹配的回章页"),
            CompletionItem(
                "date",
                "completed",
                3,
                100,
                output_path=root / "日期失败.pdf",
                date_result=SigningDateResult(
                    SigningDateStatus.FAILED,
                    "日期无法安全落位",
                ),
            ),
            CompletionItem("write", "failed", 4, 100, reason="无法写入输出 PDF"),
        )
        report_path = write_processing_report(
            batch_root,
            returned_pdf,
            root / "回拼结果",
            records,
            items,
            (OCRPageFailure(6, "OCR 模型失败"),),
        )
        return report_path.read_text(encoding="utf-8")

    def test_abnormal_rows_keep_exact_status_fields_and_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            html = self._write_status_report(Path(directory))

            for status in (
                "low_confidence",
                "ambiguous",
                "unmatched",
                "date_failed",
                "write_failed",
            ):
                self.assertIn(f'data-status="{status}"', html)
            for value in (
                "低置信度.docx",
                "82",
                "相似度不足",
                "候选并列",
                "没有可匹配的回章页",
                "日期无法安全落位",
                "无法写入输出 PDF",
            ):
                self.assertIn(value, html)
            self.assertIn('data-flags="missing_page"', html)
            self.assertNotIn('data-flags="missing_page ocr_failed"', html)
            self.assertIn('data-status="ocr_failed"', html)

    def test_malformed_manifest_record_is_isolated_in_the_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("正常.docx", "正常标题")])
            manifest_path = batch_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"].append("损坏的清单记录")
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            returned_pdf = root / "回章页合集.pdf"
            write_pdf_pages(returned_pdf, [("《正常标题》", (400, 600))])

            result = complete_processing_batch(
                batch_root, returned_pdf, root / "回拼结果"
            )

            self.assertEqual(
                [item.status for item in result.items],
                ["completed", "invalid_batch"],
            )
            html = result.report_path.read_text(encoding="utf-8")
            self.assertIn('data-status="invalid_batch"', html)
            self.assertIn("批次校验失败", html)

    @unittest.skipUnless(shutil.which("node"), "需要 Node.js 执行离线交互脚本")
    def test_embedded_script_filters_and_navigates_actual_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = self._write_status_report(root)
            script = re.search(r"<script>\s*(.*?)\s*</script>", html, re.S).group(1)
            rows = [
                {
                    "status": status,
                    "flags": flags,
                    "search": search,
                    "anomaly": "anomaly" in classes.split(),
                }
                for classes, status, flags, search in re.findall(
                    r'<article class="([^"]+)" data-status="([^"]+)"\s+'
                    r'data-flags="([^"]*)" data-search="([^"]*)">',
                    html,
                )
            ]
            harness = f"""
const sourceRows = {json.dumps(rows, ensure_ascii=False)};
const rows = sourceRows.map(row => ({{
  dataset: row,
  hidden: false,
  tabIndex: 0,
  scrolls: 0,
  focuses: 0,
  classList: {{contains: name => name === 'anomaly' && row.anomaly}},
  scrollIntoView() {{ this.scrolls += 1; }},
  focus() {{ this.focuses += 1; }}
}}));
function control(value = '') {{
  return {{value, listeners: {{}}, addEventListener(type, callback) {{ this.listeners[type] = callback; }}}};
}}
const search = control('');
const filter = control('all');
const count = {{textContent: ''}};
const ocrNote = {{hidden: false}};
const button = control();
global.document = {{
  querySelectorAll: selector => selector === '.case-row' ? rows : [],
  querySelector: selector => ({{
    '#search-input': search,
    '#status-filter': filter,
    '#visible-count': count,
    '.ocr-note': ocrNote,
    '#next-anomaly': button
  }})[selector]
}};
eval({json.dumps(script, ensure_ascii=False)});
filter.value = 'write_failed';
filter.listeners.change();
const writeVisible = rows.filter(row => !row.hidden).map(row => row.dataset.status);
const ocrHiddenForWrite = ocrNote.hidden;
filter.value = 'all';
filter.listeners.change();
button.listeners.click();
const firstNavigated = rows.find(row => row.scrolls === 1).dataset.status;
search.value = '不存在的文件';
search.listeners.input();
const visibleAfterMissingSearch = rows.filter(row => !row.hidden).length;
console.log(JSON.stringify({{writeVisible, ocrHiddenForWrite, firstNavigated, visibleAfterMissingSearch}}));
"""
            harness_path = root / "report-interaction-test.js"
            harness_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [shutil.which("node"), str(harness_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["writeVisible"], ["write_failed"])
            self.assertTrue(result["ocrHiddenForWrite"])
            self.assertEqual(result["firstNavigated"], "low_confidence")
            self.assertEqual(result["visibleAfterMissingSearch"], 0)


if __name__ == "__main__":
    unittest.main()
