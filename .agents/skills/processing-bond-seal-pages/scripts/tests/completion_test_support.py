from io import BytesIO
import json
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from bond_seal_pages.pdf_ops import sha256_file
from bond_seal_pages.titles import normalize_title


def write_pdf_pages(path, pages):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=pages[0][1])
    for index, (text, size) in enumerate(pages):
        if index:
            canvas.setPageSize(size)
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(50, size[1] - 50, text)
        canvas.showPage()
    canvas.save()


def create_processing_batch(root, records):
    batch_root = Path(root) / "处理批次"
    items = []
    for working_paper_id, title in records:
        converted_pdf = batch_root / "pdfs" / f"{working_paper_id}.pdf"
        converted_pdf.parent.mkdir(parents=True, exist_ok=True)
        write_pdf_pages(
            converted_pdf,
            [
                (f"正文-{working_paper_id}", (400, 600)),
                (f"《{title}》", (400, 600)),
            ],
        )
        items.append(
            {
                "working_paper_id": working_paper_id,
                "working_paper_path": working_paper_id,
                "status": "ready",
                "title": title,
                "normalized_title": normalize_title(title),
                "converted_pdf": converted_pdf.relative_to(batch_root).as_posix(),
                "pdf_page_count": 2,
                "pdf_sha256": sha256_file(converted_pdf),
                "seal_page": len(items) + 1,
            }
        )
    (batch_root / "manifest.json").write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    return batch_root


def write_scanned_pdf_pages(path, pages):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas = Canvas(str(path), pagesize=(400, 600))
    for index, (color, text_layer) in enumerate(pages):
        if index:
            canvas.setPageSize((400, 600))
        image_buffer = BytesIO()
        Image.new("RGB", (200, 300), color).save(image_buffer, format="PNG")
        image_buffer.seek(0)
        canvas.drawImage(ImageReader(image_buffer), 0, 0, width=400, height=600)
        if text_layer:
            canvas.setFont("STSong-Light", 10)
            canvas.drawString(20, 20, text_layer)
        canvas.showPage()
    canvas.save()
