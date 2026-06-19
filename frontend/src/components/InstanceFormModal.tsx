/**
 * InstanceFormModal — shared modal for creating/editing a stock instance.
 *
 * Enforces the client-side serial ⇒ quantity = 1 rule (M1 §7.3) for exact mode:
 *   - When serial is non-empty, quantity is forced to "1" and disabled.
 *   - The server's 422 is surfaced via the `error` prop.
 *
 * M2 extension (§7.2): branches by the parent definition's `stock_tracking_mode`:
 *   - exact: shows `quantity` field (locked on edit; serial⇒qty=1 still applies).
 *   - level: shows `stock_level` Select (high/medium/low); no quantity.
 *   - none:  neither quantity nor stock_level — just identity/location/durable fields.
 *
 * Used by both the Items (definition detail) page and the InstanceDetail page.
 */
import {
  Modal,
  Stack,
  Select,
  TextInput,
  Textarea,
  NumberInput,
  Button,
  Group,
  Alert,
} from "@mantine/core";
import { AlertCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import type { components } from "../api/schema";
import { formatDate } from "../i18n/format";

/**
 * Compute today + N days as an ISO YYYY-MM-DD string (UTC).
 * Used for the best_before_date pre-fill when the definition has a default.
 */
function computeDefaultBestBefore(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + days);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// ── Types ─────────────────────────────────────────────────────────────────────

type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type LocationResponse = components["schemas"]["LocationResponse"];

export interface InstanceFormState {
  definition_id: string;
  location_id: string;
  quantity: string;
  stock_level: string;
  serial: string;
  model_number: string;
  manufacturer: string;
  best_before_date: string; // ISO date string or "" (M3)
  warranty_expires: string;
  warranty_details: string;
  purchase_price: string;
  purchase_date: string;
  purchase_source: string;
}

// ── Props ─────────────────────────────────────────────────────────────────────

export interface InstanceFormModalProps {
  opened: boolean;
  title: string;
  form: InstanceFormState;
  setForm: React.Dispatch<React.SetStateAction<InstanceFormState>>;
  onSubmit: () => void;
  onClose: () => void;
  busy: boolean;
  error: string | null;
  definitions: DefinitionResponse[];
  locations: LocationResponse[];
  /** When true, definition picker is locked (pre-filled from context). */
  lockDefinition?: boolean;
  /**
   * The parent definition's stock_tracking_mode.
   * Drives which quantity/level controls are shown.
   * Defaults to "exact" when not provided (backward compat).
   */
  trackingMode?: string;
  /**
   * When true, this is an edit (not create) operation.
   * In exact mode: quantity is locked/disabled (changes go through ledger).
   */
  isEdit?: boolean;
  /**
   * The parent definition's default_best_before_days (M3).
   * When set and best_before_date is empty on create, the form pre-fills the
   * computed date (today + N) and shows a hint. Mode-independent.
   */
  definitionDefaultBestBeforeDays?: number | null;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function InstanceFormModal({
  opened,
  title,
  form,
  setForm,
  onSubmit,
  onClose,
  busy,
  error,
  definitions,
  locations,
  lockDefinition,
  trackingMode = "exact",
  isEdit = false,
  definitionDefaultBestBeforeDays,
}: InstanceFormModalProps) {
  const { t } = useTranslation("instances");
  const { t: tStock } = useTranslation("stock");
  const { t: tExpiry } = useTranslation("expiry");

  // Pre-fill best_before_date when the form opens (create mode) with the computed
  // default if the field is empty and the definition has a default shelf life.
  // We call computeDefaultBestBefore only when needed; it is deterministic for a given day.
  const computedDefaultDate =
    !isEdit && definitionDefaultBestBeforeDays != null
      ? computeDefaultBestBefore(definitionDefaultBestBeforeDays)
      : null;

  // Client-side serial ⇒ quantity = 1 rule (§7.3, exact mode only):
  // When serial is non-empty, force quantity to "1" and disable the field.
  const serialPresent = form.serial.trim().length > 0;

  function handleSerialChange(value: string) {
    setForm((f) => ({
      ...f,
      serial: value,
      // auto-set quantity to 1 when a serial is entered (exact mode only)
      quantity: value.trim().length > 0 ? "1" : f.quantity,
    }));
  }

  const defOptions = definitions.map((d) => ({
    value: String(d.id),
    label: d.name,
  }));
  const locationOptions = [
    { value: "", label: t("form.noneOption") },
    ...locations.map((l) => {
      const assetSuffix = l.container_asset_label ? ` — ${l.container_asset_label}` : "";
      return { value: String(l.id), label: `${l.name}${assetSuffix}` };
    }),
  ];

  // Stock-level options for "level" mode
  const stockLevelOptions = [
    { value: "", label: t("form.noneOption") },
    { value: "high", label: tStock("stockLevel.high") },
    { value: "medium", label: tStock("stockLevel.medium") },
    { value: "low", label: tStock("stockLevel.low") },
  ];

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="md">
      <Stack gap="sm">
        {error && (
          <Alert
            icon={<AlertCircle size={16} />}
            color="red"
            variant="light"
            data-testid="instance-error-alert"
          >
            {error}
          </Alert>
        )}
        <Select
          label={t("form.definitionLabel")}
          required
          data={defOptions}
          value={form.definition_id}
          onChange={(v) => setForm((f) => ({ ...f, definition_id: v ?? "" }))}
          disabled={lockDefinition}
          data-testid="inst-definition-select"
        />
        <Select
          label={t("form.locationLabel")}
          data={locationOptions}
          value={form.location_id}
          onChange={(v) => setForm((f) => ({ ...f, location_id: v ?? "" }))}
          clearable
          data-testid="inst-location-select"
        />
        <TextInput
          label={t("form.serialLabel")}
          value={form.serial}
          onChange={(e) => handleSerialChange(e.currentTarget.value)}
          data-testid="inst-serial-input"
        />

        {/* ── Mode-branching: quantity / stock_level / neither ── */}

        {trackingMode === "exact" && !isEdit && (
          <NumberInput
            label={t("form.quantityLabel")}
            value={form.quantity}
            onChange={(v) => setForm((f) => ({ ...f, quantity: String(v) }))}
            min={0}
            allowDecimal
            disabled={serialPresent}
            description={
              serialPresent ? t("form.quantitySerialHint") : undefined
            }
            data-testid="inst-quantity-input"
          />
        )}

        {trackingMode === "exact" && isEdit && (
          <NumberInput
            label={t("form.quantityLabel")}
            value={form.quantity}
            onChange={() => {/* no-op: locked on edit */}}
            min={0}
            allowDecimal
            disabled
            description={t("form.quantityEditHint")}
            data-testid="inst-quantity-input"
          />
        )}

        {trackingMode === "level" && (
          <Select
            label={t("form.stockLevelLabel")}
            data={stockLevelOptions}
            value={form.stock_level}
            onChange={(v) => setForm((f) => ({ ...f, stock_level: v ?? "" }))}
            clearable
            data-testid="inst-stock-level-select"
          />
        )}

        {/* trackingMode === "none": neither quantity nor stock_level shown */}

        <TextInput
          label={t("form.modelNumberLabel")}
          value={form.model_number}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, model_number: value }));
          }}
        />
        <TextInput
          label={t("form.manufacturerLabel")}
          value={form.manufacturer}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, manufacturer: value }));
          }}
          data-testid="inst-manufacturer-input"
        />
        {/* Best-before date (M3) — mode-independent, shown for all modes */}
        <TextInput
          label={t("form.bestBeforeDateLabel")}
          placeholder={t("form.bestBeforeDatePlaceholder")}
          value={form.best_before_date}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, best_before_date: value }));
          }}
          description={
            !isEdit && computedDefaultDate && !form.best_before_date
              ? tExpiry("defaultHint", {
                  date: formatDate(computedDefaultDate),
                  days: definitionDefaultBestBeforeDays,
                })
              : undefined
          }
          data-testid="inst-best-before-date-input"
        />

        <TextInput
          label={t("form.warrantyExpiresLabel")}
          placeholder={t("form.warrantyExpiresPlaceholder")}
          value={form.warranty_expires}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, warranty_expires: value }));
          }}
        />
        <Textarea
          label={t("form.warrantyDetailsLabel")}
          value={form.warranty_details}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, warranty_details: value }));
          }}
          autosize
          minRows={2}
        />
        <TextInput
          label={t("form.purchasePriceLabel")}
          placeholder={t("form.purchasePricePlaceholder")}
          value={form.purchase_price}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, purchase_price: value }));
          }}
        />
        <TextInput
          label={t("form.purchaseDateLabel")}
          placeholder={t("form.purchaseDatePlaceholder")}
          value={form.purchase_date}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, purchase_date: value }));
          }}
        />
        <TextInput
          label={t("form.purchaseSourceLabel")}
          value={form.purchase_source}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, purchase_source: value }));
          }}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={busy}>
            {t("common:actions.cancel", "Cancel")}
          </Button>
          <Button
            onClick={onSubmit}
            loading={busy}
            disabled={!form.definition_id}
            data-testid="inst-submit-btn"
          >
            {t("common:actions.save", "Save")}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
