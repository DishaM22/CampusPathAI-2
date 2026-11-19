"""
Presidency University - Budget Breakup Form: server-side rules.

The browser sends  budget_breakup = {category: {item: {selected, quantity,
amount, [description]}}, total_estimated_budget}.  Nothing in it is trusted:
this module rebuilds a clean copy from the fixed structure below, recalculates
the total itself (Decimal, so no float rounding) and rejects a client total
that does not match.

TOTAL = sum of "amount" of every TICKED row. Quantity is informational.
"""

import re
from decimal import Decimal, InvalidOperation

# Same structure and keys as src/components/budget/budgetConfig.js
BUDGET_CATEGORIES = {
    "resource_person": ("honorarium", "lodging", "transportation", "memento", "others"),
    "branding": ("print_banners_standees", "brochures", "others"),
    "participants": ("certificates", "prize", "conference_kits", "others"),
    "refreshments": ("breakfast_snacks", "lunch", "others"),
    "miscellaneous": ("badges", "stationery", "others"),
}

BUDGET_MAX_TOTAL = Decimal("100000000")  # 10,00,00,000 rupees
BUDGET_MAX_FIELD = Decimal("100000000")
OTHER_DESCRIPTION_MAX = 100
TOTAL_KEY = "total_estimated_budget"

TWO_PLACES = Decimal("0.01")


class BudgetError(ValueError):
    """Raised with a readable message and the field path that is wrong."""

    def __init__(self, path, message):
        super().__init__(message)
        self.path = path
        self.message = message


def _label(category, item):
    return f"{category.replace('_', ' ')} / {item.replace('_', ' ')}"


def _number(value, path, what):
    """Empty/None -> 0. Booleans, NaN, inf, negatives, >2 decimals -> error."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, bool):
        raise BudgetError(path, f"{what} must be a number.")
    text = str(value).strip()
    if re.fullmatch(r"-\d+(\.\d+)?", text):
        raise BudgetError(path, f"{what} cannot be negative.")
    # plain digits only: no exponents ("1e3"), signs, spaces, nan or inf
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise BudgetError(path, f"{what} must be a number.")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise BudgetError(path, f"{what} must be a number.")
    if number != number.quantize(TWO_PLACES):
        raise BudgetError(path, f"{what} can have at most 2 decimal places.")
    if number > BUDGET_MAX_FIELD:
        raise BudgetError(path, f"{what} is too large.")
    return number


def _plain(number):
    """Decimal -> int when whole, else float with 2 places (JSON/Mongo safe)."""
    number = number.quantize(TWO_PLACES)
    return int(number) if number == number.to_integral_value() else float(number)


def clean_budget_breakup(raw):
    """
    Validate a budget_breakup dict and return (clean_dict, total_decimal).
    Unknown categories/items are rejected, so nothing extra is ever stored.
    """
    if not isinstance(raw, dict):
        raise BudgetError("budget_breakup", "Budget breakup must be an object.")

    for key in raw:
        if key != TOTAL_KEY and key not in BUDGET_CATEGORIES:
            raise BudgetError(f"budget_breakup.{key}", f"Unknown budget section '{key}'.")

    clean = {}
    total = Decimal("0")

    for category, items in BUDGET_CATEGORIES.items():
        section = raw.get(category) or {}
        if not isinstance(section, dict):
            raise BudgetError(f"budget_breakup.{category}", "Section must be an object.")
        for key in section:
            if key not in items:
                raise BudgetError(
                    f"budget_breakup.{category}.{key}", f"Unknown budget item '{key}'."
                )

        clean[category] = {}
        for item in items:
            entry = section.get(item) or {}
            path = f"budget_breakup.{category}.{item}"
            if not isinstance(entry, dict):
                raise BudgetError(path, "Budget row must be an object.")

            selected = entry.get("selected", False)
            if not isinstance(selected, bool):
                raise BudgetError(path, "'selected' must be true or false.")

            where = _label(category, item)
            quantity = _number(entry.get("quantity"), path + ".quantity", f"Quantity ({where})")
            amount = _number(entry.get("amount"), path + ".amount", f"Estimated cost ({where})")

            row = {
                "selected": selected,
                "quantity": _plain(quantity),
                "amount": _plain(amount),
            }

            if item == "others":
                text = " ".join(str(entry.get("description") or "").split())
                if len(text) > OTHER_DESCRIPTION_MAX:
                    raise BudgetError(
                        path + ".description",
                        f"Description must be {OTHER_DESCRIPTION_MAX} characters or fewer.",
                    )
                if selected and not text:
                    raise BudgetError(path + ".description", f"Please specify the expense ({where}).")
                row["description"] = text

            if selected:
                total += amount
            clean[category][item] = row

    if total > BUDGET_MAX_TOTAL:
        raise BudgetError("budget_breakup", "Budget total is too large (maximum 10,00,00,000).")

    # The client total is only a cross-check; the server's figure is stored.
    if TOTAL_KEY in raw and raw[TOTAL_KEY] is not None:
        claimed = _number(raw[TOTAL_KEY], f"budget_breakup.{TOTAL_KEY}", "Total")
        if claimed != total:
            raise BudgetError(
                f"budget_breakup.{TOTAL_KEY}",
                "The total does not match the entered costs. Please reload the form and try again.",
            )

    clean[TOTAL_KEY] = _plain(total)
    return clean, total


def has_budget_data(breakup, legacy_budget):
    """True if the request tries to send any budget information at all."""
    return breakup is not None or legacy_budget is not None