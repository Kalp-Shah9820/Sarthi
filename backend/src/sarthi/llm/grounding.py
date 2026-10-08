"""Grounding check: a model-written sentence may only contain numbers that the code produced.

Deliberately strict. Rejecting a good sentence costs nothing (a template is used instead); accepting a
sentence with a wrong number costs the user's trust.
"""

import re

MAX_CHARS = 900
NUM = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*\.?\d*")
SMALL_INTS = {0.0, 1.0, 2.0, 3.0, 4.0}             # counts such as "2 suppliers" are allowed ...
UNIT_AFTER = re.compile(r"^\s*(%|percent|days?\b|units?\b|k\b|lakh|crore)", re.IGNORECASE)   # ... unless a unit follows
CURRENCY_BEFORE = re.compile(r"(₹|rs\.?|inr)\s*$", re.IGNORECASE)


def _to_float(token: str) -> float | None:
    cleaned = token.replace(",", "").rstrip(".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _flatten(facts) -> tuple[list[float], list[str]]:
    """Every number and every string found anywhere in `facts`."""
    numbers: list[float] = []
    strings: list[str] = []

    def walk(value) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            numbers.append(float(value))
        elif isinstance(value, str):
            strings.append(value)
            as_number = _to_float(value.strip()) if NUM.fullmatch(value.strip()) else None
            if as_number is not None:
                numbers.append(as_number)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item)

    walk(facts)
    return numbers, strings


def _variants(x: float) -> set[float]:
    """Ways a fact may legitimately be written: rounded, as a percentage, in thousands or lakhs."""
    x = abs(x)
    forms = {x}
    if 0 < x <= 1:
        forms.add(x * 100)
    if x >= 1000:
        forms.update({x / 1_000, x / 100_000})
    return {round(f, digits) for f in forms for digits in (0, 1, 2)} | forms


def _matches(value: float, allowed: set[float], tol: float) -> bool:
    return any(abs(value - a) <= tol * max(abs(a), 1e-9) for a in allowed)


def grounded(text: str, facts: dict, tol: float = 0.011) -> bool:
    """True when every number in `text` matches a number in `facts` (allowing rounding and unit scaling)."""
    if not text or not text.strip() or len(text) > MAX_CHARS:
        return False
    numbers, strings = _flatten(facts)
    allowed: set[float] = set()
    for number in numbers:
        allowed |= _variants(number)

    # Names and ids such as "Amul Butter 500g" or "PO-2A7F" legitimately contain digits: mask them first.
    masked = text
    for entity in sorted({s for s in strings if len(s) >= 2}, key=len, reverse=True):
        masked = re.sub(re.escape(entity), " ", masked, flags=re.IGNORECASE)

    for match in NUM.finditer(masked):
        value = _to_float(match.group())
        if value is None:
            continue
        value = abs(value)
        if _matches(value, allowed, tol):
            continue
        has_unit = bool(UNIT_AFTER.match(masked[match.end():])) or bool(CURRENCY_BEFORE.search(masked[:match.start()]))
        if value in SMALL_INTS and not has_unit:
            continue
        return False
    return True
