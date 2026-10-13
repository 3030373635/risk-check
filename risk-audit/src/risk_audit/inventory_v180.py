"""确认稿 V1 的报送材料扫描与说明材料识别。"""
from pathlib import Path
import subprocess
import tempfile

from risk_audit.business_identity import (
    BusinessIdentityError,
    identify_business as identify_business_v2,
    unmatched_business_id,
    workbook_material_evidence,
)
from risk_audit.entities import identify_material_entity
from risk_audit.inventory import classify_material, identify_business, is_auxiliary, true_format
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


def scan_package_v180(
    root,
    entities,
    aliases,
    business_registry=None,
    naming_keywords=None,
    use_workbook_evidence=False,
):
    """扫描确认稿材料。

    Args:
        root: 待扫描的输入目录。
        entities: 会计主体配置。
        aliases: 会计主体别名配置。
        business_registry: 模板业务目录。
        naming_keywords: 当前地区规则包的材料名称关键字。
        use_workbook_evidence: 文件名未命中时，是否使用工作簿标题匹配。
    """
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
        matched_from_workbook = False
        if suffix in {'.doc', '.docx', '.pdf'}:
            try:
                text = explanation_text(path)
            except Exception as error:
                read_error = f'说明标题无法读取：{error}'
            if not any(word in path.name or word in text for word in EXPLANATION_WORDS):
                continue
            material = 'explanation'
        else:
            material = classify_material(path, naming_keywords=naming_keywords)
        with path.open('rb') as stream:
            signature = stream.read(8)
        file_format = 'pdf' if signature.startswith(b'%PDF-') and suffix == '.pdf' else true_format(path)
        if not material and use_workbook_evidence and file_format in {'xlsx', 'xls'}:
            try:
                material_evidence = workbook_material_evidence(path, file_format)
            except Exception:
                material_evidence = []
            material = classify_material(
                path,
                naming_keywords=naming_keywords,
                workbook_evidence=material_evidence,
            )
            matched_from_workbook = material is not None
        if not material:
            continue
        business_id = None
        business_directory = None
        business_error = ''
        template_match = {'status': 'not_applicable', 'reason': ''}
        if material == 'explanation':
            business, variant = None, 'default'
        elif business_registry and business_registry.get('schema_version') == '2.0':
            try:
                business_identity = identify_business_v2(
                    relative,
                    business_registry,
                    base.name,
                )
                business_id = business_identity.business_id
                business = business_identity.business_code
                business_directory = business_identity.business_directory
                variant = business_identity.variant_id
                template_match = {
                    'status': 'matched',
                    'reason': '',
                    'business_id': business_id,
                    'variant_id': variant,
                    'matched_alias': business_identity.matched_alias,
                }
            except BusinessIdentityError as error:
                business_error = str(error)
                business_id = unmatched_business_id(relative)
                business, variant = None, 'default'
                template_match = {
                    'status': 'ambiguous' if '冲突' in business_error else 'unmatched',
                    'reason': business_error,
                    'business_id': business_id,
                }
        else:
            # 历史发布包仅用于复核旧运行，当前发布包必须提供 v2 模板业务目录。
            business, variant = identify_business(relative)
            business_id = business
        identity = [base.name, *relative.parts]
        if text:
            identity.extend(text.splitlines()[:30])
        code, evidence, conflict = identify_material_entity(
            path, base, entities, aliases, identity, business_registry, business_id, business,
            business_directory,
        )
        errors = []
        if file_format == 'unknown':
            errors.append('文件签名不是受支持的 Excel、Word 或 PDF 格式')
        if read_error:
            errors.append(read_error)
        if code is None:
            errors.append('主体证据冲突' if conflict else '无法确定会计主体')
        record = FileRecord(path, relative, sha256_file(path), file_format, code, evidence, conflict,
                            business, variant, material, parse_errors=errors, business_id=business_id,
                            business_directory=business_directory)
        if matched_from_workbook:
            # 命名检查复用扫描阶段的标题证据，避免对已确认类型的重庆材料重复报错。
            record.preservation['material_match'] = {
                'source': 'workbook',
                'material_type': material,
            }
        record.preservation['template_match'] = template_match
        if (material != 'explanation' and business_registry
                and business_registry.get('schema_version') == '2.0'
                and template_match['status'] == 'matched'):
            # 展示名始终保留报送业务目录，标准名仅用于追溯规则映射。
            record.preservation['business_name'] = business_directory.name or base.name
            record.preservation['registry_business_name'] = business_identity.business_name
        records.append(record)
    return records
