"""验证 LibreOffice 可执行文件和 profile 的任务级隔离。"""

from pathlib import Path
from types import SimpleNamespace


def test_convert_xls_uses_explicit_soffice_and_profile(monkeypatch, tmp_path: Path) -> None:
    """转换命令必须使用 Worker 配置的可执行文件和独立 profile。"""
    from risk_audit.readers import xls

    soffice = tmp_path / "runtime/libreoffice/program/soffice.exe"
    soffice.parent.mkdir(parents=True)
    soffice.write_bytes(b"exe")
    profile = tmp_path / "output/.task/libreoffice-profile"
    source = tmp_path / "source.xls"
    source.write_bytes(b"xls")
    destination = tmp_path / "converted"
    captured = {}

    def fake_run(command, **kwargs):
        """记录真实命令边界并生成转换目标；参数对应 subprocess.run。"""
        captured["command"] = command
        captured["kwargs"] = kwargs
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "source.xlsx").write_bytes(b"xlsx")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(xls.subprocess, "run", fake_run)
    monkeypatch.setattr(xls, "restore_biff_formula_caches", lambda *args: 0)
    monkeypatch.setattr(xls, "compare_conversion", lambda *args: {"passed": True})

    xls.configure_conversion_runtime(soffice, profile)
    xls.convert_xls(source, destination)

    assert captured["command"][0] == str(soffice.resolve())
    assert f"-env:UserInstallation={profile.resolve().as_uri()}" in captured["command"]
    assert captured["kwargs"].get("shell") is None
    settings = profile / "user/registrymodifications.xcu"
    assert "DisableMacrosExecution" in settings.read_text(encoding="utf-8")
    assert captured["kwargs"]["env"]["SAL_DISABLE_MACROS"] == "1"
