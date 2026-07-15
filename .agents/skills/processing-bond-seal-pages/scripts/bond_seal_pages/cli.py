import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sys

from .preflight import (
    build_installation_plan,
    format_preflight_report,
    run_preflight,
)


def _signing_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("落款日期必须使用 YYYY-MM-DD 格式") from error


def _parser():
    parser = argparse.ArgumentParser(
        description="处理债券底稿文件的待盖章页合集与回章页回拼",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="只执行前置检查")
    preflight.add_argument(
        "--stage",
        choices=("prepare", "complete"),
        default="prepare",
        help="prepare 检查 Word 转换器；complete 不要求转换器",
    )
    preflight.add_argument("--python", dest="selected_python")
    preflight.add_argument(
        "--approved-install-plan",
        action="store_true",
        help="仅在用户已明确同意安装后输出安装计划；本命令不会执行安装",
    )

    prepare = subparsers.add_parser("prepare", help="生成处理批次和待盖章页合集")
    prepare.add_argument("working_paper_root", type=Path)
    prepare.add_argument("batch_root", type=Path)
    prepare.add_argument("--python", dest="selected_python")

    complete = subparsers.add_parser("complete", help="使用回章页合集完成回拼")
    complete.add_argument("batch_root", type=Path)
    complete.add_argument("returned_pdf", type=Path)
    complete.add_argument("output_root", type=Path)
    complete.add_argument("--python", dest="selected_python")
    complete.add_argument("--signing-date", type=_signing_date)
    return parser


def _utf8_stdout():
    output = sys.stdout
    reconfigure = getattr(output, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="strict")
    return output


def _emit(payload, output):
    json.dump(payload, output, ensure_ascii=False, indent=2)
    output.write("\n")


def _preflight(args, preflight_runner):
    require_converter = args.command == "prepare" or (
        args.command == "preflight" and args.stage == "prepare"
    )
    return preflight_runner(
        selected_python=args.selected_python,
        require_converter=require_converter,
    )


def _preflight_payload(result):
    return {
        "ready": result.ready,
        "report": format_preflight_report(result),
        "selected_python": (
            result.selected.runtime.executable if result.selected else None
        ),
    }


def _prepare_payload(result):
    return {
        "command": "prepare",
        "status": "completed" if result.failed == 0 else "partial",
        "succeeded": result.succeeded,
        "failed": result.failed,
        "seal_pages_pdf": str(result.seal_pages_path),
        "manifest": str(result.manifest_path),
    }


def _complete_item_payload(item):
    output_path = getattr(item, "output_path", None)
    return {
        "working_paper_id": getattr(item, "working_paper_id", None),
        "status": item.status,
        "returned_page": getattr(item, "returned_page", None),
        "score": getattr(item, "score", None),
        "output_path": str(output_path) if output_path is not None else None,
        "reason": getattr(item, "reason", None),
        "date_status": getattr(item, "date_status", None),
        "date_reason": getattr(item, "date_reason", None),
    }


def _complete_payload(result):
    outcomes = Counter(item.status for item in result.items)
    date_outcomes = Counter(
        item.date_status
        for item in result.items
        if getattr(item, "date_status", None)
    )
    has_date_anomaly = any(
        status in {"failed", "partial"} for status in date_outcomes
    )
    partial = (
        result.succeeded != len(result.items)
        or bool(result.unused_pages)
        or bool(result.ocr_failures)
        or has_date_anomaly
    )
    return {
        "command": "complete",
        "status": "partial" if partial else "completed",
        "total": len(result.items),
        "succeeded": result.succeeded,
        "outcomes": dict(sorted(outcomes.items())),
        "items": [_complete_item_payload(item) for item in result.items],
        "date_outcomes": dict(sorted(date_outcomes.items())),
        "unused_pages": list(result.unused_pages),
        "ocr_failures": [
            {"page": failure.page, "reason": failure.reason}
            for failure in result.ocr_failures
        ],
        "output_root": str(result.output_root),
        "report": str(result.report_path),
    }


def main(
    argv=None,
    *,
    preflight_runner=run_preflight,
    installation_plan_builder=build_installation_plan,
    prepare_runner=None,
    complete_runner=None,
    output=None,
):
    output = _utf8_stdout() if output is None else output
    args = _parser().parse_args(argv)
    try:
        preflight_result = _preflight(args, preflight_runner)
        preflight_payload = _preflight_payload(preflight_result)
        if args.command == "preflight":
            payload = {
                "command": "preflight",
                "status": "ready" if preflight_result.ready else "not_ready",
                **preflight_payload,
            }
            if args.approved_install_plan:
                plan = installation_plan_builder(
                    preflight_result,
                    approved=True,
                )
                payload["installation_plan"] = {
                    "target_python": (
                        plan.target.runtime.executable if plan.target else None
                    ),
                    "commands": [list(command) for command in plan.commands],
                    "manual_steps": list(plan.manual_steps),
                }
            _emit(payload, output)
            return 0 if preflight_result.ready else 2

        if not preflight_result.ready:
            _emit(
                {
                    "command": args.command,
                    "status": "preflight_failed",
                    **preflight_payload,
                },
                output,
            )
            return 2

        if args.command == "prepare":
            if prepare_runner is None:
                from .processing_batch import prepare_processing_batch

                prepare_runner = prepare_processing_batch
            result = prepare_runner(
                args.working_paper_root,
                args.batch_root,
                preflight_result=preflight_result,
            )
            _emit(_prepare_payload(result), output)
            return 0

        if complete_runner is None:
            from .completion import complete_processing_batch

            complete_runner = complete_processing_batch
        result = complete_runner(
            args.batch_root,
            args.returned_pdf,
            args.output_root,
            signing_date=args.signing_date,
        )
        _emit(_complete_payload(result), output)
        return 0
    except Exception as error:
        _emit(
            {
                "command": args.command,
                "status": "failed",
                "error": str(error) or error.__class__.__name__,
            },
            output,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
