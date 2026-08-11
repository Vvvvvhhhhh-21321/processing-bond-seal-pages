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
        description="处理债券底稿文件的待盖章页合集、日期确认与回章页回拼",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="只执行前置检查")
    preflight.add_argument(
        "--stage",
        choices=("prepare", "date-review", "finalize", "complete"),
        default="prepare",
        help="只有 prepare 检查 Word 转换器；其余阶段不要求转换器",
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
    prepare.add_argument(
        "--duplicate-policy",
        choices=("keep", "deduplicate"),
        required=True,
        help="keep 保留全部 Word；deduplicate 按文件 SHA-256 排除重复 Word",
    )

    date_review = subparsers.add_parser(
        "date-review",
        help="匹配并生成一份已落日期的待确认 PDF，不回拼底稿",
    )
    date_review.add_argument("batch_root", type=Path)
    date_review.add_argument("returned_pdf", type=Path)
    date_review.add_argument("review_root", type=Path)
    date_review.add_argument("--python", dest="selected_python")
    date_review.add_argument("--signing-date", type=_signing_date)

    finalize = subparsers.add_parser(
        "finalize",
        help="用户确认日期稿后，按已保存的匹配关系回拼",
    )
    finalize.add_argument("batch_root", type=Path)
    finalize.add_argument("review_root", type=Path)
    finalize.add_argument("output_root", type=Path)
    finalize.add_argument("--python", dest="selected_python")
    finalize.add_argument("--confirmed", action="store_true", required=True)

    complete = subparsers.add_parser("complete", help="兼容旧版一体化回拼流程")
    complete.add_argument("batch_root", type=Path)
    complete.add_argument("returned_pdf", type=Path)
    complete.add_argument("output_root", type=Path)
    complete.add_argument("--python", dest="selected_python")
    complete.add_argument("--signing-date", type=_signing_date)

    project_init = subparsers.add_parser(
        "project-init",
        help="内部：初始化签署页处理项目",
    )
    project_init.add_argument("project_parent", type=Path)
    project_init.add_argument("project_name")
    project_init.add_argument("--issuer-source", type=Path)
    project_init.add_argument("--project-team-source", type=Path)
    project_init.add_argument("--confirmed", action="store_true", required=True)

    project_status = subparsers.add_parser(
        "project-status",
        help="内部：读取签署页处理项目状态",
    )
    project_status.add_argument("project_root", type=Path)

    project_prepare = subparsers.add_parser(
        "project-prepare",
        help="内部：生成一个签署文件组的待签署材料",
    )
    project_prepare.add_argument("project_root", type=Path)
    project_prepare.add_argument("group", choices=("issuer", "project_team"))
    project_prepare.add_argument(
        "--duplicate-policy",
        choices=("individual", "reuse"),
        required=True,
    )
    project_prepare.add_argument("--python", dest="selected_python")

    project_review = subparsers.add_parser(
        "project-date-review",
        help="内部：接收签署回页并生成日期确认稿",
    )
    project_review.add_argument("project_root", type=Path)
    project_review.add_argument("group", choices=("issuer", "project_team"))
    project_review.add_argument("returned_pdf", type=Path)
    project_review.add_argument("--signing-date", type=_signing_date)
    project_review.add_argument("--python", dest="selected_python")

    visual_package = subparsers.add_parser(
        "project-visual-package",
        help="内部：渲染待模型复核的日期页面",
    )
    visual_package.add_argument("project_root", type=Path)
    visual_package.add_argument("group", choices=("issuer", "project_team"))
    visual_package.add_argument("--python", dest="selected_python")

    visual_apply = subparsers.add_parser(
        "project-visual-apply",
        help="内部：应用模型给出的日期坐标",
    )
    visual_apply.add_argument("project_root", type=Path)
    visual_apply.add_argument("group", choices=("issuer", "project_team"))
    visual_apply.add_argument("page", type=int)
    visual_apply.add_argument("--proposal-json", default="{}")
    visual_apply.add_argument(
        "--model-decision",
        choices=("approved", "needs_adjustment", "unable_to_determine"),
        required=True,
    )
    visual_apply.add_argument("--python", dest="selected_python")

    visual_skip = subparsers.add_parser(
        "project-visual-skip",
        help="内部：记录当前模型不支持图像输入",
    )
    visual_skip.add_argument("project_root", type=Path)
    visual_skip.add_argument("group", choices=("issuer", "project_team"))

    project_finalize = subparsers.add_parser(
        "project-finalize",
        help="内部：确认日期并生成分组最终成果",
    )
    project_finalize.add_argument("project_root", type=Path)
    project_finalize.add_argument("group", choices=("issuer", "project_team"))
    project_finalize.add_argument("--confirmed", action="store_true", required=True)
    project_finalize.add_argument("--accept-attention", action="store_true")
    project_finalize.add_argument("--python", dest="selected_python")

    project_rebuild = subparsers.add_parser(
        "project-rebuild",
        help="内部：依据当前 Word 与签署回页补建处理数据",
    )
    project_rebuild.add_argument("project_root", type=Path)
    project_rebuild.add_argument("group", choices=("issuer", "project_team"))
    project_rebuild.add_argument("--returned-pdf", type=Path)
    project_rebuild.add_argument("--signing-date", type=_signing_date)
    project_rebuild.add_argument("--confirmed", action="store_true", required=True)
    project_rebuild.add_argument("--python", dest="selected_python")

    project_clean = subparsers.add_parser(
        "project-clean",
        help="内部：经独立确认后把处理数据移入系统回收站",
    )
    project_clean.add_argument("project_root", type=Path)
    project_clean.add_argument("--confirmed", action="store_true", required=True)
    project_clean.add_argument("--accept-attention", action="store_true")
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
    require_converter = args.command in {"prepare", "project-prepare", "project-rebuild"} or (
        args.command == "preflight" and args.stage == "prepare"
    )
    return preflight_runner(
        selected_python=getattr(args, "selected_python", None),
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
        "duplicate_policy": getattr(result, "duplicate_policy", None),
        "excluded_duplicates": getattr(result, "excluded_duplicates", 0),
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


def _review_pages_requiring_attention(result):
    return sorted(
        {
            item.returned_page
            for item in result.items
            if getattr(item, "date_status", None) == "filled_needs_review"
            and getattr(item, "returned_page", None) is not None
        }
    )


def _date_review_confirmation_message(result, attention_pages):
    lines = [f"已生成已落日期的盖章页确认稿：{result.review_pdf}。"]
    if attention_pages:
        pages = "、".join(str(page) for page in attention_pages)
        lines.append(
            f"第 {pages} 页的日期定位结果需要重点核对；"
            "系统已采用较可靠的定位结果落日期。"
        )
    lines.append(
        "请检查日期是否正确。你可以使用金山 PDF 修改页面内容，"
        "但不要增删页面，也不要改变页面顺序。"
    )
    lines.append("确认无误后请回复“确认回拼”。")
    return "\n".join(lines)


def _date_review_payload(result):
    outcomes = Counter(item.status for item in result.items)
    date_outcomes = Counter(
        item.date_status
        for item in result.items
        if getattr(item, "date_status", None)
    )
    partial = (
        result.succeeded != len(result.items)
        or bool(result.unused_pages)
        or bool(result.ocr_failures)
        or any(
            status in {"failed", "partial", "filled_needs_review"}
            for status in date_outcomes
        )
    )
    attention_pages = _review_pages_requiring_attention(result)
    return {
        "command": "date-review",
        "status": "awaiting_confirmation",
        "review_result": "partial" if partial else "completed",
        "requires_user_confirmation": True,
        "review_pages_requiring_attention": attention_pages,
        "confirmation_message": _date_review_confirmation_message(
            result,
            attention_pages,
        ),
        "total": len(result.items),
        "ready_for_review": result.succeeded,
        "outcomes": dict(sorted(outcomes.items())),
        "items": [_complete_item_payload(item) for item in result.items],
        "date_outcomes": dict(sorted(date_outcomes.items())),
        "unused_pages": list(result.unused_pages),
        "ocr_failures": [
            {"page": failure.page, "reason": failure.reason}
            for failure in result.ocr_failures
        ],
        "review_root": str(result.review_root),
        "review_pdf": str(result.review_pdf),
        "manifest": str(result.manifest_path),
    }


def _complete_payload(result, command="complete"):
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
        "command": command,
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
    date_review_runner=None,
    finalize_runner=None,
    output=None,
):
    output = _utf8_stdout() if output is None else output
    args = _parser().parse_args(argv)
    try:
        no_preflight_commands = {
            "project-init",
            "project-status",
            "project-visual-skip",
            "project-clean",
        }
        if args.command in no_preflight_commands:
            preflight_result = None
            preflight_payload = None
        else:
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

        if preflight_result is not None and not preflight_result.ready:
            _emit(
                {
                    "command": args.command,
                    "status": "preflight_failed",
                    **preflight_payload,
                },
                output,
            )
            return 2

        if args.command.startswith("project-"):
            from .project_workflow import (
                apply_visual_date_calibration,
                clean_project_processing_data,
                create_group_date_review,
                finalize_signing_group,
                initialize_signing_project,
                inspect_signing_project,
                prepare_signing_group,
                prepare_visual_review_package,
                rebuild_signing_group,
                skip_group_visual_review,
            )

            if args.command == "project-init":
                sources = {
                    key: value
                    for key, value in (
                        ("issuer", args.issuer_source),
                        ("project_team", args.project_team_source),
                    )
                    if value is not None
                }
                result = initialize_signing_project(
                    args.project_parent,
                    args.project_name,
                    sources,
                    confirmed=args.confirmed,
                )
                payload = {
                    "command": args.command,
                    "status": "resumed" if result.resumed else "initialized",
                    "project_root": str(result.project_root),
                    "manifest": str(result.manifest_path),
                    "moved_files": result.moved_files,
                    "requires_confirmation": result.requires_confirmation,
                }
            elif args.command == "project-status":
                result = inspect_signing_project(args.project_root)
                payload = {
                    "command": args.command,
                    "status": result.status,
                    "project_root": str(result.project_root),
                    "groups": result.groups,
                    "requires_confirmation": result.requires_confirmation,
                }
            elif args.command == "project-prepare":
                result = prepare_signing_group(
                    args.project_root,
                    args.group,
                    duplicate_policy=args.duplicate_policy,
                    preflight_result=preflight_result,
                )
                payload = {
                    "command": args.command,
                    "status": "completed" if result.failed == 0 else "partial",
                    "group": result.group_key,
                    "collection": str(result.collection_path),
                    "batch_manifest": str(result.batch_manifest_path),
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                }
            elif args.command == "project-date-review":
                result = create_group_date_review(
                    args.project_root,
                    args.group,
                    args.returned_pdf,
                    signing_date=args.signing_date,
                )
                payload = {
                    "command": args.command,
                    "status": "awaiting_visual_review",
                    "group": result.group_key,
                    "returned_original": str(result.returned_original),
                    "review_pdf": str(result.review_pdf),
                    "review_manifest": str(result.review_manifest_path),
                    "succeeded": result.succeeded,
                    "attention_pages": list(result.attention_pages),
                }
            elif args.command == "project-visual-package":
                result = prepare_visual_review_package(
                    args.project_root,
                    args.group,
                )
                payload = {
                    "command": args.command,
                    "status": "ready" if result.pages else "not_required",
                    "group": result.group_key,
                    "pages": list(result.pages),
                    "log": str(result.log_path),
                }
            elif args.command == "project-visual-apply":
                proposals = json.loads(args.proposal_json)
                result = apply_visual_date_calibration(
                    args.project_root,
                    args.group,
                    args.page,
                    proposals,
                    model_decision=args.model_decision,
                )
                payload = {
                    "command": args.command,
                    "status": result.status,
                    "group": result.group_key,
                    "page": result.page_number,
                    "round": result.round_number,
                    "accepted": result.accepted,
                    "reason": result.reason,
                    "log": str(result.log_path),
                }
            elif args.command == "project-visual-skip":
                result = skip_group_visual_review(
                    args.project_root,
                    args.group,
                )
                payload = {
                    "command": args.command,
                    "status": "skipped_no_image_input",
                    "group": result.group_key,
                    "log": str(result.log_path),
                }
            elif args.command == "project-finalize":
                result = finalize_signing_group(
                    args.project_root,
                    args.group,
                    confirmed=args.confirmed,
                    accept_attention=args.accept_attention,
                )
                payload = {
                    "command": args.command,
                    "status": "completed" if result.failed == 0 else "partial",
                    "group": result.group_key,
                    "result_root": str(result.result_root),
                    "report": str(result.report_path),
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                }
            elif args.command == "project-rebuild":
                result = rebuild_signing_group(
                    args.project_root,
                    args.group,
                    confirmed=args.confirmed,
                    returned_pdf=args.returned_pdf,
                    signing_date=args.signing_date,
                    preflight_result=preflight_result,
                )
                payload = {
                    "command": args.command,
                    "status": "awaiting_visual_review",
                    "group": result.group_key,
                    "warning": result.warning,
                    "review_pdf": str(result.review.review_pdf),
                    "attention_pages": list(result.review.attention_pages),
                }
            else:
                result = clean_project_processing_data(
                    args.project_root,
                    confirmed=args.confirmed,
                    accept_attention=args.accept_attention,
                )
                payload = {
                    "command": args.command,
                    "status": "cleaned",
                    "project_root": str(result.project_root),
                    "cleaned_path": str(result.cleaned_path),
                    "groups": list(result.groups),
                }
            _emit(payload, output)
            return 0

        if args.command == "prepare":
            if prepare_runner is None:
                from .processing_batch import prepare_processing_batch

                prepare_runner = prepare_processing_batch
            result = prepare_runner(
                args.working_paper_root,
                args.batch_root,
                preflight_result=preflight_result,
                duplicate_policy=args.duplicate_policy,
            )
            _emit(_prepare_payload(result), output)
            return 0

        if args.command == "date-review":
            if date_review_runner is None:
                from .review_workflow import create_date_review

                date_review_runner = create_date_review
            result = date_review_runner(
                args.batch_root,
                args.returned_pdf,
                args.review_root,
                signing_date=args.signing_date,
            )
            _emit(_date_review_payload(result), output)
            return 0

        if args.command == "finalize":
            if finalize_runner is None:
                from .review_workflow import finalize_date_review

                finalize_runner = finalize_date_review
            result = finalize_runner(
                args.batch_root,
                args.review_root,
                args.output_root,
                confirmed=args.confirmed,
            )
            _emit(_complete_payload(result, command="finalize"), output)
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
