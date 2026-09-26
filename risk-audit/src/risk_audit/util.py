from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


MEASURE_ID_PATTERN = re.compile(
    # 连续横线后允许 @、#、下划线等非文字脏字符，但不跨过业务名文字寻找数字。
    r"[-—－]+[\W_]*(?P<process>\d+)(?:[.．、])?[^-—－]*[-—－]+控制措施(?P<measure>\d+)"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).strip()


def measure_id_key(value: Any) -> tuple[int, ...] | tuple[str, str]:
    """生成控制措施编号比较键。

    参数 value 为原始控制措施编号。函数优先按结构化正则提取控制点和措施序号，
    并跳过横线后的非文字脏字符；未命中结构时统一提取原文中的所有数字。
    所有数字转为整数消除前导零，完全没有数字时才保留规范化原文。
    """
    normalized = norm_text(value)
    matched = MEASURE_ID_PATTERN.search(normalized)
    if matched:
        return int(matched["process"]), int(matched["measure"])
    numbers = tuple(int(number) for number in re.findall(r"\d+", normalized))
    return numbers or ("unparsed", normalized)


def display_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[ \t\r\f\v]+", " ", str(value)).strip()


def natural_key(value: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", norm_text(value)))


def deep_diff(a: Any, b: Any, path: str = "$") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if type(a) is not type(b):
        return [{"path": path, "before": a, "after": b}]
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append({"path": f"{path}.{key}", "before": None, "after": b[key]})
            elif key not in b:
                out.append({"path": f"{path}.{key}", "before": a[key], "after": None})
            else:
                out.extend(deep_diff(a[key], b[key], f"{path}.{key}"))
    elif isinstance(a, list):
        if a != b:
            out.append({"path": path, "before": a, "after": b})
    elif a != b:
        out.append({"path": path, "before": a, "after": b})
    return out
