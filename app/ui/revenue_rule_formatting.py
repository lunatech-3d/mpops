"""Exact, reusable formatting and entry conversion for financial rules."""

from decimal import Decimal, InvalidOperation


def _decimal(value, label: str, *, required: bool = True) -> Decimal | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not number.is_finite():
        raise ValueError(f"{label} must be a finite number.")
    return number


def format_basis_points(value: int) -> str:
    """Display integer basis points as a percentage without trailing zeroes."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Basis points must be an integer.")
    number = Decimal(value) / Decimal(100)
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def percentage_to_basis_points(value, *, required: bool = True) -> int | None:
    number = _decimal(value, "Percentage", required=required)
    if number is None:
        return None
    if number < 0 or number > 100:
        raise ValueError("Percentage must be between 0 and 100.")
    scaled = number * 100
    if scaled != scaled.to_integral_value():
        raise ValueError("Percentage may have no more than two decimal places.")
    return int(scaled)


def amount_to_cents(value, *, required: bool = True) -> int | None:
    number = _decimal(value, "Flat amount", required=required)
    if number is None:
        return None
    if number < 0:
        raise ValueError("Flat amount cannot be negative.")
    scaled = number * 100
    if scaled != scaled.to_integral_value():
        raise ValueError("Flat amount may have no more than two decimal places.")
    return int(scaled)


def format_cents(value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Cents must be an integer.")
    return f"${Decimal(value) / Decimal(100):,.2f}"
