"""从目标 Python 运行时收集发行版和 Python 许可证。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Sequence


class LicenseCollectionError(RuntimeError):
    """表示目标运行时或某个发行版缺少可交付许可证。"""


TARGET_LICENSE_QUERY = r'''
import importlib.metadata
import json
from pathlib import Path
import sys
import sysconfig

names = json.loads(sys.argv[1])
items = []
for name in names:
    distribution = importlib.metadata.distribution(name)
    licenses = []
    for item in distribution.files or []:
        relative = str(item).replace("\\", "/")
        parts = relative.split("/")
        file_name = parts[-1].lower()
        parents = {part.lower() for part in parts[:-1]}
        if (
            "licenses" in parents
            or file_name.startswith("license")
            or file_name.startswith("licence")
            or file_name.startswith("copying")
            or file_name.startswith("notice")
        ):
            licenses.append(str(Path(distribution.locate_file(item)).resolve()))
    items.append({"name": name, "licenses": licenses})

python_root = Path(sys.base_prefix)
# python-build-standalone 的精简包把许可证放在标准库目录。
standard_library = Path(sysconfig.get_paths()["stdlib"])
python_candidates = [
    python_root / "LICENSE.txt",
    python_root / "LICENSE",
    standard_library / "LICENSE.txt",
    standard_library / "LICENSE",
]
python_license = next((str(path.resolve()) for path in python_candidates if path.is_file()), None)
print(json.dumps({"python_license": python_license, "distributions": items}))
'''


def _safe_name(value: str) -> str:
    """生成安全目录名；value 为 Python 发行版名称。"""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "package"


def _load_target_index(python_executable: Path, distribution_names: Sequence[str]) -> dict[str, Any]:
    """用目标 Python 读取许可证索引；参数为 Python 和发行名。"""
    try:
        result = subprocess.run(
            [
                str(python_executable),
                "-I",
                "-c",
                TARGET_LICENSE_QUERY,
                json.dumps(list(distribution_names)),
            ],
            check=True,
            shell=False,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise LicenseCollectionError(f"目标 Python 许可证索引无法读取：{error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("distributions"), list):
        raise LicenseCollectionError("目标 Python 许可证索引格式无效")
    return payload


def collect_distribution_licenses(
    python_executable: Path,
    distribution_names: Sequence[str],
    destination: Path,
) -> list[Path]:
    """复制目标环境许可证；参数为 Python、发行名和目标目录。"""
    unique_names = list(dict.fromkeys(distribution_names))
    payload = _load_target_index(python_executable, unique_names)
    indexed = payload["distributions"]
    if len(indexed) != len(unique_names):
        raise LicenseCollectionError("目标 Python 返回的发行版数量不一致")

    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for expected_name, item in zip(unique_names, indexed, strict=True):
        if not isinstance(item, dict) or item.get("name") != expected_name:
            raise LicenseCollectionError(f"目标 Python 发行版索引错位：{expected_name}")
        candidates = item.get("licenses")
        if not isinstance(candidates, list) or not candidates:
            raise LicenseCollectionError(f"依赖未携带许可证：{expected_name}")
        package_root = destination / _safe_name(expected_name)
        for index, raw_path in enumerate(candidates, start=1):
            source = Path(raw_path) if isinstance(raw_path, str) else Path()
            if not source.is_file():
                raise LicenseCollectionError(f"许可证文件缺失：{expected_name}；{source}")
            suffix = source.suffix or ".txt"
            target = package_root / f"{index:02d}-{source.stem}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target)

    raw_python_license = payload.get("python_license")
    python_license = Path(raw_python_license) if isinstance(raw_python_license, str) else Path()
    if not python_license.is_file():
        raise LicenseCollectionError(f"Python 许可证缺失：{raw_python_license}")
    python_target = destination / "Python.txt"
    shutil.copy2(python_license, python_target)
    copied.append(python_target)
    return copied
