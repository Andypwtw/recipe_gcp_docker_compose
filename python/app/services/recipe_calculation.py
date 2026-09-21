from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any, Iterable

MAIN_MISSING_RATIO_LIMIT = Decimal("0.20")
MAIN_KCAL_OVERRIDE_THRESHOLD = Decimal("500")
AROMATIC_MAIN_WEIGHT_G = Decimal("80")
NORMAL_SEASONING_MIN_WEIGHT_G = Decimal("50")
HIGH_ENERGY_SEASONING_MIN_WEIGHT_G = Decimal("20")

# High-energy seasoning groups use the lower (20 g) inclusion threshold.
HIGH_ENERGY_CATEGORIES = {
    "FAT_SEASONING",
    "SWEETENER_SEASONING",
    "SAUCE_CONDIMENT",
}

# Aromatics are excluded from the main-ingredient 20% completeness denominator
# unless they are present in a large amount (>= 80 g).
AROMATIC_CATEGORIES = {
    "AROMATIC_SEASONING",
    "HERB_SEASONING",
}

# Dry spices never participate in the main-ingredient 20% completeness ratio.
SPICE_CATEGORIES = {
    "SPICE",
}

# Water/ice is not treated as a main food or seasoning calorie contributor.
NON_FOOD_CATEGORIES = {
    "WATER",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def ceil_price(price: Any) -> int | None:
    """Round a calculated ingredient price upward to a whole currency unit."""
    value = _decimal(price)
    if value is None:
        return None
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def ingredient_price(weight_g: Any, price_per_100g: Any) -> int | None:
    """Return the display price for one ingredient, or None when data is missing."""
    weight = _decimal(weight_g)
    rate = _decimal(price_per_100g)
    if weight is None or rate is None or weight < 0 or rate < 0:
        return None
    return ceil_price(weight * rate / Decimal("100"))


def price_display(value: int | None) -> str:
    return "$無資料" if value is None else f"${value}"


def _line_category(line: dict[str, Any]) -> str:
    return str(line.get("ingredient_category") or "FOOD").upper()


def is_aromatic(line: dict[str, Any]) -> bool:
    return _line_category(line) in AROMATIC_CATEGORIES


def is_spice(line: dict[str, Any]) -> bool:
    return _line_category(line) in SPICE_CATEGORIES


def is_high_energy_seasoning(line: dict[str, Any]) -> bool:
    return _line_category(line) in HIGH_ENERGY_CATEGORIES


def is_main_coverage_line(line: dict[str, Any]) -> bool:
    """Whether this line participates in the main-ingredient 20% missing ratio."""
    category = _line_category(line)
    weight = _decimal(line.get("weight_g"))

    if category in NON_FOOD_CATEGORIES or category in SPICE_CATEGORIES:
        return False
    if category in AROMATIC_CATEGORIES:
        return weight is not None and weight >= AROMATIC_MAIN_WEIGHT_G
    if bool(line.get("is_seasoning")):
        return False
    return True


def is_seasoning_calorie_line(line: dict[str, Any]) -> bool:
    """Whether this line should be handled by the seasoning weight thresholds."""
    category = _line_category(line)
    if category in NON_FOOD_CATEGORIES:
        return False
    if category in SPICE_CATEGORIES:
        return True
    if category in AROMATIC_CATEGORIES:
        weight = _decimal(line.get("weight_g"))
        return weight is None or weight < AROMATIC_MAIN_WEIGHT_G
    return bool(line.get("is_seasoning"))


def should_include_seasoning_calorie(line: dict[str, Any]) -> bool:
    """Apply 50 g ordinary / 20 g high-energy seasoning thresholds.

    Seasonings without a usable weight are excluded from calorie calculation.
    """
    if not is_seasoning_calorie_line(line):
        return False
    weight = _decimal(line.get("weight_g"))
    if weight is None:
        return False
    threshold = (
        HIGH_ENERGY_SEASONING_MIN_WEIGHT_G
        if is_high_energy_seasoning(line)
        else NORMAL_SEASONING_MIN_WEIGHT_G
    )
    return weight >= threshold


def _line_kcal(line: dict[str, Any], *, allow_fallback: bool = True) -> Decimal | None:
    weight = _decimal(line.get("weight_g"))
    kcal_per_100g = _decimal(line.get("energy_kcal_per_100g"))
    if kcal_per_100g is None and allow_fallback:
        kcal_per_100g = _decimal(line.get("fallback_kcal_per_100g"))
    if weight is None or kcal_per_100g is None or weight < 0:
        return None
    return kcal_per_100g * weight / Decimal("100")


@dataclass(frozen=True)
class RecipeCalculationResult:
    ingredient_energy_kcal: int | None
    seasoning_energy_kcal: int | None
    energy_kcal: int | None
    main_missing_ratio_percent: float | None
    main_total_lines: int
    main_missing_lines: int
    estimated_price: int | None
    priced_lines: int
    total_lines: int
    calorie_status: str
    price_status: str


def calculate_recipe(lines: Iterable[dict[str, Any]]) -> RecipeCalculationResult:
    rows = list(lines)

    main_rows = [row for row in rows if is_main_coverage_line(row)]
    main_known_values: list[Decimal] = []
    main_missing = 0
    for row in main_rows:
        kcal = _line_kcal(row, allow_fallback=False)
        if kcal is None:
            main_missing += 1
        else:
            main_known_values.append(kcal)

    main_known_sum = sum(main_known_values, Decimal("0"))
    main_total = len(main_rows)
    if main_total:
        missing_ratio = Decimal(main_missing) / Decimal(main_total)
        missing_ratio_percent = float(missing_ratio * Decimal("100"))
    else:
        missing_ratio = None
        missing_ratio_percent = None

    ingredient_energy: int | None
    if not main_total or not main_known_values:
        ingredient_energy = None
    elif (
        missing_ratio is not None
        and missing_ratio > MAIN_MISSING_RATIO_LIMIT
        and main_known_sum <= MAIN_KCAL_OVERRIDE_THRESHOLD
    ):
        ingredient_energy = None
    else:
        ingredient_energy = int(main_known_sum)

    seasoning_values: list[Decimal] = []
    for row in rows:
        if not should_include_seasoning_calorie(row):
            continue
        kcal = _line_kcal(row)
        if kcal is not None:
            seasoning_values.append(kcal)

    seasoning_energy = int(sum(seasoning_values, Decimal("0"))) if seasoning_values else None

    if ingredient_energy is None:
        total_energy = None
        calorie_status = "INSUFFICIENT"
    else:
        total_energy = ingredient_energy + (seasoning_energy or 0)
        calorie_status = "CALCULATED"

    item_prices = [ingredient_price(row.get("weight_g"), row.get("price_per_100g")) for row in rows]
    known_prices = [value for value in item_prices if value is not None]
    total_price = sum(known_prices) if known_prices else None
    if total_price is None:
        price_status = "INSUFFICIENT"
    elif len(known_prices) == len(rows):
        price_status = "CALCULATED"
    else:
        price_status = "PARTIAL"

    return RecipeCalculationResult(
        ingredient_energy_kcal=ingredient_energy,
        seasoning_energy_kcal=seasoning_energy,
        energy_kcal=total_energy,
        main_missing_ratio_percent=missing_ratio_percent,
        main_total_lines=main_total,
        main_missing_lines=main_missing,
        estimated_price=total_price,
        priced_lines=len(known_prices),
        total_lines=len(rows),
        calorie_status=calorie_status,
        price_status=price_status,
    )
