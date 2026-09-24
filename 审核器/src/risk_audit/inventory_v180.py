"""确认稿 V1 的报送材料扫描与说明材料识别。"""
from pathlib import Path
import subprocess
import tempfile

from risk_audit.business_identity import BusinessIdentityError, identify_business as identify_business_v2
from risk_audit.entities import identify_material_entity
from risk_audit.inventory import identify_business, is_auxiliary, probe_material, true_format
from risk_audit.models import FileRecord
from risk_audit.util import sha256_file

EXPLANATION_WORDS = ('说明', '情况说明', '合并说明', '主体说明', '申请')


def explanation_text(path):
    """读取说明标题及开头；path 为只读的 DOC、DOCX 或 PDF 文件路径。"""
    suffix = path.suffix.lower()
    if suffix == '.docx':
        from docx import Document
        # 修订删除内容不作为当前材料标题，python-docx 默认不拼接删除节点。
        return '\n'.join(p.text for p in Document(path).paragraphs[:30])
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        title = str((reader.metadata or {}).get('/Title', ''))
        return title + '\n' + (reader.pages[0].extract_text() or '' if reader.pages else '')
    if suffix == '.doc':
        from risk_audit.readers import xls
        if not xls.SOFFICE.is_file():
            raise ValueError('读取 DOC 说明标题需要 LibreOffice')
        with tempfile.TemporaryDirectory(prefix='risk-audit-doc-') as directory:
            root = Path(directory)
            profile = root / 'profile'
            xls.prepare_conversion_profile(profile)
            # 与 XLS 一样使用隔离且禁用宏的 profile，仅转换临时副本。
            subprocess.run([str(xls.SOFFICE), f'-env:UserInstallation={profile.as_uri()}', '--headless', '--convert-to', 'docx', '--outdir', str(root), str(path)], check=True, capture_output=True, timeout=60)
            return explanation_text(root / (path.stem + '.docx'))
    return ''


def scan_package_v180(root, entities, aliases, business_registry=None):
    """扫描确认稿材料；root 为输入目录，entities/aliases 为主体配置，business_registry 为业务目录。"""
    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError(f'待审核材料目录不存在：{base}')
    records = []
    for path in sorted(base.rglob('*')):
        suffix = path.suffix.lower()
        # 点开头文件和 Office 锁文件属于系统或编辑临时文件，不进入报送材料清单。
        if not path.is_file() or path.name.startswith(('.', '~$')) or is_auxiliary(path):
            continue
        # ET 只作为文件入口；后续按真实签名分流为 OOXML 或 BIFF，未知格式保留错误。
        if suffix not in {'.xlsx', '.xls', '.et', '.doc', '.docx', '.pdf'}:
            continue
        relative = path.relative_to(base)
        text = ''
        read_error = ''
        if suffix in {'.doc', '.docx', '.pdf'}:
            try:
                text = explanation_text(path)
            except Exception as error:
                read_error = f'说明标题无法读取：{error}'
            if not any(word in path.name or word in text for word in EXPLANATION_WORDS):
                continue
            material = 'explanation'
        else:
            material = 'three_lists' if '三清单' in path.name else 'matrix' if '矩阵' in path.name else probe_material(path)
            if not material and any(word in path.stem for word in ('岗位内控责任清单', '不相容岗位清单', '系统控制规则清单')):
                material = 'three_lists'
            if not material:
                # XLS、晚表头或独立区域须交给实际业务读取器；扫描不能凭文件名漏件。
                material = 'unclassified'
        business_id = None
        business_error = ''
        if material == 'explanation':
            business, variant = None, 'default'
        elif business_registry and business_registry.get('schema_version') == '2.0':
            try:
                business_identity = identify_business_v2(relative, business_registry)
                business_id = business_identity.business_id
                business = business_identity.business_code
                variant = business_identity.variant_id
            except BusinessIdentityError as error:
                business, variant, business_error = None, 'default', str(error)
        else:
            # 历史发布包仅用于复核旧运行，当前发布包必须提供 v2 模板业务目录。
            business, variant = identify_business(relative)
            business_id = business
        identity = [base.name, *relative.parts]
        if text:
            identity.extend(text.splitlines()[:30])
        code, evidence, conflict = identify_material_entity(
            path, base, entities, aliases, identity, business_registry, business_id, business,
        )
        with path.open('rb') as stream:
            signature = stream.read(8)
        file_format = 'pdf' if signature.startswith(b'%PDF-') and suffix == '.pdf' else true_format(path)
        errors = []
        if file_format == 'unknown':
            errors.append('文件签名不是受支持的 Excel、Word 或 PDF 格式')
        if read_error:
            errors.append(read_error)
        if material != 'explanation' and business is None:
            errors.append(business_error or '文件路径中的业务编号不唯一或无法识别')
        if code is None:
            errors.append('主体证据冲突' if conflict else '无法确定会计主体')
        record = FileRecord(path, relative, sha256_file(path), file_format, code, evidence, conflict,
                            business, variant, material, parse_errors=errors, business_id=business_id)
        if (material != 'explanation' and business_registry
                and business_registry.get('schema_version') == '2.0' and business_id):
            record.preservation['business_name'] = business_identity.business_name
        records.append(record)
    return records
