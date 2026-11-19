// ============================================================
// PRESIDENCY UNIVERSITY - BUDGET BREAKUP FORM: fixed structure
// ------------------------------------------------------------
// Five categories, in the order printed on the university form.
// `key` values are the field names stored in MongoDB
// (event.budget_breakup.<category>.<item>), so do not rename them.
// ============================================================

export const BUDGET_CATEGORIES = [
  {
    key: "resource_person",
    label: "Resource Person",
    items: [
      { key: "honorarium", label: "Honorarium" },
      { key: "lodging", label: "Lodging" },
      { key: "transportation", label: "Transportation" },
      { key: "memento", label: "Memento" },
      { key: "others", label: "Others (Specify)", other: true },
    ],
  },
  {
    key: "branding",
    label: "Branding",
    items: [
      { key: "print_banners_standees", label: "Print Banners/Standees" },
      { key: "brochures", label: "Brochures" },
      { key: "others", label: "Others (Specify)", other: true },
    ],
  },
  {
    key: "participants",
    label: "Participants",
    items: [
      { key: "certificates", label: "Certificates" },
      { key: "prize", label: "Prize" },
      { key: "conference_kits", label: "Conference Kits" },
      { key: "others", label: "Others (Specify)", other: true },
    ],
  },
  {
    key: "refreshments",
    label: "Refreshments",
    items: [
      { key: "breakfast_snacks", label: "Breakfast/snacks" },
      { key: "lunch", label: "Lunch" },
      { key: "others", label: "Others (Specify)", other: true },
    ],
  },
  {
    key: "miscellaneous",
    label: "Miscellaneous",
    items: [
      { key: "badges", label: "Badges" },
      { key: "stationery", label: "Stationery" },
      { key: "others", label: "Others (Specify)", other: true },
    ],
  },
];

// Same limits the backend enforces (rupees).
export const BUDGET_MAX_TOTAL = 100000000; // 10,00,00,000
export const BUDGET_MAX_FIELD = 100000000; // one quantity or amount
export const OTHER_DESCRIPTION_MAX = 100; // characters