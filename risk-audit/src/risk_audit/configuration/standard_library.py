"""校验冻结标准库的业务身份、措施编号和公司性质数据。"""

from typing import Any

from risk_audit.util import measure_id_key


def validate_standard_library(library: Any, business_registry: dict) -> list[str]:
    """返回资源错误；library 为冻结附件，business_registry 为重庆业务身份表。"""
    errors = []
    if not isinstance(library, dict):
        return ["standard_library: object required"]
    companies, measures = library.get("companies"), library.get("measures")
    if not isinstance(companies, list) or not companies:
        return ["standard_library.companies: nonempty array required"]
    if not isinstance(measures, list) or not measures:
        return ["standard_library.measures: nonempty array required"]
    company_names = set()
    natures = set()
    for index, company in enumerate(companies):
        where = f"standard_library.companies[{index}]"
        if not isinstance(company, dict) or any(not isinstance(company.get(key), str) or not company[key].strip() for key in ("company_name", "unit_nature")):
            errors.append(where + ": company_name and unit_nature required")
            continue
        if company["company_name"] in company_names:
            errors.append(where + ": duplicate company_name")
        company_names.add(company["company_name"])
        natures.add(company["unit_nature"])
    business_codes = {business["business_id"]: business["business_code"] for business in business_registry.get("businesses", [])}
    codes = set()
    keys = set()
    for index, measure in enumerate(measures):
        where = f"standard_library.measures[{index}]"
        required = ("matrix_name", "matrix_type", "standard_code", "measure_id", "business_id", "business_code")
        if not isinstance(measure, dict) or any(not isinstance(measure.get(key), str) or not measure[key].strip() for key in required):
            errors.append(where + ": measure fields required")
            continue
        key = measure_id_key(measure["measure_id"])
        if not key or not all(isinstance(part, int) for part in key):
            errors.append(where + ".measure_id: numeric identifier required")
        identity = measure["business_id"], key
        if measure["standard_code"] in codes or identity in keys:
            errors.append(where + ": duplicate standard_code or business measure_id")
        codes.add(measure["standard_code"])
        keys.add(identity)
        if business_codes.get(measure["business_id"]) != measure["business_code"]:
            errors.append(where + ": unknown business identity")
        cells = measure.get("applicable_cells")
        if not isinstance(cells, dict) or any(nature not in natures or not isinstance(cell, str) or not cell.strip() for nature, cell in cells.items()):
            errors.append(where + ".applicable_cells: known unit nature and source cell required")
    return errors
