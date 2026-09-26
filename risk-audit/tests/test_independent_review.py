"""主任务维护的独立验收入口，具体用例位于 .work/review。

若失败，请先读 risk-audit/独立审查待办.md 并修正实现。
不要修改桥接器或放宽独立测试。真正的配置契约变化可交主任务校核fixture。
"""
from pathlib import Path
import subprocess
import sys


def test_independent_acceptance_suite():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-s', str(root / '.work/review'),
         '-p', 'test_*.py', '-v'], cwd=root, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        '请先阅读 risk-audit/独立审查待办.md，修正实现后重新运行。\n'
        + result.stdout + result.stderr
    )
