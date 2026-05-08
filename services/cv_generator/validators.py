import re

_RUT_CLEAN_RE = re.compile(r"[^0-9kK]")


def normalize_rut(raw: str) -> str:
    cleaned = _RUT_CLEAN_RE.sub("", raw).upper()
    if len(cleaned) < 2:
        raise ValueError("rut too short")
    body, dv = cleaned[:-1], cleaned[-1]
    if not body.isdigit():
        raise ValueError("rut body must be digits")
    return f"{body}-{dv}"


def validate_rut(rut: str) -> str:
    if "-" not in rut:
        rut = normalize_rut(rut)
    body, dv = rut.split("-")
    digits = list(map(int, body[::-1]))
    factors = [2, 3, 4, 5, 6, 7] * ((len(digits) + 5) // 6)
    total = sum(d * f for d, f in zip(digits, factors))
    remainder = 11 - (total % 11)
    expected_dv = {11: "0", 10: "K"}.get(remainder, str(remainder))
    if expected_dv != dv.upper():
        raise ValueError(f"invalid RUT check digit: {rut}")
    return f"{body}-{expected_dv}"
