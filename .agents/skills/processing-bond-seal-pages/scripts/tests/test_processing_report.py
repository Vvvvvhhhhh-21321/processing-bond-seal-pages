import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bond_seal_pages.completion import complete_processing_batch  # noqa: E402
from completion_test_support import (  # noqa: E402
    create_processing_batch,
    write_pdf_pages,
    write_scanned_pdf_pages,
)


class FailingOCREngine:
    def recognize_page(self, pdf_path, page_number):
        raise RuntimeError("OCR 模型读取失败")


class ProcessingReportTests(unittest.TestCase):
    def _complete_one(self, root, signing_date=None):
        batch_root = create_processing_batch(
            root,
            [("中文目录/核查说明.docx", "中文路径核查说明")],
        )
        working_paper_root = root / "底稿文件"
        working_paper = working_paper_root / "中文目录/核查说明.docx"
        working_paper.parent.mkdir(parents=True)
        working_paper.write_bytes(b"unchanged-word-file")
        manifest_path = batch_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["working_paper_root"] = str(working_paper_root)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        returned_pdf = root / "客户回章页合集.pdf"
        write_pdf_pages(returned_pdf, [("《中文路径核查说明》", (400, 600))])
        return complete_processing_batch(
            batch_root,
            returned_pdf,
            root / "回拼结果",
            signing_date=signing_date,
        )

    def test_completion_generates_lightweight_offline_report_with_trace_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._complete_one(Path(directory))

            self.assertTrue(result.report_path.is_file())
            html = result.report_path.read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", html.lower())
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("<link ", html.lower())
            self.assertNotIn("<script src=", html.lower())
            self.assertNotIn("<img", html.lower())
            self.assertNotIn("data:image", html.lower())
            for value in (
                "中文目录/核查说明.docx",
                "中文路径核查说明",
                "待盖章页 #1",
                "回章页 #1",
                "100",
                "未要求补日期",
                "已处理",
                str(result.items[0].output_path),
                "不生成页面缩略图",
            ):
                self.assertIn(value, html)
            self.assertGreaterEqual(html.count('href="file:///'), 3)
            self.assertIn("#page=1", html)
            self.assertIn("不检查页面是否已经盖章，也不判断印章真伪", html)

    def test_report_exposes_filters_and_anomaly_navigation_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._complete_one(Path(directory))
            html = result.report_path.read_text(encoding="utf-8")

            self.assertIn('id="status-filter"', html)
            self.assertIn('id="search-input"', html)
            self.assertIn('id="next-anomaly"', html)
            self.assertIn('data-status="completed"', html)
            self.assertIn("applyFilters", html)
            self.assertIn("scrollIntoView", html)
            self.assertIn("ocrNote.hidden", html)
            for status in (
                "low_confidence",
                "ambiguous",
                "unmatched",
                "missing_page",
                "conversion_failed",
                "ocr_failed",
                "date_failed",
                "write_failed",
            ):
                self.assertIn(f'value="{status}"', html)

    def test_conversion_failure_remains_a_working_paper_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(root, [("正常.docx", "正常说明")])
            manifest_path = batch_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["items"].append(
                {
                    "working_paper_id": "失败目录/坏文件.doc",
                    "working_paper_path": "失败目录/坏文件.doc",
                    "status": "failed",
                    "error": "Microsoft Word 转换失败",
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            returned_pdf = root / "回章页合集.pdf"
            write_pdf_pages(returned_pdf, [("《正常说明》", (400, 600))])

            result = complete_processing_batch(
                batch_root, returned_pdf, root / "回拼结果"
            )

            self.assertEqual(
                [item.status for item in result.items],
                ["completed", "conversion_failed"],
            )
            html = result.report_path.read_text(encoding="utf-8")
            self.assertIn("失败目录/坏文件.doc", html)
            self.assertIn("转换失败", html)
            self.assertIn("Microsoft Word 转换失败", html)
            self.assertIn("未生成", html)

    def test_ocr_failure_is_reported_without_losing_working_paper_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch_root = create_processing_batch(
                root, [("扫描件.docx", "扫描件核查说明")]
            )
            returned_pdf = root / "扫描回章页合集.pdf"
            write_scanned_pdf_pages(returned_pdf, [("white", None)])

            result = complete_processing_batch(
                batch_root,
                returned_pdf,
                root / "回拼结果",
                ocr_engine=FailingOCREngine(),
            )

            self.assertEqual(result.items[0].status, "unmatched")
            self.assertEqual(len(result.ocr_failures), 1)
            html = result.report_path.read_text(encoding="utf-8")
            self.assertIn("扫描件.docx", html)
            self.assertIn("OCR 失败", html)
            self.assertIn("OCR 模型读取失败", html)

    def test_rerun_overwrites_old_report_and_completed_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "回拼结果"
            completed_root = output_root / "completed-pdfs"
            completed_root.mkdir(parents=True)
            (completed_root / "过期文件.pdf").write_bytes(b"stale")
            (output_root / "processing-report.html").write_text(
                "OLD-REPORT-MARKER", encoding="utf-8"
            )

            result = self._complete_one(root)

            self.assertFalse((completed_root / "过期文件.pdf").exists())
            html = result.report_path.read_text(encoding="utf-8")
            self.assertNotIn("OLD-REPORT-MARKER", html)


if __name__ == "__main__":
    unittest.main()
