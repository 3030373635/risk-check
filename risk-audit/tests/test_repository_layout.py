"""验证仓库顶层目录职责和英文命名。"""

from pathlib import Path

from risk_audit.configuration.loader import load_pack
from risk_audit.util import sha256_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_repository_uses_english_project_and_data_directories() -> None:
    """项目、模板和参考数据必须分开存放；无参数。"""
    assert (REPOSITORY_ROOT / "risk-audit/pyproject.toml").is_file()
    assert (REPOSITORY_ROOT / "templates").is_dir()
    assert (REPOSITORY_ROOT / "reference-data/会计主体清单20260907.xlsx").is_file()
    assert not (REPOSITORY_ROOT / "审核").exists()
    assert not (REPOSITORY_ROOT / "审核器").exists()


def test_all_released_rulepacks_have_current_content_hash() -> None:
    """所有已发布规则包必须匹配当前资源哈希；无参数。"""
    releases_root = REPOSITORY_ROOT / "risk-audit/rulepacks/releases"
    mismatched_versions: list[str] = []
    for release_path in sorted(path for path in releases_root.iterdir() if path.is_dir()):
        pack = load_pack(release_path)
        # manifest 不参与自身 content_hash 计算，避免循环依赖。
        resource_hashes = {
            path: digest
            for path, digest in pack["_resource_hashes"].items()
            if path != "manifest.json"
        }
        if pack["manifest"].get("content_hash") != sha256_json(resource_hashes):
            mismatched_versions.append(release_path.name)

    assert mismatched_versions == []
