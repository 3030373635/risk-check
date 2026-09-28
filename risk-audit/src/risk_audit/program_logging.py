"""为每次审核配置独立的 UTF-8 日志文件。"""
from contextlib import contextmanager
import logging
from pathlib import Path
from typing import Iterator


def build_log_reference(log_file: Path, output_root: Path, runs_root: Path) -> str:
    """生成可移动的审核日志展示路径。

    Args:
        log_file: 本次审核日志文件。
        output_root: 本次审核输出目录。
        runs_root: 当前执行器的运行记录根目录。

    Returns:
        桌面端返回相对输出目录的路径；CLI 返回带运行根目录名的路径。
    """
    resolved_log = log_file.resolve(strict=False)
    resolved_output = output_root.resolve(strict=False)
    resolved_runs = runs_root.resolve(strict=False)

    try:
        # 桌面审核器把运行记录保存在输出目录内，相对路径可随输出整体移动。
        return resolved_log.relative_to(resolved_output).as_posix()
    except ValueError:
        pass

    try:
        # CLI 的运行记录与输出目录分离，保留 runs 等根目录名便于定位。
        relative_log = resolved_log.relative_to(resolved_runs)
        return (Path(resolved_runs.name) / relative_log).as_posix()
    except ValueError:
        return resolved_log.name


class RunIdFilter(logging.Filter):
    """为日志补充运行标识；run_id 为当前审核批次标识。"""

    def __init__(self, run_id: str) -> None:
        """保存运行标识；run_id 为当前审核批次标识。"""
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        """写入运行标识并允许记录输出；record 为待格式化日志。"""
        record.run_id = self.run_id
        return True


@contextmanager
def audit_logging(run_dir: Path, run_id: str) -> Iterator[logging.Logger]:
    """配置并释放审核日志；run_dir 为本次运行目录，run_id 为日志中的运行标识。"""
    logger = logging.getLogger('risk_audit')
    previous_handlers = list(logger.handlers)
    previous_level, previous_propagate = logger.level, logger.propagate
    file_handler = logging.FileHandler(run_dir / 'audit.log', encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(run_id)s] %(name)s: %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.addFilter(RunIdFilter(run_id))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    # 只配置审核器自身的文件日志，避免污染命令行 JSON 输出。
    logger.handlers = [file_handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger
    except BaseException:
        logger.exception('审核异常终止，详情见 audit.log')
        raise
    finally:
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        file_handler.close()
