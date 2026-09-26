"""发布或复验支持重复业务序号的 v1.9.19 规则包。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "risk-audit/src"))

from risk_audit.business_identity import validate_business_registry
from risk_audit.checks.registry import build_registry
from risk_audit.configuration.publisher import RulePackStore
from risk_audit.util import read_json, sha256_file


DRAFT_NAME = "template-business-identity-1.9.19"
TARGET_VERSION = "1.9.19"


def verify_templates(registry: dict) -> None:
    """核验模板目录；registry 为 v2 基准配置，错误时立即终止发布。"""
    errors = validate_business_registry(registry)
    if errors:
        raise ValueError("模板业务配置无效：" + "；".join(errors))
    for business in registry["businesses"]:
        for variant in business["variants"]:
            template = variant["template"]
            path = ROOT / template["path"]
            if not path.is_file():
                raise FileNotFoundError(f"模板不存在：{path}")
            actual = sha256_file(path)
            if actual != template["sha256"]:
                raise ValueError(f"模板哈希变化：{path}")


def main() -> None:
    """发布或复验 1.9.19；--verify-only 仅执行只读校验。"""
    parser = argparse.ArgumentParser(description="发布或复验多批次模板业务目录")
    parser.add_argument("--verify-only", action="store_true", help="只验证发布包和模板，不修改激活版本")
    args = parser.parse_args()
    store = RulePackStore(ROOT / "risk-audit/rulepacks", build_registry())
    draft = store.root / "drafts" / DRAFT_NAME
    release = store.root / "releases" / TARGET_VERSION
    if release.is_dir():
        pack = store.validate(release)
        verified = True
    else:
        if args.verify_only:
            raise FileNotFoundError(f"发布包不存在：{release}")
        if not draft.is_dir():
            raise FileNotFoundError(f"规则草稿不存在：{draft}")
        pack = store.validate(draft)
        # 创建不可变发布目录前先检查外部模板，避免失败后留下无法覆盖的半成品发布。
        verify_templates(pack["baseline_registry"])
        release = store.publish(DRAFT_NAME, TARGET_VERSION)
        pack = store.validate(release)
        verified = False
    verify_templates(pack["baseline_registry"])
    if not args.verify_only:
        store.activate(TARGET_VERSION)
    active = read_json(store.root / "active.json")
    print(json.dumps({
        "release": str(release),
        "activated": not args.verify_only,
        "verified": verified,
        "active_version": active.get("version"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
