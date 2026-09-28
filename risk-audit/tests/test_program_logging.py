"""验证审核日志只写入本次运行的日志文件。"""
from risk_audit.program_logging import audit_logging, build_log_reference


def test_audit_logging_writes_file_without_console_output(tmp_path, capsys):
    """tmp_path 为隔离目录，capsys 为终端捕获器；日志不应写入控制台。"""
    with audit_logging(tmp_path, 'file-only') as logger:
        logger.info('审核开始')
        logger.error('审核失败')

    captured = capsys.readouterr()
    log_text = (tmp_path / 'audit.log').read_text(encoding='utf-8')
    assert captured.out == ''
    assert captured.err == ''
    assert 'INFO [file-only]' in log_text and '审核开始' in log_text
    assert 'ERROR [file-only]' in log_text and '审核失败' in log_text


def test_startup_error_before_log_directory_uses_stderr(tmp_path, capsys):
    """tmp_path 为隔离目录，capsys 为终端捕获器；无日志目录时启动错误应写 stderr。"""
    from run_audit import main

    missing_soffice = tmp_path / 'missing-soffice'
    code = main(['--soffice', str(missing_soffice)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ''
    assert 'LibreOffice executable does not exist' in captured.err
    assert not list(tmp_path.rglob('audit.log'))


def test_desktop_log_reference_is_relative_to_output_directory(tmp_path):
    """验证桌面审核器日志使用输出目录相对路径。

    Args:
        tmp_path: pytest 提供的隔离测试目录。
    """
    output_root = tmp_path / '审核结果'
    runs_root = output_root / '.task'
    log_file = runs_root / 'report/audit.log'

    assert build_log_reference(log_file, output_root, runs_root) == '.task/report/audit.log'


def test_cli_log_reference_keeps_runs_directory_name(tmp_path):
    """验证本地 CLI 日志保留 runs 根目录名称。

    Args:
        tmp_path: pytest 提供的隔离测试目录。
    """
    output_root = tmp_path / 'outputs/审核结果'
    runs_root = tmp_path / 'runs'
    log_file = runs_root / '20260928-120000/audit.log'

    assert build_log_reference(log_file, output_root, runs_root) == 'runs/20260928-120000/audit.log'
