/**
 * ResponsiblePicker — a clearable responsible-party selector.
 *
 * Uses NativeSelect (a true <select>) for reliable fireEvent.change behaviour
 * in vitest/jsdom tests (the same pattern used in Users.tsx for the role picker).
 *
 * The component accepts an injected `users` list so the parent can batch-fetch
 * alongside other reference data without N+1 requests per picker.
 *
 * Value semantics:
 *   - `number`  → a specific user id is selected
 *   - `null`    → unassigned / cleared (inherits the definition default for instances,
 *                 or means truly unassigned for definitions)
 *
 * M6 Step 11 (§7.5).
 */
import { NativeSelect } from "@mantine/core";
import { useTranslation } from "react-i18next";
import type { components } from "../api/schema";

type UserSummary = components["schemas"]["UserSummary"];

export interface ResponsiblePickerProps {
  /** Current value: user id (number) or null (unassigned / inherit). */
  value: number | null;
  /** Called when the user changes the selection. */
  onChange: (v: number | null) => void;
  /** The user list — fetch with GET /api/users and inject. */
  users: UserSummary[];
  /**
   * Controls the empty-option label:
   *   "unassigned" → "Unassigned"   (definition picker)
   *   "inherited"  → "Inherited from definition"  (instance picker)
   */
  emptyLabel?: "unassigned" | "inherited";
}

export function ResponsiblePicker({
  value,
  onChange,
  users,
  emptyLabel = "unassigned",
}: ResponsiblePickerProps) {
  const { t } = useTranslation("responsible");

  const emptyText =
    emptyLabel === "inherited" ? t("inheritedFromDef") : t("unassigned");

  const options = [
    { value: "", label: emptyText },
    ...users
      .filter((u) => u.is_active)
      .map((u) => ({ value: String(u.id), label: u.email })),
  ];

  return (
    <NativeSelect
      label={t("label")}
      data={options}
      value={value !== null ? String(value) : ""}
      onChange={(e) => {
        const v = e.currentTarget.value;
        onChange(v === "" ? null : Number(v));
      }}
      data-testid="responsible-picker"
    />
  );
}
