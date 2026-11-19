import React, { useMemo } from "react";
import "./BudgetBreakupForm.css";
import { BUDGET_CATEGORIES, OTHER_DESCRIPTION_MAX } from "./budgetConfig";
import { computeTotal, formatRupees, parseNumber } from "./budgetUtils";

/**
 * Presidency University - Budget Breakup Form.
 *
 *   value      form state from budgetUtils (emptyBreakup / breakupFromEvent)
 *   onChange   called with the new state (not used when readOnly)
 *   readOnly   true for HOD / Dean / Admin views: checkboxes cannot be
 *              changed and every field is read-only
 *   legacyTotal  single-figure budget of an older event (no breakup yet)
 *
 * The TOTAL is derived from `value` on every render. It is never stored
 * in state and can never be typed into.
 */
export default function BudgetBreakupForm({
  value,
  onChange,
  readOnly = false,
  legacyTotal = null,
  idPrefix = "bbf",
}) {
  const total = useMemo(() => computeTotal(value), [value]);

  function setEntry(categoryKey, itemKey, patch) {
    if (readOnly || !onChange) return;
    onChange({
      ...value,
      [categoryKey]: {
        ...value[categoryKey],
        [itemKey]: { ...value[categoryKey][itemKey], ...patch },
      },
    });
  }

  const hasLegacy =
    legacyTotal !== null && legacyTotal !== undefined && legacyTotal !== "";

  return (
    <section className="bbf" aria-label="Budget Breakup Form">
      <header className="bbf-head">
        
        <div className="bbf-title">BUDGET BREAKUP FORM</div>
        <p className="bbf-note">
          Note: Please tick whatever applicable as per the nature and size of
          the event.
        </p>
      </header>

      {hasLegacy && (
        <p className="bbf-legacy">
          {readOnly
            ? `Older event: only a single budget figure of ${formatRupees(
                legacyTotal
              )} was recorded (no breakup).`
            : `This event was saved earlier with a single budget figure of ${formatRupees(
                legacyTotal
              )}. Fill in the breakup below to replace it.`}
        </p>
      )}

      <div className="bbf-scroll">
        <table className="bbf-table">
          <colgroup>
            <col className="bbf-col-particulars" />
            <col className="bbf-col-qty" />
            <col className="bbf-col-cost" />
          </colgroup>

          <thead>
            <tr>
              <th scope="col">Particulars</th>
              <th scope="col">Quantity</th>
              <th scope="col">Estimated Cost in rupees</th>
            </tr>
          </thead>

          <tbody>
            {BUDGET_CATEGORIES.map((category) => (
              <React.Fragment key={category.key}>
                <tr className="bbf-category">
                  <th colSpan={3} scope="colgroup">
                    {category.label}
                  </th>
                </tr>

                {category.items.map((item) => {
                  const entry = value?.[category.key]?.[item.key] || {
                    selected: false,
                    quantity: "",
                    amount: "",
                    description: "",
                  };
                  const id = `${idPrefix}-${category.key}-${item.key}`;
                  const live = entry.selected && !readOnly;
                  const amountNumber = parseNumber(entry.amount);

                  return (
                    <tr
                      key={item.key}
                      className={entry.selected ? "bbf-row" : "bbf-row bbf-off"}
                    >
                      <td className="bbf-particular">
                        <label className="bbf-check" htmlFor={`${id}-tick`}>
                          <input
                            id={`${id}-tick`}
                            type="checkbox"
                            checked={entry.selected}
                            disabled={readOnly}
                            onChange={(e) =>
                              setEntry(category.key, item.key, {
                                selected: e.target.checked,
                              })
                            }
                          />
                          <span>{item.label}</span>
                        </label>

                        {item.other && entry.selected && (
                          <input
                            type="text"
                            className="bbf-input bbf-spec"
                            aria-label={`${category.label}: specify the other expense`}
                            placeholder="Specify expense"
                            maxLength={OTHER_DESCRIPTION_MAX}
                            value={entry.description}
                            readOnly={readOnly}
                            onChange={(e) =>
                              setEntry(category.key, item.key, {
                                description: e.target.value,
                              })
                            }
                          />
                        )}
                      </td>

                      <td>
                        <input
                          type="number"
                          className="bbf-input"
                          aria-label={`${item.label} quantity`}
                          min="0"
                          step="any"
                          inputMode="decimal"
                          value={entry.quantity}
                          readOnly={readOnly}
                          disabled={!readOnly && !entry.selected}
                          onChange={(e) =>
                            setEntry(category.key, item.key, {
                              quantity: e.target.value,
                            })
                          }
                        />
                      </td>

                      <td>
                        {readOnly ? (
                          <input
                            type="text"
                            className="bbf-input bbf-money"
                            aria-label={`${item.label} estimated cost`}
                            readOnly
                            value={
                              entry.selected && entry.amount !== ""
                                ? formatRupees(amountNumber || 0)
                                : ""
                            }
                          />
                        ) : (
                          <>
                            <input
                              type="number"
                              className="bbf-input bbf-money"
                              aria-label={`${item.label} estimated cost in rupees`}
                              min="0"
                              step="any"
                              inputMode="decimal"
                              value={entry.amount}
                              disabled={!live}
                              onChange={(e) =>
                                setEntry(category.key, item.key, {
                                  amount: e.target.value,
                                })
                              }
                            />
                            {entry.selected &&
                              Number.isFinite(amountNumber) &&
                              amountNumber > 0 && (
                                <small className="bbf-pretty">
                                  {formatRupees(amountNumber)}
                                </small>
                              )}
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </React.Fragment>
            ))}

            <tr className="bbf-total">
              <th colSpan={2} scope="row">
                TOTAL
              </th>
              <td aria-live="polite" data-testid="bbf-total">
                {formatRupees(total)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

    
    </section>
  );
}