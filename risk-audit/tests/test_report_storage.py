"""验证审核报告不会因重复保存明细而异常膨胀。"""

import json

from risk_audit import runner, util
from risk_audit.issue_routing import (
    build_internal_diagnostics,
    write_internal_diagnostics,
)
from risk_audit.runner import _write_audit_result


def test_write_compact_json_uses_compact_encoding(tmp_path):
    """验证内部 JSON 使用紧凑编码。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    output = tmp_path / "compact.json"
    value = [
        {"message": "重复内容", "values": [1, 2, 3]}
        for _ in range(100)
    ]

    util.write_compact_json(output, value)

    content = output.read_text(encoding="utf-8")
    assert json.loads(content) == value
    # 同一批结构化记录不应因缩进和换行产生额外的成倍空间开销。
    assert len(content.encode("utf-8")) < 6_500


def test_audit_progress_only_writes_summary(tmp_path):
    """验证业务和主体进度目录只保存汇总结果。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    output = tmp_path / "entities/0001/businesses/0001"
    result = {
        "entity_code": "E001",
        "business_code": "06",
        "findings": 100,
        "limitations": 200,
    }

    _write_audit_result(output, result)

    assert json.loads((output / "result.json").read_text(encoding="utf-8")) == result
    assert [path.name for path in output.iterdir()] == ["result.json"]


def test_audit_progress_details_append_to_single_root_log(tmp_path):
    """验证已完成业务的明细增量写入唯一根目录日志。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    output = tmp_path / "progress_details.jsonl"
    first = {"findings": [{"finding_key": "f1"}], "limitations": [{"check_id": "c1"}]}
    second = {"findings": [{"finding_key": "f2"}], "limitations": []}

    runner._write_audit_progress_details(output, first)
    runner._write_audit_progress_details(output, second)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [first, second]
    assert all("\n" not in line for line in lines)


def test_internal_diagnostics_reference_canonical_limitations(tmp_path):
    """验证内部诊断只保存分组信息，不复制完整限制项。

    Args:
        tmp_path: pytest 提供的隔离目录。
    """
    limitations = [
        {
            "check_id": "measure_exists",
            "entity_code": "E001",
            "business_id": "06",
            "business_code": "06",
            "variant_id": "default",
            "file_path": "E001/06矩阵.xlsx",
            "sheet": "风控矩阵",
            "row": row,
            "evidence": {
                "cause_type": "matrix_source_unconfirmed",
                "issue_type": "reference_field_unavailable",
                "unavailable_reason": "未找到矩阵来源",
            },
        }
        for row in (2, 2, 3)
    ]
    scopes = {
        "E001": {
            "businesses": [
                {"business_id": "06", "business_code": "06", "variant_id": "default"}
            ]
        }
    }

    groups = build_internal_diagnostics(limitations, [], scopes)
    summary = write_internal_diagnostics(tmp_path, groups)

    assert len(groups) == 1
    group = groups[0]
    assert group["detail_count"] == 3
    assert group["affected_location_count"] == 2
    assert group["example_location"]["row"] == 2
    assert group["example_reason"] == "未找到矩阵来源"
    assert "details" not in group
    assert all(
        item["evidence"]["root_cause_id"] == group["root_cause_id"]
        for item in limitations
    )
    saved_groups = json.loads(
        (tmp_path / "internal_diagnostics.json").read_text(encoding="utf-8")
    )
    assert "details" not in saved_groups[0]
    assert summary["in_scope_unfinished_checks"] == 3
