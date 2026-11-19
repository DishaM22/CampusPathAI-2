import {
  BUDGET_CATEGORIES,
  BUDGET_MAX_FIELD,
  BUDGET_MAX_TOTAL,
  OTHER_DESCRIPTION_MAX,
} from "./budgetConfig";

// ------------------------------------------------------------
// Form state: every number is kept as TEXT so a field can be
// empty while the user types. Empty counts as zero.
//   { resource_person: { honorarium: { selected, quantity,
//     amount, description }, ... }, ... }
// ------------------------------------------------------------

export const emptyEntry = () => ({
  selected: false,
  quantity: "",
  amount: "",
  description: "",
});

export function emptyBreakup() {
  const state = {};
  for (const category of BUDGET_CATEGORIES) {
    state[category.key] = {};
    for (const item of category.items) {
      state[category.key][item.key] = emptyEntry();
    }
  }
  return state;
}

/** True when an event carries a saved breakup (older events do not). */
export function hasSavedBreakup(saved) {
  return (
    Boolean(saved) &&
    typeof saved === "object" &&
    BUDGET_CATEGORIES.some((category) => saved[category.key])
  );
}

/** True when at least one row is ticked. */
export function anySelected(breakup) {
  return BUDGET_CATEGORIES.some((category) =>
    category.items.some(
      (item) => breakup?.[category.key]?.[item.key]?.selected === true
    )
  );
}

/** Saved event.budget_breakup -> form state (missing parts stay empty). */
export function breakupFromEvent(saved) {
  const state = emptyBreakup();
  if (!hasSavedBreakup(saved)) return state;

  for (const category of BUDGET_CATEGORIES) {
    for (const item of category.items) {
      const entry = saved?.[category.key]?.[item.key];
      if (!entry || typeof entry !== "object") continue;

      state[category.key][item.key] = {
        selected: entry.selected === true,
        quantity:
          entry.quantity === null || entry.quantity === undefined
            ? ""
            : String(entry.quantity),
        amount:
          entry.amount === null || entry.amount === undefined
            ? ""
            : String(entry.amount),
        description: item.other ? String(entry.description || "") : "",
      };
    }
  }
  return state;
}

/**
 * Text -> number. Empty is 0. Anything that is not a plain
 * non-negative number with at most 2 decimals gives NaN.
 */
export function parseNumber(value) {
  const text = String(value ?? "").trim();
  if (text === "") return 0;
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return NaN;
  return Number(text);
}

const toPaise = (rupees) => Math.round(rupees * 100);

/**
 * TOTAL = sum of the Estimated Cost of every TICKED row.
 * Unticked rows, empty cells and unreadable values count as 0.
 * Works in paise so 0.1 + 0.2 style rounding errors cannot appear.
 */
export function computeTotal(breakup) {
  let paise = 0;
  for (const category of BUDGET_CATEGORIES) {
    for (const item of category.items) {
      const entry = breakup?.[category.key]?.[item.key];
      if (!entry || entry.selected !== true) continue;
      const amount = parseNumber(entry.amount);
      if (Number.isFinite(amount)) paise += toPaise(amount);
    }
  }
  return paise / 100;
}

/** Indian rupee format: 150000 -> "₹ 1,50,000". */
export function formatRupees(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "₹ 0";
  return `₹ ${number.toLocaleString("en-IN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
}

/** First problem as readable text, or "" when the breakup is fine. */
export function validateBreakup(breakup) {
  for (const category of BUDGET_CATEGORIES) {
    for (const item of category.items) {
      const entry = breakup?.[category.key]?.[item.key] || emptyEntry();
      const where = `Budget - ${category.label}, ${item.label}`;

      const quantity = parseNumber(entry.quantity);
      if (!Number.isFinite(quantity)) {
        return `${where}: quantity must be a number that is not negative (up to 2 decimals).`;
      }
      if (quantity > BUDGET_MAX_FIELD) {
        return `${where}: quantity is too large.`;
      }

      const amount = parseNumber(entry.amount);
      if (!Number.isFinite(amount)) {
        return `${where}: estimated cost must be a number that is not negative (up to 2 decimals).`;
      }
      if (amount > BUDGET_MAX_FIELD) {
        return `${where}: estimated cost is too large.`;
      }

      if (item.other && entry.selected) {
        const text = String(entry.description || "").trim();
        if (!text) {
          return `${where}: please specify the expense.`;
        }
        if (text.length > OTHER_DESCRIPTION_MAX) {
          return `${where}: description must be ${OTHER_DESCRIPTION_MAX} characters or fewer.`;
        }
      }
    }
  }

  if (computeTotal(breakup) > BUDGET_MAX_TOTAL) {
    return "Budget total is too large (maximum 10,00,00,000).";
  }
  return "";
}

/**
 * Form state -> the object sent to the server. The server
 * recalculates and checks total_estimated_budget; it never trusts it.
 */
export function breakupToPayload(breakup) {
  const payload = {};
  for (const category of BUDGET_CATEGORIES) {
    payload[category.key] = {};
    for (const item of category.items) {
      const entry = breakup?.[category.key]?.[item.key] || emptyEntry();
      const quantity = parseNumber(entry.quantity);
      const amount = parseNumber(entry.amount);

      const row = {
        selected: entry.selected === true,
        quantity: Number.isFinite(quantity) ? quantity : 0,
        amount: Number.isFinite(amount) ? amount : 0,
      };
      if (item.other) {
        row.description = String(entry.description || "").trim();
      }
      payload[category.key][item.key] = row;
    }
  }
  payload.total_estimated_budget = computeTotal(breakup);
  return payload;
}