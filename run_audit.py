"""交付启动入口：优先加载包内 v1.9.19 源码并定位转换环境。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
# 解压包的源码是当前版本；直接运行时避免仍调用环境中安装的旧 wheel。
sys.path.insert(0, str(ROOT / '审核器/src'))

def configure_soffice(explicit=None):
    """定位 LibreOffice；explicit 为可选的可执行文件绝对路径。"""
    from risk_audit.readers import xls
    setting = explicit or os.environ.get('RISK_AUDIT_SOFFICE')
    if setting:
        path = Path(setting).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f'LibreOffice executable does not exist: {path}')
        xls.SOFFICE = path
        return path
    candidates = [shutil.which('soffice'), shutil.which('libreoffice'),
                  '/Applications/LibreOffice.app/Contents/MacOS/soffice']
    for key in ['PROGRAMFILES', 'PROGRAMFILES(X86)']:
        if os.environ.get(key):
            candidates.append(str(Path(os.environ[key]) / 'LibreOffice/program/soffice.exe'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            xls.SOFFICE = Path(candidate).resolve()
            return xls.SOFFICE
    # Preserve the existing workstation path if this package runs on its host.
    return xls.SOFFICE if xls.SOFFICE.is_file() else None

def main(argv=None):
    """启动审核工具；argv 为可选的命令行参数列表，默认读取系统参数。"""
    opts = argparse.ArgumentParser(add_help=False)
    opts.add_argument('--soffice')
    opts.add_argument('--doctor', action='store_true')
    args, rest = opts.parse_known_args(argv)
    try:
        import risk_audit
        if risk_audit.__version__ != '1.9.19':
            raise ValueError('当前启动入口要求 risk_audit 1.9.19，请使用本包源码或安装新版 wheel。')
        office = configure_soffice(args.soffice)
        os.chdir(ROOT)
        if args.doctor:
            from risk_audit.configuration.publisher import RulePackStore
            from risk_audit.checks.registry import build_registry
            from risk_audit.util import sha256_file
            store = RulePackStore(ROOT / '审核器/rulepacks', build_registry())
            pack = store.validate(store.active_path())
            baseline_registry = pack['baseline_registry']
            # 1.9.19 的模板固定在业务及变体下；旧发布包仅保留诊断读取能力。
            baseline_entries = (
                [variant['template']
                 for business in baseline_registry.get('businesses', [])
                 for variant in business.get('variants', [])]
                if baseline_registry.get('schema_version') == '2.0'
                else baseline_registry.get('entries', [])
            )
            missing = [x['path'] for x in baseline_entries
                       if not (ROOT / x['path']).is_file() or sha256_file(ROOT / x['path']) != x['sha256']]
            result = {'version': risk_audit.__version__, 'rulepack': pack['manifest']['version'],
                      'baseline_errors': missing, 'soffice': str(office) if office else None,
                      'embedding_enabled': pack['semantic_config']['enabled'],
                      'entity_list_present': (ROOT / '审核/会计主体清单20260907.xlsx').is_file(),
                      'note': 'Path discovery is not a conversion compatibility test.'}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if office and not missing and result['entity_list_present'] else 2
        from risk_audit.cli import main as cli_main
        return cli_main(rest)
    except (ImportError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
