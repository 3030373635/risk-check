"""验证审核日志只写入本次运行的日志文件。"""
from risk_audit.program_logging import audit_logging


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
