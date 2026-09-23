# Architecture: quick signing-page collection

## Boundaries

- The canonical Python implementation lives in the Skill's bond_seal_pages package. The installed desktop app and the Skill call that same source at build time. The Explorer extension only transfers the selected paths and starts the desktop host.
- Collect accepts an explicit list of Word files. It never scans or moves the containing folder. Existing prepare, project-prepare, and project-init retain their behavior.
- Each quick batch belongs to one signing group. The menu does not classify files; the Skill asks for issuer or project_team when importing. Two batches may enter the two groups of one project.

## Request v1

The UTF-8 JSON request has version 1, a nonempty files array of absolute .doc or .docx paths, and optional output_directory. All files must be direct children of one directory. The default output directory is that shared parent. Source ordering is deterministic by case-folded filename, with the original filename as the tie breaker.

The public Python entry point is collect_selected_batch(request: dict, *, converter_factory=None, progress=None, cancel_event=None) -> dict. On success it returns status, absolute collection, batch_dir, and manifest paths, selected_count, and page_count. The CLI emits one JSON object on stdout; it uses exit codes 0 (success), 1 (processing/input failure), and 2 (environment unavailable).

## Output and quick-batch manifest

The first available pair is 签署页合集.pdf and 签署页合集_处理数据_请勿删除/; later runs use 签署页合集 (2) and so on. The PDF and sidecar directory are siblings beside the selected Word files. The sidecar contains quick_batch.json and the complete converted PDFs. A final PDF is published only after every selected Word and the resulting PDF have passed validation; it is published after the sidecar. A failed or cancelled run never overwrites prior output or leaves a complete-looking final PDF. Cancellation signals the isolated Word worker and removes its staging directory.

quick_batch.json uses format bond-seal-quick-batch and version 1. It records batch_id, absolute source_root, collection PDF basename and SHA-256, and one items record per selected Word, including duplicates. Each item records working_paper_id, working_paper_path relative to source_root, source SHA-256, converted_pdf relative to the sidecar, converted PDF SHA-256 and page count, one-based seal_page, reuse_of, and duplicate_group_sha256. Files with identical source SHA-256 share one collection page, while every source retains its own item and converted PDF.

## Import into the existing Skill

project-import-batch validates the manifest version, collection and cached PDF hashes, source Word hashes, path containment, page mapping, and duplicate relationships before modifying a project. It copies exactly the manifest-listed Word files into one new or missing project group; it never calls the directory-scanning project-init and never moves the user's originals. It adapts the quick manifest to the existing project manifest version 1 and marks the group prepared. Existing groups are not overwritten. A second quick batch may fill the other missing group.

## Compatibility and release

Old Skill commands and project manifests remain readable and retain their existing partial-success behavior. The new collect command alone uses all-or-nothing delivery. The x64 Windows 11 app uses Microsoft Word installed on the user's machine; its packaged Python runtime does not require a separate Python installation. A test-signed MSIX is for internal verification only. GitHub Releases production packages require a publicly trusted signature and verification before upload.
