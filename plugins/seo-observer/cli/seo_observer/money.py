"""Exact presentation of aggregate minor units; unknown currencies have no default.

XTR uses integral Stars and TON uses nanotons. These are units, not exchange
rates: currencies are never converted into one another here.
"""
from decimal import Decimal, localcontext


CURRENCY_EXPONENTS = {
    "RUB": 2,
    "USD": 2,
    "EUR": 2,
    "XTR": 0,
    "TON": 9,
    "JPY": 0,
    "KWD": 3,
}


def format_minor(
    value: int | float | Decimal,
    currency: str | None,
    *,
    decimal_separator: str = ".",
    compact: bool = False,
) -> str | None:
    """Format in native units without passing integer minor values through float.

    Legacy reports retain fixed native precision. Compact cards omit the
    decimal suffix only for integral major amounts. None means unsupported.
    """
    exponent = CURRENCY_EXPONENTS.get(currency)
    if exponent is None:
        return None
    minor = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(28, len(minor.as_tuple().digits) + exponent + 2)
        amount = minor.scaleb(-exponent)
        places = 0 if compact and amount == amount.to_integral_value() else exponent
        formatted = f"{amount:,.{places}f}"
    return formatted.replace(",", "\u00a0").replace(".", decimal_separator)
