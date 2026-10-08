"""Exact, source-linked local calculations; never business approval or stock moves."""

import re
from decimal import Context, Decimal, localcontext

MAX_ROWS = 200
ROW_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
DECIMAL_VALUE = re.compile(r"[+-]?[0-9]{1,18}(?:\.[0-9]{1,6})?")
SCOPE = "本地人工核对明细；不代表报销批准/付款或真实收发"
UNKNOWN = {"unknown", "pending", "tbd", "n/a", "?", "未知", "待核", "未核", "不明", "待确认", "未确认"}


class CalculationError(Exception):
    """Invalid or unconfirmed local rows, suitable for a caller's 422 response."""


def _dimension(domain):
    if not isinstance(domain, str) or domain not in {"expense", "inventory"}:
        raise CalculationError("仅费用与物资明细支持本地核对计算。")
    return "currency" if domain == "expense" else "unit"


def _required_text(value, label, limit, *, known=False):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise CalculationError(f"{label}须为非空文字，且不超过{limit}字符。")
    normalized = value.strip()
    if known and (normalized.casefold() in UNKNOWN or any(marker in normalized for marker in ("待核", "未知", "未核", "不明", "待确认", "未确认"))):
        raise CalculationError(f"{label}仍待核，不能参加计算。")
    return normalized


def _normalize_rows(domain, rows, allowed_sources=None):
    dimension = _dimension(domain)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise CalculationError("明细须为列表，最多200行。")
    expected = {"id", "label", "value", dimension, "period", "source_id", "source_position"}
    result, used_ids = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected:
            raise CalculationError("明细字段不符合费用或物资行合同，请核对缺项与额外字段。")
        row_id = row["id"]
        if not isinstance(row_id, str) or not ROW_ID.fullmatch(row_id):
            raise CalculationError("行ID仅允许字母、数字、下划线和短横线，最多80字符。")
        if row_id in used_ids:
            raise CalculationError("明细行ID重复，请核对原记录，不能自动去重。")
        used_ids.add(row_id)
        value = row["value"]
        if not isinstance(value, str) or not DECIMAL_VALUE.fullmatch(value):
            raise CalculationError("金额或数量须为明确十进制字符串，最多18位整数、6位小数；缺数不能当0。")
        source_id = _required_text(row["source_id"], "来源ID", 80)
        if allowed_sources is not None and source_id not in allowed_sources:
            raise CalculationError("明细来源不属于本事项，请引用本事项已有来源。")
        result.append({"id": row_id, "label": _required_text(row["label"], "项目或物品", 500),
                       "value": format(Decimal(value), "f"),
                       dimension: _required_text(row[dimension], "币种" if dimension == "currency" else "单位", 80, known=True),
                       "period": _required_text(row["period"], "期间", 200, known=True),
                       "source_id": source_id,
                       # Position is the local recorder's citation, not proof of original verification.
                       "source_position": _required_text(row["source_position"], "来源原位置", 500, known=True)})
    return result


def validate_rows(domain, rows, sources):
    """Validate rows against the caller's current matter sources without mutating input."""
    if not isinstance(sources, list):
        raise CalculationError("本事项来源须为列表，来源未核不能计算。")
    source_ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise CalculationError("本事项来源格式无效。")
        source_id = _required_text(source.get("id"), "来源ID", 80)
        if source_id in source_ids:
            raise CalculationError("本事项来源ID重复，来源归属须先核对。")
        source_ids.add(source_id)
    return _normalize_rows(domain, rows, source_ids)


def summarize_rows(domain, rows):
    """Sum exact currencies/periods or item labels/units/periods; no conversion or item merging."""
    dimension = _dimension(domain)
    normalized = _normalize_rows(domain, rows)
    groups = {}
    # At most 24 input digits and 200 rows need fewer than 28 significant digits.
    # A local context protects exact sums from the calling thread's Decimal settings.
    with localcontext(Context(prec=40)):
        for row in normalized:
            key = (row[dimension], row["period"]) if domain == "expense" else (row["label"], row[dimension], row["period"])
            if key not in groups:
                groups[key] = {dimension: row[dimension], "period": row["period"], "total": Decimal(0), "row_ids": []}
                if domain == "inventory":
                    groups[key]["label"] = row["label"]
            groups[key]["total"] += Decimal(row["value"])
            groups[key]["row_ids"].append(row["id"])
        values = [{**group, "total": format(group["total"], "f")} for group in groups.values()]
    return {"groups": values, "row_count": len(normalized), "scope": SCOPE}
