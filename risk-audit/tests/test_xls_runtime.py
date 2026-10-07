"""验证 LibreOffice 可执行文件和 profile 的任务级隔离。"""

from pathlib import Path
from types import SimpleNamespace


def test_convert_xls_uses_short_explicit_destination(monkeypatch, tmp_path: Path) -> None:
    """转换命令必须使用短目标名、配置的可执行文件和独立 profile。

    Args:
        monkeypatch: pytest 提供的补丁工具。
        tmp_path: pytest 提供的隔离目录。
    """
    from risk_audit.readers import xls

    soffice = tmp_path / "runtime/libreoffice/program/soffice.com"
    soffice.parent.mkdir(parents=True)
    soffice.write_bytes(b"exe")
    profile = tmp_path / "output/.task/libreoffice-profile"
    source = tmp_path / ("超长业务路径" * 20) / ("超长原文件名" * 12 + ".xls")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"xls")
    destination = tmp_path / "work/converted/0123456789abcdef0123456789abcdef.xlsx"
    captured = {}

    def fake_run(command, **kwargs):
        """记录真实命令边界并生成转换目标；参数对应 subprocess.run。"""
        captured["command"] = command
        captured["kwargs"] = kwargs
        converted_source = Path(command[-1])
        converted_source.with_suffix(".xlsx").write_bytes(b"xlsx")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(xls.subprocess, "run", fake_run)
    monkeypatch.setattr(xls, "restore_biff_formula_caches", lambda *args: 0)
    monkeypatch.setattr(xls, "compare_conversion", lambda *args: {"passed": True})
    monkeypatch.setattr(xls, "sys", SimpleNamespace(platform="win32"), raising=False)

    xls.configure_conversion_runtime(soffice, profile)
    converted, _report = xls.convert_xls(source, destination)

    assert converted == destination
    assert captured["command"][0] == str(soffice.resolve())
    assert Path(captured["command"][-1]).name == "0123456789abcdef0123456789abcdef.xls"
    assert Path(captured["command"][-1]).parent == destination.parent
    assert captured["command"][-3:-1] == ["--outdir", str(destination.parent)]
    assert f"-env:UserInstallation={profile.resolve().as_uri()}" in captured["command"]
    assert captured["kwargs"].get("shell") is None
    assert captured["kwargs"]["creationflags"] == 0x08000000
    settings = profile / "user/registrymodifications.xcu"
    assert "DisableMacrosExecution" in settings.read_text(encoding="utf-8")
    assert captured["kwargs"]["env"]["SAL_DISABLE_MACROS"] == "1"
    assert not destination.with_suffix(".xls").exists()
