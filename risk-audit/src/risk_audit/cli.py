from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from risk_audit.checks.registry import build_registry
from risk_audit.configuration import ConfigError, RulePackStore, load_pack, validate_pack
from risk_audit.runner import audit
from risk_audit.run_diff import compare_runs
from risk_audit.util import deep_diff, read_json, write_json
from risk_audit.validation.manual import compare_findings, extract_manual_labels


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器；无参数，返回审核及配置命令的解析器。"""
    p = argparse.ArgumentParser(prog="risk-audit")
    p.add_argument("--rulepacks-root", default="risk-audit/rulepacks")
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("config-validate"); v.add_argument("pack")
    d = sub.add_parser("config-draft"); d.add_argument("name"); d.add_argument("--from-version")
    df = sub.add_parser("config-diff"); df.add_argument("left"); df.add_argument("right")
    rdf = sub.add_parser("run-diff"); rdf.add_argument("left"); rdf.add_argument("right")
    pub = sub.add_parser("config-publish"); pub.add_argument("draft"); pub.add_argument("version")
    rb = sub.add_parser("config-rollback"); rb.add_argument("version")
    cap = sub.add_parser("capabilities")
    vb = sub.add_parser("vocabulary-build")
    vb.add_argument("--input", action="append", required=True)
    vb.add_argument("--output", required=True)
    vb.add_argument("--pack")
    vb.add_argument("--entities", default="reference-data/会计主体清单20260907.xlsx")
    vb.add_argument("--holdout-key", action="append", default=[])
    for name in ("audit", "trial"):
        a = sub.add_parser(name)
        a.add_argument("--input", required=True); a.add_argument("--output", required=name == "audit")
        a.add_argument("--pack"); a.add_argument("--entities", default="reference-data/会计主体清单20260907.xlsx")
        a.add_argument("--config", help="审核运行配置；未指定时自动读取当前目录的 audit-config.json")
        a.add_argument("--enable-rule", dest="enable_rules", action="append", default=[],
                       help="仅对本次运行启用指定规则展示编号，可重复使用")
        # 默认将运行记录保存到项目的 runs 目录，与 Git 忽略规则保持一致。
        a.add_argument("--project-root", default="."); a.add_argument("--runs-root", default="runs"); a.add_argument("--run-id")
        a.add_argument("--include-hidden", action="store_true", help="明确将隐藏工作表纳入读取范围；默认跳过并集中提示")
        a.add_argument("--scope-file", help="可选手动覆盖自动范围；默认按实际上传的风控矩阵和三清单确定范围")
    m = sub.add_parser("manual-extract"); m.add_argument("--input", required=True); m.add_argument("--output", required=True)
    c = sub.add_parser("manual-compare"); c.add_argument("--labels", required=True); c.add_argument("--findings", required=True); c.add_argument("--output", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    registry = build_registry(); store = RulePackStore(args.rulepacks_root, registry)
    try:
        if args.command == "config-validate":
            pack = load_pack(args.pack); validate_pack(pack, registry); _print({"valid": True, "rules": len(pack["rules"]), "capabilities": registry.versions()})
        elif args.command == "config-draft": _print({"draft": str(store.create_draft(args.name, args.from_version))})
        elif args.command == "config-diff": _print({"changes": store.diff(args.left, args.right)})
        elif args.command == "run-diff": _print(compare_runs(args.left, args.right))
        elif args.command == "config-publish": _print({"release": str(store.publish(args.draft, args.version))})
        elif args.command == "config-rollback": _print({"active": str(store.rollback(args.version))})
        elif args.command == "capabilities": _print({"capabilities": registry.versions()})
        elif args.command == "vocabulary-build":
            from risk_audit.vocabulary import build_vocabulary
            _print(build_vocabulary(args.input, args.output, args.pack or store.active_path(), args.entities, args.holdout_key))
        elif args.command in {"audit", "trial"}:
            pack = Path(args.pack).resolve() if args.pack else store.active_path()
            output = args.output if args.command == "audit" else Path(args.runs_root) / "_trial_no_output"
            default_config = Path("audit-config.json")
            config_file = args.config or (default_config if default_config.is_file() else None)
            _print(audit(args.input, output, pack, args.entities, args.project_root, args.runs_root,
                         write=args.command == "audit", run_id=args.run_id,
                         include_hidden=args.include_hidden, scope_file=args.scope_file,
                         config_file=config_file, enabled_rules=args.enable_rules))
        elif args.command == "manual-extract": _print(extract_manual_labels(args.input, args.output)["counts"])
        elif args.command == "manual-compare":
            report = compare_findings(read_json(Path(args.labels)), read_json(Path(args.findings))); write_json(Path(args.output), report); _print({k: v for k, v in report.items() if k != "items"})
        return 0
    except (ConfigError, ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
        _print({"ok": False, "error": str(exc), "errors": getattr(exc, "errors", None)}); return 2


if __name__ == "__main__": raise SystemExit(main())
