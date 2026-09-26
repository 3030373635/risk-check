from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Entity:
    code: str | None
    name: str
    parent: str = ""
    primary_owner: str = ""
    active: str = ""
    source_row: int = 0


@dataclass
class FieldValue:
    raw: Any
    current: str
    coordinate: str
    formula: str | None = None
    cached: Any = None
    state: str = "value"
    deleted_spans: list[dict[str, Any]] = field(default_factory=list)
    deleted_unavailable: bool = False
    red_spans: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Record:
    record_type: str
    entity_code: str
    business_code: str
    variant_id: str
    file_path: str
    sheet: str
    row: int
    fields: dict[str, FieldValue]
    record_id: str
    person_keys: list[dict[str, Any]] = field(default_factory=list)
    business_id: str = ""

    def value(self, field_id: str) -> str:
        value = self.fields.get(field_id)
        return value.current if value else ""


@dataclass
class ParsedSheet:
    title: str
    sheet_type: str
    header_rows: list[int]
    columns: dict[str, int]
    audit_columns: dict[str, int]
    output_column: int
    first_data_row: int | None
    records: list[Record]
    business_header_paths: list[dict[str, Any]] = field(default_factory=list)
    region_bounds: tuple[int, int, int, int] | None = None
    region_audit_columns: dict[str, int] = field(default_factory=dict)


@dataclass
class FileRecord:
    source: Path
    relative_path: Path
    sha256: str
    true_format: str
    entity_code: str | None
    entity_evidence: list[str]
    entity_conflict: bool
    business_code: str | None
    variant_id: str
    material_type: str | None
    sheets: list[ParsedSheet] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    converted_from: str | None = None
    preservation: dict[str, Any] = field(default_factory=dict)
    business_id: str | None = None


@dataclass
class Finding:
    finding_key: str
    finding_id: str
    rule_id: str
    check_id: str
    display_code: str
    revision: int
    severity: str
    entity_code: str
    entity_name: str
    business_code: str
    variant_id: str
    file_path: str
    sheet: str
    row: int | None
    message: str
    evidence: dict[str, Any]
    location_policy: str
    business_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckStatus:
    rule_id: str
    check_id: str
    status: str
    reason: str = ""
    records_considered: int = 0
    findings: int = 0
    limitations: int = 0
