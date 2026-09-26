from __future__ import annotations

import re
from typing import Any, Iterable

from risk_audit.util import norm_text


QUALITY_NAMES = (
    "准确性", "一致性", "真实性", "完整性", "合规性", "及时性", "规范性", "有效性", "安全性",
)
QUALITY_ALTERNATION = "|".join(QUALITY_NAMES)
QUALITY_TAIL = re.compile(
    rf"(?:的)?(?:{QUALITY_ALTERNATION})(?:[、，,]|(?:及|和|与))?(?:(?:{QUALITY_ALTERNATION})(?:[、，,]|(?:及|和|与))?)*$"
)
DESCRIBED_QUALITY_TAIL = re.compile(
    rf"的[^!?！？。；;]{{1,40}}(?:{QUALITY_ALTERNATION})(?:(?:[、，,]|及|和|与)(?:{QUALITY_ALTERNATION}))*$"
)
ACTION_QUALITY_TAILS = tuple(
    action + quality
    for action in ("签订", "编制", "填写", "登记", "录入", "报送", "审批", "审核", "核对", "执行")
    for quality in QUALITY_NAMES
)
GENERIC_OBJECTS = {
    norm_text(x)
    for x in (
        "本岗位工作", "本职工作", "岗位工作", "工作", "相关工作", "职责范围内工作",
        "涉及财务管理的部分", "财务管理的部分", "相关事项", "有关事项", "某事项", "移交",
    )
}
BEHAVIOUR_PATTERNS = (
    # These complete predicates describe a control objective, not a document.
    # Full matching preserves real names such as “账卡一致核对表”.
    re.compile(r"^(?:确保|保证|保持)?账(?:卡物?|实|账|表)(?:相符|一致)$"),
    re.compile(r"^(?:涉及)?[一-鿿]{1,30}管理的部分$"),
    re.compile(r"^(?:本|相关|有关)?[一-鿿]{0,16}(?:工作|事项|业务)$"),
)
DOCUMENT_ENDINGS = (
    "申请单", "审批单", "计划单", "清单", "明细表", "台账", "卡片", "记录", "报告", "协议", "合同", "凭证", "档案", "方案", "表单", "文件", "资料", "单", "表",
)
RESPONSIBILITY_OBJECT = re.compile(
    r"对(?P<object>[^!?！？。；;]{1,240}?)(?:(?:负有?|承担)(?:主体|审核|审批)?责任|负责)(?:[!?！？。；;]|$)"
)
ACTION_OBJECT = re.compile(r"(?:^|[，,。；;])(?:负责|核对|确保|保证)(?P<object>[^!?！？。；;]{1,160})")
QUOTED = re.compile(r"《([^<>《》]{1,120})》")


def clean_carrier_name(value: str) -> str:
    """Remove only list numbering and terminal punctuation, not qualifiers."""
    text = str(value or "").strip()
    text = re.sub(r"^\s*(?:[（(]?\d+[）)]|\d+[.．、])\s*", "", text)
    if text.startswith("《") and text.endswith("》"):
        text = text[1:-1]
    return norm_text(text.strip(" \t。；;，,"))


def split_carrier_list(text: str) -> set[str]:
    # Matrix/source carrier cells use punctuation outside brackets/book-title
    # marks as reliable separators. Conjunction characters remain part of names.
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    source = str(text or "")
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "《": "》"}
    for index, char in enumerate(source):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif not stack and char in "；;\n、，,":
            parts.append(source[start:index])
            start = index + 1
    parts.append(source[start:])
    return {name for part in parts if (name := clean_carrier_name(part))}


def terminology_carriers(resource: Any) -> set[str]:
    if not isinstance(resource, dict):
        return set()
    terms = resource.get("terms")
    if not isinstance(terms, dict) or not isinstance(terms.get("carrier"), list):
        return set()
    return {norm_text(x) for x in terms["carrier"] if isinstance(x, str) and norm_text(x)}


def _quality_stripped(value: str) -> str:
    text = norm_text(value).strip("、，,")
    text = DESCRIBED_QUALITY_TAIL.sub("", text)
    previous = None
    while text != previous:
        previous = text
        for suffix in ACTION_QUALITY_TAILS:
            if text.endswith(suffix):
                text = text[: -len(suffix)].rstrip("的、，,")
                break
        else:
            text = QUALITY_TAIL.sub("", text).rstrip("的、，,")
    return text


def _is_term_boundary(text: str, start: int, end: int) -> bool:
    before = text[:start]
    after = text[end:]
    if before and before[-1] not in "对、，,；;。:：()（）[]【】《》\n" and not before.endswith(("及", "和", "与")):
        return False
    if not after:
        return True
    if after[0] in "、，,；;。:：()（）[]【】《》\n的":
        return True
    if after.startswith(("及", "和", "与")):
        return True
    if after.startswith(QUALITY_NAMES) or after.startswith(ACTION_QUALITY_TAILS):
        return True
    if after.startswith(("涉及财务管理的部分", "财务管理的部分")):
        return True
    return False


def _known_references(text: str, known: Iterable[str], aliases: dict[str, str]) -> tuple[set[str], list[tuple[int, int]]]:
    references: set[str] = set()
    occupied: list[tuple[int, int]] = []
    for term in sorted({x for x in known if x}, key=lambda x: (-len(x), x)):
        cursor = 0
        while (start := text.find(term, cursor)) >= 0:
            end = start + len(term)
            cursor = end
            if any(start < right and end > left for left, right in occupied):
                continue
            if not _is_term_boundary(text, start, end):
                continue
            occupied.append((start, end))
            references.add(aliases.get(term, term))
    return references, occupied


def _looks_like_document(value: str) -> bool:
    # An action ending in a document noun is still a sentence fragment.
    # Keep it unavailable unless an exact, bounded vocabulary match exists.
    if value.startswith(("按时", "及时", "按照", "按规定", "开展", "收集", "审核", "编制", "提交", "发起", "发布", "上传", "确保", "保证", "负责", "核对")):
        return False
    return any(value.endswith(ending) for ending in DOCUMENT_ENDINGS)


def _is_behaviour(value: str) -> bool:
    value = norm_text(value)
    compact = re.sub(r"[、，,]", "", value)
    return compact in GENERIC_OBJECTS or any(pattern.fullmatch(compact) for pattern in BEHAVIOUR_PATTERNS)


def _object_remainder(value: str, spans: list[tuple[int, int]]) -> str:
    chars = list(value)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    remainder = norm_text("".join(chars))
    remainder = _quality_stripped(remainder)
    return remainder.strip("、，,及和与的")


def parse_carrier_references(
    text: str,
    *,
    allowed: Iterable[str],
    aliases: dict[str, str] | None = None,
    terminology: Iterable[str] = (),
    _version: int = 1,
) -> dict[str, Any]:
    """Deterministically parse carrier references from one duty sentence.

    Status is one of matched, unresolved, unavailable, or not_applicable.
    The terminology set is recognition-only; callers still compare every
    recognized canonical reference with the current measure's allowed set.
    """
    sentence = str(text or "")
    normalized = norm_text(sentence)
    alias_map = {norm_text(k): norm_text(v) for k, v in (aliases or {}).items() if norm_text(k) and norm_text(v)}
    allowed_set = {norm_text(x) for x in allowed if norm_text(x)}
    known = allowed_set | set(alias_map) | set(alias_map.values()) | {norm_text(x) for x in terminology if norm_text(x)}
    references: set[str] = set()
    unresolved: set[str] = set()
    unavailable: list[str] = []
    evidence: list[dict[str, str]] = []

    quoted_spans: list[tuple[int, int]] = []
    for match in QUOTED.finditer(normalized):
        raw = norm_text(match.group(1))
        canonical = alias_map.get(raw, raw)
        references.add(canonical)
        quoted_spans.append(match.span())
        evidence.append({"kind": "quoted_reference", "text": raw})

    if _version == 2:
        # Clause refinement must not discard earlier, explicitly bounded
        # document names. Vocabulary recognizes names but never allows them.
        found, _ = _known_references(normalized, known, alias_map)
        references.update(found)
        for name in sorted(found):
            evidence.append({"kind": "bounded_full_text_reference", "text": name})

    objects: list[tuple[str, str]] = []
    responsibility_pattern = RESPONSIBILITY_OBJECT
    if _version == 2:
        # A later 对-clause owns its own responsibility object. Crossing it
        # lets the quality-tail stripper erase that entire second clause.
        responsibility_pattern = re.compile(
            r"对(?P<object>(?:(?![，,](?:并|且|而)?对)[^!?！？。；;]){1,240}?)(?:(?:负有?|承担)(?:主体|审核|审批)?责任|负责)(?:[，,!?！？。；;]|$)"
        )
    for pattern, kind in ((responsibility_pattern, "responsibility_object"), (ACTION_OBJECT, "action_object")):
        for match in pattern.finditer(normalized):
            obj = _quality_stripped(match.group("object"))
            if obj:
                objects.append((obj, kind))

    # Quoted references do not short-circuit parsing: the same sentence can
    # contain additional unquoted, exact known terms.
    for obj, kind in objects:
        unquoted = QUOTED.sub("", obj)
        found, spans = _known_references(unquoted, known, alias_map)
        references.update(found)
        if found:
            evidence.append({"kind": kind, "text": obj})
        remainder = _object_remainder(unquoted, spans)
        if not remainder or _is_behaviour(remainder):
            continue
        # A single document-shaped remainder is an explicit reference.  A
        # conjunction-containing unknown phrase is not split speculatively.
        sentence_fragment = _version == 2 and (
            any(x in remainder for x in (",", "，", "对"))
            or remainder.startswith(("会同", "组织", "复核", "进行", "自动", "办理", "督促"))
        )
        if _looks_like_document(remainder) and not any(x in remainder for x in ("及", "和", "与")) and not sentence_fragment:
            canonical = alias_map.get(remainder, remainder)
            references.add(canonical)
            unresolved.add(canonical)
            evidence.append({"kind": "unlisted_document_reference", "text": remainder})
        elif found or not _is_behaviour(remainder):
            unavailable.append(obj)
            evidence.append({"kind": "unparsed_responsibility_object", "text": obj})

    unresolved.update(references - allowed_set)
    if not normalized:
        status = "not_applicable"
    elif unavailable:
        status = "unavailable"
    elif unresolved:
        status = "unresolved"
    elif references:
        status = "matched"
    else:
        status = "not_applicable"
    return {
        "references": sorted(references),
        "unresolved": sorted(unresolved),
        "status": status,
        "evidence": evidence,
        "original_sentence": sentence,
        "unavailable_fragments": unavailable,
    }


def parse_carrier_references_v2(text: str, **kwargs) -> dict[str, Any]:
    """Versioned clause parsing; v1 remains the frozen default capability."""
    return parse_carrier_references(text, **kwargs, _version=2)
