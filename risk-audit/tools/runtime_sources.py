"""读取、下载并安全解压锁定的便携运行时来源。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import tarfile
from typing import Any
from urllib.request import urlopen


@dataclass(frozen=True)
class RuntimeSource:
    """描述一个平台运行时来源。"""

    platform_id: str
    kind: str
    version: str
    url: str | None
    sha256: str | None
    size: int | None
    archive_root: str


def _source_from_dict(platform_id: str, name: str, value: Any) -> RuntimeSource:
    """解析单个来源；参数为平台、来源名和 JSON 值。"""
    if not isinstance(value, dict):
        raise ValueError(f"运行时来源必须是对象：{platform_id}/{name}")
    required = {"kind", "version", "url", "sha256", "size", "archive_root"}
    if set(value) != required:
        raise ValueError(f"运行时来源字段不完整：{platform_id}/{name}")
    for field_name in ("kind", "version", "archive_root"):
        if not isinstance(value[field_name], str) or not value[field_name]:
            raise ValueError(f"运行时来源字段无效：{platform_id}/{name}/{field_name}")
    if value["url"] is not None and not isinstance(value["url"], str):
        raise ValueError(f"运行时来源 URL 无效：{platform_id}/{name}")
    if value["sha256"] is not None and (
        not isinstance(value["sha256"], str) or len(value["sha256"]) != 64
    ):
        raise ValueError(f"运行时来源 SHA-256 无效：{platform_id}/{name}")
    if value["size"] is not None and (
        type(value["size"]) is not int or value["size"] <= 0
    ):
        raise ValueError(f"运行时来源大小无效：{platform_id}/{name}")
    return RuntimeSource(
        platform_id,
        value["kind"],
        value["version"],
        value["url"],
        value["sha256"],
        value["size"],
        value["archive_root"],
    )


def load_runtime_sources(path: Path) -> dict[str, dict[str, RuntimeSource]]:
    """读取运行时来源清单；path 为 JSON 文件，返回平台索引。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"运行时来源清单无法读取：{path}；{error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise ValueError(f"运行时来源清单版本不受支持：{path}")
    platforms = payload.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise ValueError(f"运行时来源清单缺少平台：{path}")
    result: dict[str, dict[str, RuntimeSource]] = {}
    for platform_id, raw_sources in platforms.items():
        if not isinstance(platform_id, str) or not isinstance(raw_sources, dict):
            raise ValueError(f"运行时平台条目无效：{platform_id!r}")
        if set(raw_sources) != {"python", "libreoffice"}:
            raise ValueError(f"运行时平台来源不完整：{platform_id}")
        result[platform_id] = {
            name: _source_from_dict(platform_id, name, value)
            for name, value in raw_sources.items()
        }
    return result


def download_verified(source: RuntimeSource, destination: Path) -> Path:
    """下载并校验来源；参数为锁定来源和目标文件，返回目标路径。"""
    if destination.exists():
        raise FileExistsError(f"下载目标已存在，拒绝覆盖：{destination}")
    if source.url is None or source.sha256 is None:
        raise ValueError(f"运行时来源不可下载：{source.platform_id}/{source.kind}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with urlopen(source.url) as response, temporary.open("xb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if source.size is not None and size != source.size:
            raise ValueError(f"下载文件大小不一致：{size} != {source.size}")
        if digest.hexdigest() != source.sha256:
            raise ValueError("下载文件 SHA-256 不一致")
        os.replace(temporary, destination)
        return destination
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _normalized_archive_path(value: str) -> str | None:
    """规范归档 POSIX 路径；value 为成员或链接路径，无效时返回 None。"""
    pure_path = PurePosixPath(value)
    if pure_path.is_absolute() or not pure_path.parts or ".." in pure_path.parts:
        return None
    normalized = posixpath.normpath(pure_path.as_posix())
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def _validate_tar_members(members: list[tarfile.TarInfo]) -> None:
    """验证 tar 成员不会越界；members 为完整成员列表。"""
    names: set[str] = set()
    symlinks: set[str] = set()
    for member in members:
        normalized = _normalized_archive_path(member.name)
        if normalized is None or normalized in names:
            raise ValueError(f"归档包含不安全路径：{member.name}")
        names.add(normalized)
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError(f"归档包含不安全成员类型：{member.name}")
        if member.issym():
            target = _normalized_archive_path(
                posixpath.join(posixpath.dirname(normalized), member.linkname)
            )
            if target is None:
                raise ValueError(f"归档包含不安全符号链接：{member.name}")
            symlinks.add(normalized)
        if member.islnk() and _normalized_archive_path(member.linkname) is None:
            raise ValueError(f"归档包含不安全硬链接：{member.name}")
    for name in names:
        parents = PurePosixPath(name).parents
        if any(parent.as_posix() in symlinks for parent in parents):
            raise ValueError(f"归档成员穿过符号链接目录：{name}")


def extract_verified_archive(
    archive: Path,
    destination: Path,
    *,
    archive_root: str,
) -> Path:
    """安全解压 tar；参数为归档、目标目录和预期顶层目录，返回顶层路径。"""
    if destination.exists():
        raise FileExistsError(f"解压目标已存在，拒绝覆盖：{destination}")
    expected_root_name = _normalized_archive_path(archive_root)
    if expected_root_name is None or "/" in expected_root_name:
        raise ValueError(f"归档根无效：{archive_root}")
    destination.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            _validate_tar_members(members)
            handle.extractall(destination)
        resolved_destination = destination.resolve()
        for path in destination.rglob("*"):
            resolved = path.resolve(strict=False)
            if resolved != resolved_destination and resolved_destination not in resolved.parents:
                raise ValueError(f"归档包含不安全解压结果：{path}")
        expected_root = destination / expected_root_name
        if not expected_root.is_dir():
            raise ValueError(f"归档缺少预期根目录：{expected_root_name}")
        return expected_root
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
