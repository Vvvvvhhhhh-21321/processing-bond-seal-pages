from html import escape
from pathlib import Path


STATUS_LABELS = {
    "completed": "已处理",
    "low_confidence": "低置信度",
    "ambiguous": "匹配歧义",
    "unmatched": "未匹配",
    "missing_page": "缺少回章页",
    "conversion_failed": "转换失败",
    "ocr_failed": "OCR 失败",
    "date_failed": "日期失败",
    "write_failed": "写入失败",
    "invalid_batch": "批次校验失败",
}

DATE_LABELS = {
    "not_requested": "未要求补日期",
    "not_applied": "未应用",
    "filled": "已补齐",
    "already_present": "原页已有完整日期",
    "partial": "部分补齐",
    "failed": "补齐失败",
}


def _local_file_link(path, text, fragment=None):
    try:
        href = Path(path).resolve().as_uri()
    except (OSError, TypeError, ValueError):
        return escape(str(text))
    if fragment:
        href = f"{href}#{fragment}"
    return (
        f'<a href="{escape(href, quote=True)}" title="打开对应文件">'
        f"{escape(str(text))}</a>"
    )


def _source_file(source_root, relative_path):
    if not source_root:
        return None
    try:
        root = Path(source_root).resolve()
        candidate = (root / relative_path).resolve()
    except (OSError, TypeError, ValueError):
        return None
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def _report_status(item):
    if item.status == "failed":
        return "write_failed"
    if item.status == "completed" and item.date_status in {"failed", "partial"}:
        return "date_failed"
    return item.status


def _row_html(returned_pdf, seal_pages_pdf, record, item, source_root):
    if not isinstance(record, dict):
        record = {}
    status = _report_status(item)
    flags = []
    if item.status == "unmatched":
        flags.append("missing_page")
    source_path = str(record.get("working_paper_path", item.working_paper_id))
    source_file = _source_file(source_root, source_path)
    source_display = (
        _local_file_link(source_file, source_path)
        if source_file is not None
        else escape(source_path)
    )
    title = str(record.get("title") or "未能取得标题")
    seal_page = record.get("seal_page")
    score = "—" if item.score is None else str(item.score)
    output_text = "未生成" if item.output_path is None else str(item.output_path)
    output_display = (
        "未生成"
        if item.output_path is None
        else _local_file_link(item.output_path, output_text)
    )
    date_label = DATE_LABELS.get(item.date_status, item.date_status)
    reason = item.reason or item.date_reason or "—"
    search_text = " ".join(
        (source_path, title, STATUS_LABELS.get(status, status), reason, output_text)
    ).lower()
    seal_ref = "—" if seal_page is None else f"待盖章页 #{seal_page}"
    seal_display = (
        seal_ref
        if seal_page is None
        else _local_file_link(seal_pages_pdf, seal_ref, f"page={seal_page}")
    )
    returned_ref = (
        "—" if item.returned_page is None else f"回章页 #{item.returned_page}"
    )
    returned_display = (
        returned_ref
        if item.returned_page is None
        else _local_file_link(
            returned_pdf,
            returned_ref,
            f"page={item.returned_page}",
        )
    )
    classes = "case-row" if status == "completed" else "case-row anomaly"
    return f"""
      <article class="{classes}" data-status="{escape(status)}"
        data-flags="{escape(' '.join(flags))}" data-search="{escape(search_text)}">
        <div class="case-index"><span>{escape(STATUS_LABELS.get(status, status))}</span></div>
        <div class="case-main">
          <div class="case-heading">
            <div><p class="eyebrow">底稿文件</p><h2>{source_display}</h2></div>
            <div class="score"><small>相似度</small><strong>{escape(score)}</strong></div>
          </div>
          <p class="title-line">{escape(title)}</p>
          <dl class="trace-grid">
            <div><dt>待盖章页合集</dt><dd>{seal_display}</dd></div>
            <div><dt>回章页合集</dt><dd>{returned_display}</dd></div>
            <div><dt>日期结果</dt><dd>{escape(date_label)}</dd></div>
            <div><dt>输出 PDF</dt><dd>{output_display}</dd></div>
          </dl>
          <div class="reason"><b>处理说明</b><span>{escape(reason)}</span></div>
        </div>
      </article>"""


def _ocr_failure_html(ocr_failures):
    if not ocr_failures:
        return ""
    items = "".join(
        f"<li><b>回章页 #{failure.page}</b><span>{escape(failure.reason)}</span></li>"
        for failure in ocr_failures
    )
    return f"""
      <section class="ocr-note anomaly" data-status="ocr_failed">
        <div><p class="eyebrow">批次级异常</p><h2>OCR 失败</h2></div>
        <ul>{items}</ul>
      </section>"""


def write_processing_report(
    batch_root,
    returned_pdf,
    output_root,
    records,
    items,
    ocr_failures,
    working_paper_root=None,
    seal_pages_name="seal-pages.pdf",
):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "processing-report.html"
    seal_pages_pdf = _source_file(batch_root, seal_pages_name)
    rows = "".join(
        _row_html(
            Path(returned_pdf),
            seal_pages_pdf,
            record,
            item,
            working_paper_root,
        )
        for record, item in zip(records, items)
    )
    completed = sum(item.status == "completed" for item in items)
    abnormal = len(items) - sum(
        item.status == "completed" and item.date_status not in {"failed", "partial"}
        for item in items
    )
    ocr_section = _ocr_failure_html(ocr_failures)
    options = "".join(
        f'<option value="{key}">{label}</option>'
        for key, label in (
            ("all", "全部状态"),
            ("completed", "已处理"),
            ("low_confidence", "低置信度"),
            ("ambiguous", "匹配歧义"),
            ("unmatched", "未匹配"),
            ("missing_page", "缺少回章页"),
            ("conversion_failed", "转换失败"),
            ("ocr_failed", "OCR 失败"),
            ("date_failed", "日期失败"),
            ("write_failed", "写入失败"),
            ("invalid_batch", "批次校验失败"),
        )
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>底稿文件处理清单</title>
  <style>
    :root {{ --ink:#17201d; --paper:#f4f0e5; --sheet:#fffdf7; --line:#c9c1ae;
      --red:#a52b21; --green:#315c45; --muted:#6e7169; --shadow:0 18px 48px #342b1d18; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--paper);
      font-family:"Aptos","Microsoft YaHei UI",sans-serif; }}
    body::before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.28;
      background-image:repeating-linear-gradient(0deg,transparent 0 23px,#71664e10 24px); }}
    a {{ color:inherit; text-decoration-color:#a52b2166; text-underline-offset:3px; }}
    a:hover {{ color:var(--red); }}
    header, main, footer {{ position:relative; max-width:1180px; margin:auto; }}
    header {{ padding:58px 34px 28px; display:grid; grid-template-columns:1fr auto; gap:30px; }}
    h1, h2 {{ font-family:"Iowan Old Style","Songti SC","STSong",serif; margin:0; }}
    h1 {{ font-size:clamp(40px,6vw,82px); line-height:.92; max-width:760px; letter-spacing:-.04em; }}
    .kicker,.eyebrow {{ color:var(--red); font-weight:800; letter-spacing:.15em; text-transform:uppercase; }}
    .stamp {{ width:180px; height:180px; border:4px double var(--red); color:var(--red);
      border-radius:50%; display:grid; place-content:center; text-align:center; transform:rotate(5deg); }}
    .stamp strong {{ font:700 54px/1 "Songti SC","STSong",serif; }}
    .summary {{ grid-column:1/-1; display:flex; gap:12px; flex-wrap:wrap; margin-top:26px; }}
    .summary div {{ background:var(--sheet); border:1px solid var(--line); padding:12px 18px; min-width:148px; }}
    .summary b {{ display:block; font:700 30px/1.2 "Iowan Old Style",serif; }}
    main {{ padding:0 34px 50px; }}
    .boundary {{ background:#211e19; color:#f8f0df; padding:15px 18px; border-left:7px solid var(--red); }}
    .controls {{ position:sticky; top:0; z-index:5; margin:22px 0; padding:14px;
      display:grid; grid-template-columns:minmax(220px,1fr) 240px auto; gap:10px;
      background:#f4f0e5e8; backdrop-filter:blur(14px); border-block:1px solid var(--line); }}
    input, select, button {{ min-height:44px; border:1px solid var(--line); background:var(--sheet);
      color:var(--ink); padding:0 13px; font:inherit; }}
    button {{ background:var(--red); color:white; border-color:var(--red); cursor:pointer; font-weight:700; }}
    .case-row {{ display:grid; grid-template-columns:110px minmax(0,1fr); gap:24px;
      margin:16px 0; padding:24px; background:var(--sheet); border:1px solid var(--line);
      border-left:7px solid var(--green); box-shadow:var(--shadow); }}
    .case-row.anomaly {{ border-left-color:var(--red); }}
    .case-index span {{ display:inline-block; padding:7px 9px; color:white; background:var(--green); font-weight:800; }}
    .anomaly .case-index span {{ background:var(--red); }}
    .case-heading {{ display:flex; justify-content:space-between; gap:20px; }}
    .case-heading h2 {{ font-size:clamp(20px,2vw,30px); overflow-wrap:anywhere; }}
    .eyebrow {{ margin:0 0 5px; font-size:11px; }}
    .score {{ text-align:right; }} .score small {{ display:block; color:var(--muted); }}
    .score strong {{ font:700 34px/1 "Iowan Old Style",serif; }}
    .title-line {{ font-family:"Songti SC","STSong",serif; font-size:19px; }}
    .trace-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px 20px; margin:18px 0; }}
    .trace-grid div {{ min-width:0; border-top:1px solid var(--line); padding-top:8px; }}
    dt {{ color:var(--muted); font-size:12px; }} dd {{ margin:3px 0 0; overflow-wrap:anywhere; }}
    .reason {{ display:grid; grid-template-columns:74px 1fr; gap:10px; padding:12px; background:#eee8da; }}
    .ocr-note {{ display:grid; grid-template-columns:220px 1fr; gap:25px; padding:24px;
      margin:18px 0; color:white; background:#76241e; }}
    .ocr-note ul {{ margin:0; }} .ocr-note li {{ margin:6px 0; display:flex; gap:16px; }}
    [hidden] {{ display:none !important; }}
    footer {{ padding:0 34px 48px; color:var(--muted); }}
    @media(max-width:760px) {{ header {{ grid-template-columns:1fr; }} .stamp {{ display:none; }}
      .controls {{ grid-template-columns:1fr; }} .case-row {{ grid-template-columns:1fr; }}
      .trace-grid {{ grid-template-columns:1fr; }} .ocr-note {{ grid-template-columns:1fr; }} }}
    @media print {{ .controls {{ display:none; }} body {{ background:white; }} .case-row {{ break-inside:avoid; box-shadow:none; }} }}
  </style>
</head>
<body>
  <header>
    <div><p class="kicker">Processing Ledger · 处理批次终稿</p><h1>底稿文件<br>处理清单</h1></div>
    <div class="stamp"><strong>{len(items)}</strong><span>逐份留痕</span></div>
    <div class="summary"><div><span>底稿文件</span><b>{len(items)}</b></div><div><span>已完成回拼</span><b>{completed}</b></div><div><span>异常待核对</span><b>{abnormal}</b></div><div><span>OCR 异常页</span><b>{len(ocr_failures)}</b></div></div>
  </header>
  <main>
    <p class="boundary">边界说明：系统只处理页面识别、匹配、日期补齐和回拼；不检查页面是否已经盖章，也不判断印章真伪。</p>
    <section class="controls" aria-label="清单筛选">
      <input id="search-input" type="search" placeholder="搜索文件、标题、原因或输出位置">
      <select id="status-filter" aria-label="按状态筛选">{options}</select>
      <button id="next-anomaly" type="button">定位下一个异常</button>
    </section>
    <p id="visible-count" aria-live="polite"></p>
    {ocr_section}
    <section id="case-list">{rows}</section>
  </main>
  <footer>处理清单不生成页面缩略图，以减少处理时间和文件体积；需要核对时请直接打开对应底稿文件、回章页合集或输出 PDF。</footer>
  <script>
    (() => {{
      const rows = [...document.querySelectorAll('.case-row')];
      const search = document.querySelector('#search-input');
      const filter = document.querySelector('#status-filter');
      const count = document.querySelector('#visible-count');
      const ocrNote = document.querySelector('.ocr-note');
      let anomalyIndex = -1;
      function applyFilters() {{
        const query = search.value.trim().toLowerCase();
        const status = filter.value;
        let visible = 0;
        rows.forEach(row => {{
          const flags = row.dataset.flags.split(' ').filter(Boolean);
          const matchesStatus = status === 'all' || row.dataset.status === status || flags.includes(status);
          const matchesQuery = !query || row.dataset.search.includes(query);
          row.hidden = !(matchesStatus && matchesQuery);
          if (!row.hidden) visible += 1;
        }});
        if (ocrNote) ocrNote.hidden = !['all', 'ocr_failed'].includes(status);
        count.textContent = `当前显示 ${{visible}} / ${{rows.length}} 份底稿文件`;
        anomalyIndex = -1;
      }}
      search.addEventListener('input', applyFilters);
      filter.addEventListener('change', applyFilters);
      document.querySelector('#next-anomaly').addEventListener('click', () => {{
        const anomalies = rows.filter(row => !row.hidden && row.classList.contains('anomaly'));
        if (!anomalies.length) return;
        anomalyIndex = (anomalyIndex + 1) % anomalies.length;
        anomalies[anomalyIndex].scrollIntoView({{behavior:'smooth', block:'center'}});
        anomalies[anomalyIndex].focus({{preventScroll:true}});
      }});
      rows.forEach(row => row.tabIndex = -1);
      applyFilters();
    }})();
  </script>
</body>
</html>
"""
    temporary_path = report_path.with_suffix(".html.tmp")
    temporary_path.write_text(html, encoding="utf-8")
    temporary_path.replace(report_path)
    return report_path
