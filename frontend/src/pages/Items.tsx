/**
 * Items page — definition list, search, category filter, and CRUD.
 *
 * Routes handled by this file:
 *   /items       — Items (definition list + search + category filter)
 *   /items/:id   — ItemDetail (definition detail + its instances + register new instance)
 *
 * Instance CRUD modal also lives here because instances are always
 * created/edited in the context of a definition.
 *
 * Data access: exclusively via the typed openapi-fetch client — no hand-written fetch.
 * Money / quantity are sent as strings per the API schema (Decimal on the wire).
 *
 * Client-side serial ⇒ quantity = 1 rule (§7.3) is handled inside InstanceFormModal.
 *
 * M2 Step 7: Consume (FIFO) button, per-lot action menu (Intake/Move/Adjust/Discard),
 * low-stock badge (client-side derived from loaded lots vs min_stock), and mode-aware
 * quantity/level rendering in the instances table.
 */
import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Stack,
  Group,
  Text,
  Title,
  Button,
  TextInput,
  Textarea,
  NumberInput,
  Select,
  Modal,
  Alert,
  Table,
  Badge,
  Anchor,
  Divider,
  ActionIcon,
  Card,
  SimpleGrid,
  Menu,
} from "@mantine/core";
import { Plus, Edit2, Trash2, AlertCircle, ArrowLeft, Search, MoreVertical, Zap } from "react-feather";
import { useTranslation, Trans } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess } from "../components/notify";
import type { components } from "../api/schema";
import { PageShell } from "../components/PageShell";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import {
  InstanceFormModal,
  type InstanceFormState,
} from "../components/InstanceFormModal";
import { ExpiryBadge } from "../components/ExpiryBadge";
import { AttachmentPanel } from "../components/AttachmentPanel";
import { TagPanel } from "../components/TagPanel";
import { NotePanel } from "../components/NotePanel";
import { CustomFieldsEditor } from "../components/CustomFieldsEditor";
import { BarcodePanel } from "../components/BarcodePanel";
import { BarcodeScanModal } from "../components/BarcodeScanModal";
import { formatDate, formatQuantity } from "../i18n/format";

// ── Schema types ─────────────────────────────────────────────────────────────

type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type InstanceResponse = components["schemas"]["InstanceResponse"];
type KindResponse = components["schemas"]["KindResponse"];
type CategoryResponse = components["schemas"]["CategoryResponse"];
type LocationResponse = components["schemas"]["LocationResponse"];
type TagResponse = components["schemas"]["TagResponse"];

// ── Definition form state ────────────────────────────────────────────────────

interface DefinitionFormState {
  name: string;
  description: string;
  category_id: string; // select value (id as string or "")
  kind_id: string;
  unit: string;
  default_location_id: string;
  stock_tracking_mode: string; // "exact" | "level" | "none"
  min_stock: string; // numeric string or "" when not set
  default_best_before_days: string; // integer string or "" when not set
  reminder_lead_days: string; // integer string or "" when not set (M4 per-item override)
  /** M5: arbitrary JSON key/value map; null when empty. */
  custom_fields: Record<string, string | number | boolean | null> | null;
}

const emptyDefForm = (): DefinitionFormState => ({
  name: "",
  description: "",
  category_id: "",
  kind_id: "",
  unit: "pcs",
  default_location_id: "",
  stock_tracking_mode: "exact",
  min_stock: "",
  default_best_before_days: "",
  reminder_lead_days: "",
  custom_fields: null,
});

const emptyInstanceForm = (definitionId?: number): InstanceFormState => ({
  definition_id: definitionId != null ? String(definitionId) : "",
  location_id: "",
  quantity: "1",
  stock_level: "",
  serial: "",
  model_number: "",
  manufacturer: "",
  best_before_date: "",
  warranty_expires: "",
  warranty_details: "",
  purchase_price: "",
  purchase_date: "",
  purchase_source: "",
  custom_fields: null,
});

// ── Modal discriminated unions ────────────────────────────────────────────────

type DefModalState =
  | { kind: "none" }
  | { kind: "create" }
  | { kind: "edit"; def: DefinitionResponse }
  | { kind: "delete"; def: DefinitionResponse };

type InstModalState =
  | { kind: "none" }
  | { kind: "create"; definitionId: number }
  | { kind: "edit"; inst: InstanceResponse }
  | { kind: "delete"; inst: InstanceResponse };

// ── DefinitionFormModal ───────────────────────────────────────────────────────

interface DefinitionFormModalProps {
  opened: boolean;
  title: string;
  form: DefinitionFormState;
  setForm: React.Dispatch<React.SetStateAction<DefinitionFormState>>;
  onSubmit: () => void;
  onClose: () => void;
  busy: boolean;
  error: string | null;
  kinds: KindResponse[];
  categories: CategoryResponse[];
  locations: LocationResponse[];
}

function DefinitionFormModal({
  opened,
  title,
  form,
  setForm,
  onSubmit,
  onClose,
  busy,
  error,
  kinds,
  categories,
  locations,
}: DefinitionFormModalProps) {
  const { t } = useTranslation("items");
  const { t: tStock } = useTranslation("stock");
  const kindOptions = kinds.map((k) => ({
    value: String(k.id),
    label: t(`kinds.${k.code}`, { defaultValue: k.name }),
  }));
  const categoryOptions = [
    { value: "", label: t("defForm.noneOption") },
    ...categories.map((c) => ({ value: String(c.id), label: c.name })),
  ];
  const locationOptions = [
    { value: "", label: t("defForm.noneOption") },
    ...locations.map((l) => {
      const assetSuffix = l.container_asset_label ? ` — ${l.container_asset_label}` : "";
      return { value: String(l.id), label: `${l.name}${assetSuffix}` };
    }),
  ];
  const trackingModeOptions = [
    { value: "exact", label: tStock("trackingMode.exact") },
    { value: "level", label: tStock("trackingMode.level") },
    { value: "none", label: tStock("trackingMode.none") },
  ];

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="md">
      <Stack gap="sm">
        {error && (
          <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
            {error}
          </Alert>
        )}
        <TextInput
          label={t("defForm.nameLabel")}
          required
          value={form.name}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, name: value }));
          }}
          data-autofocus
          data-testid="def-name-input"
        />
        <Textarea
          label={t("defForm.descriptionLabel")}
          value={form.description}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, description: value }));
          }}
          autosize
          minRows={2}
        />
        <Select
          label={t("defForm.categoryLabel")}
          data={categoryOptions}
          value={form.category_id}
          onChange={(v) => setForm((f) => ({ ...f, category_id: v ?? "" }))}
          clearable
          data-testid="def-category-select"
        />
        <Select
          label={t("defForm.kindLabel")}
          data={kindOptions}
          value={form.kind_id}
          onChange={(v) => setForm((f) => ({ ...f, kind_id: v ?? "" }))}
          placeholder={t("defForm.kindPlaceholder")}
          data-testid="def-kind-select"
        />
        <TextInput
          label={t("defForm.unitLabel")}
          value={form.unit}
          onChange={(e) => {
            const value = e.currentTarget.value;
            setForm((f) => ({ ...f, unit: value }));
          }}
          placeholder={t("defForm.unitPlaceholder")}
        />
        <Select
          label={t("defForm.defaultLocationLabel")}
          data={locationOptions}
          value={form.default_location_id}
          onChange={(v) => setForm((f) => ({ ...f, default_location_id: v ?? "" }))}
          clearable
        />
        <Select
          label={t("defForm.trackingModeLabel")}
          data={trackingModeOptions}
          value={form.stock_tracking_mode}
          onChange={(v) => setForm((f) => ({ ...f, stock_tracking_mode: v ?? "exact", min_stock: "" }))}
          data-testid="def-tracking-mode-select"
        />
        {form.stock_tracking_mode === "exact" && (
          <NumberInput
            label={t("defForm.minStockLabel")}
            placeholder={t("defForm.minStockPlaceholder")}
            value={form.min_stock === "" ? "" : Number(form.min_stock)}
            onChange={(v) => setForm((f) => ({ ...f, min_stock: v === "" ? "" : String(v) }))}
            min={0}
            allowDecimal
            data-testid="def-min-stock-input"
          />
        )}
        <NumberInput
          label={t("defForm.defaultBestBeforeDaysLabel")}
          placeholder={t("defForm.defaultBestBeforeDaysPlaceholder")}
          value={form.default_best_before_days === "" ? "" : Number(form.default_best_before_days)}
          onChange={(v) => setForm((f) => ({ ...f, default_best_before_days: v === "" ? "" : String(Math.round(Number(v))) }))}
          min={0}
          allowDecimal={false}
          suffix=" days"
          data-testid="def-default-best-before-days-input"
        />
        <NumberInput
          label={t("defForm.reminderLeadDaysLabel")}
          description={t("defForm.reminderLeadDaysDescription")}
          placeholder={t("defForm.reminderLeadDaysPlaceholder")}
          value={form.reminder_lead_days === "" ? "" : Number(form.reminder_lead_days)}
          onChange={(v) => setForm((f) => ({ ...f, reminder_lead_days: v === "" ? "" : String(Math.round(Number(v))) }))}
          min={0}
          allowDecimal={false}
          suffix=" days"
          data-testid="def-reminder-lead-days-input"
        />
        <CustomFieldsEditor
          value={form.custom_fields}
          onChange={(v) => setForm((f) => ({ ...f, custom_fields: v }))}
          disabled={busy}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose} disabled={busy}>
            {t("common:actions.cancel", "Cancel")}
          </Button>
          <Button
            onClick={onSubmit}
            loading={busy}
            disabled={!form.name.trim()}
            data-testid="def-submit-btn"
          >
            {t("common:actions.save", "Save")}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// ── Items page (definition list) ──────────────────────────────────────────────

export function Items() {
  const { t } = useTranslation("items");
  const { t: tTags } = useTranslation("tags");
  const [definitions, setDefinitions] = useState<DefinitionResponse[]>([]);
  const [kinds, setKinds] = useState<KindResponse[]>([]);
  const [categories, setCategories] = useState<CategoryResponse[]>([]);
  const [locations, setLocations] = useState<LocationResponse[]>([]);
  const [tags, setTags] = useState<TagResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");
  const [tagFilter, setTagFilter] = useState<string>("");
  // Cache: definitionId → tagIds[] — populated lazily when tagFilter is active.
  const [tagLinkCache, setTagLinkCache] = useState<Map<number, number[]>>(new Map());

  const [defModal, setDefModal] = useState<DefModalState>({ kind: "none" });
  const [defForm, setDefForm] = useState<DefinitionFormState>(emptyDefForm());
  const [defBusy, setDefBusy] = useState(false);
  const [defError, setDefError] = useState<string | null>(null);

  // Barcode scan modal state (items list scan entry point).
  const [scanOpen, setScanOpen] = useState(false);
  // Code to bind after a new definition is created via the scan-unknown flow.
  const [pendingBarcode, setPendingBarcode] = useState<string | null>(null);

  // Load all reference data on mount
  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [defsRes, kindsRes, catsRes, locsRes, tagsRes] = await Promise.all([
        client.GET("/api/definitions", { params: { query: {} } }),
        client.GET("/api/kinds"),
        client.GET("/api/categories", { params: { query: {} } }),
        client.GET("/api/locations", { params: { query: {} } }),
        client.GET("/api/tags"),
      ]);
      if (defsRes.error) {
        setLoadError(t("loadError"));
        return;
      }
      setDefinitions(defsRes.data ?? []);
      setKinds(kindsRes.data ?? []);
      setCategories(catsRes.data ?? []);
      setLocations(locsRes.data ?? []);
      setTags(tagsRes.data ?? []);
    } finally {
      setLoading(false);
    }
  }, [t]);

  // Re-search definitions when q or category filter changes
  const searchDefinitions = useCallback(async () => {
    const params: { q?: string; category_id?: number } = {};
    if (q.trim()) params.q = q.trim();
    if (categoryFilter) params.category_id = Number(categoryFilter);

    const { data, error } = await client.GET("/api/definitions", {
      params: { query: params },
    });
    if (!error && data) setDefinitions(data);
  }, [q, categoryFilter]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (!loading) {
      searchDefinitions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, categoryFilter]);

  // When tagFilter is active, populate the cache for any uncached definitions.
  // Running when tagLinkCache updates is safe: the second run finds no uncached
  // IDs (already fetched) and exits immediately — no infinite loop.
  useEffect(() => {
    if (!tagFilter) return;

    const uncachedIds = definitions
      .filter((d) => !tagLinkCache.has(d.id))
      .map((d) => d.id);

    if (uncachedIds.length === 0) return;

    void Promise.all(
      uncachedIds.map((id) =>
        client.GET("/api/tags/links", {
          params: { query: { model_type: "item_definition", model_id: id } },
        }),
      ),
    ).then((results) => {
      setTagLinkCache((prev) => {
        const next = new Map(prev);
        results.forEach((res, idx) => {
          const tagIds = (res.data ?? []).map((link) => link.tag_id);
          next.set(uncachedIds[idx], tagIds);
        });
        return next;
      });
    });
  }, [tagFilter, definitions, tagLinkCache]);

  // Client-side tag filter applied on top of the backend-filtered definitions.
  const displayDefinitions = useMemo(() => {
    if (!tagFilter) return definitions;
    const tagId = Number(tagFilter);
    return definitions.filter((d) => {
      const cached = tagLinkCache.get(d.id);
      if (cached === undefined) return false; // not yet loaded
      return cached.includes(tagId);
    });
  }, [definitions, tagFilter, tagLinkCache]);

  // ── Definition CRUD ──────────────────────────────────────────────────────────

  function openCreateDef() {
    setPendingBarcode(null);
    setDefForm(emptyDefForm());
    setDefError(null);
    setDefModal({ kind: "create" });
  }

  /** Open the create-definition modal with a scanned code pre-bound after save. */
  function openCreateDefWithCode(code: string) {
    setPendingBarcode(code);
    setDefForm(emptyDefForm());
    setDefError(null);
    setDefModal({ kind: "create" });
  }

  function openEditDef(def: DefinitionResponse) {
    setDefForm({
      name: def.name,
      description: def.description ?? "",
      category_id: def.category_id != null ? String(def.category_id) : "",
      kind_id: String(def.kind_id),
      unit: def.unit,
      default_location_id:
        def.default_location_id != null ? String(def.default_location_id) : "",
      stock_tracking_mode: def.stock_tracking_mode ?? "exact",
      min_stock: def.min_stock != null ? String(def.min_stock) : "",
      default_best_before_days:
        def.default_best_before_days != null ? String(def.default_best_before_days) : "",
      reminder_lead_days:
        def.reminder_lead_days != null ? String(def.reminder_lead_days) : "",
      custom_fields: def.custom_fields ?? null,
    });
    setDefError(null);
    setDefModal({ kind: "edit", def });
  }

  function openDeleteDef(def: DefinitionResponse) {
    setDefError(null);
    setDefModal({ kind: "delete", def });
  }

  function closeDefModal() {
    setDefModal({ kind: "none" });
    setDefError(null);
  }

  async function handleCreateDef() {
    if (!defForm.name.trim()) return;
    setDefBusy(true);
    setDefError(null);
    try {
      const { data: newDef, error } = await client.POST("/api/definitions", {
        body: {
          name: defForm.name.trim(),
          description: defForm.description.trim() || null,
          category_id: defForm.category_id ? Number(defForm.category_id) : null,
          kind_id: defForm.kind_id ? Number(defForm.kind_id) : null,
          unit: defForm.unit.trim() || "pcs",
          default_location_id: defForm.default_location_id
            ? Number(defForm.default_location_id)
            : null,
          stock_tracking_mode: defForm.stock_tracking_mode,
          min_stock:
            defForm.stock_tracking_mode === "exact" && defForm.min_stock !== ""
              ? defForm.min_stock
              : null,
          default_best_before_days:
            defForm.default_best_before_days !== ""
              ? Number(defForm.default_best_before_days)
              : null,
          reminder_lead_days:
            defForm.reminder_lead_days !== ""
              ? Number(defForm.reminder_lead_days)
              : null,
          custom_fields: defForm.custom_fields ?? null,
        },
      });
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      // Bind the pending barcode (from scan-unknown flow) after the definition
      // is created.  Failure is best-effort — we still close the modal.
      if (pendingBarcode && newDef) {
        await client.POST("/api/definitions/{definition_id}/barcodes", {
          params: { path: { definition_id: newDef.id } },
          body: { code: pendingBarcode, symbology: "unknown", label: null },
        });
        setPendingBarcode(null);
      }
      closeDefModal();
      notifySuccess(t("success.created"));
      await searchDefinitions();
    } finally {
      setDefBusy(false);
    }
  }

  async function handleEditDef() {
    if (defModal.kind !== "edit") return;
    if (!defForm.name.trim()) return;
    setDefBusy(true);
    setDefError(null);
    try {
      const { error } = await client.PATCH(
        "/api/definitions/{definition_id}",
        {
          params: { path: { definition_id: defModal.def.id } },
          body: {
            name: defForm.name.trim(),
            description: defForm.description.trim() || null,
            category_id: defForm.category_id ? Number(defForm.category_id) : null,
            kind_id: defForm.kind_id ? Number(defForm.kind_id) : null,
            unit: defForm.unit.trim() || "pcs",
            default_location_id: defForm.default_location_id
              ? Number(defForm.default_location_id)
              : null,
            stock_tracking_mode: defForm.stock_tracking_mode || null,
            min_stock:
              defForm.stock_tracking_mode === "exact" && defForm.min_stock !== ""
                ? defForm.min_stock
                : null,
            default_best_before_days:
              defForm.default_best_before_days !== ""
                ? Number(defForm.default_best_before_days)
                : null,
            reminder_lead_days:
              defForm.reminder_lead_days !== ""
                ? Number(defForm.reminder_lead_days)
                : null,
            custom_fields: defForm.custom_fields ?? null,
          },
        },
      );
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      closeDefModal();
      notifySuccess(t("success.updated"));
      await searchDefinitions();
    } finally {
      setDefBusy(false);
    }
  }

  async function handleDeleteDef() {
    if (defModal.kind !== "delete") return;
    setDefBusy(true);
    setDefError(null);
    try {
      const { error } = await client.DELETE(
        "/api/definitions/{definition_id}",
        {
          params: { path: { definition_id: defModal.def.id } },
        },
      );
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      closeDefModal();
      notifySuccess(t("success.deleted"));
      await searchDefinitions();
    } finally {
      setDefBusy(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;

  const categoryFilterOptions = [
    { value: "", label: t("search.allCategories") },
    ...categories.map((c) => ({ value: String(c.id), label: c.name })),
  ];

  const tagFilterOptions = tags.map((tag) => ({
    value: String(tag.id),
    label: tag.name,
  }));

  return (
    <PageShell title={t("page.title")} subtitle={t("page.subtitle")}>
      <Stack gap="md">
        {/* Search + category filter + tag filter + create button */}
        <Group wrap="nowrap" align="flex-end">
          <TextInput
            placeholder={t("search.placeholder")}
            leftSection={<Search size={14} />}
            value={q}
            onChange={(e) => setQ(e.currentTarget.value)}
            style={{ flex: 1 }}
            data-testid="def-search-input"
          />
          <Select
            data={categoryFilterOptions}
            value={categoryFilter}
            onChange={(v) => setCategoryFilter(v ?? "")}
            placeholder={t("search.allCategories")}
            style={{ minWidth: 160 }}
            data-testid="def-category-filter"
          />
          <Select
            data={tagFilterOptions}
            value={tagFilter || null}
            onChange={(v) => {
              setTagFilter(v ?? "");
            }}
            placeholder={tTags("filter.placeholder")}
            style={{ minWidth: 140 }}
            clearable
            data-testid="tag-filter-select"
          />
          <Button
            leftSection={<Zap size={14} />}
            onClick={() => setScanOpen(true)}
            variant="light"
            data-testid="scan-barcode-btn"
          >
            {t("barcode:scanBtn")}
          </Button>
          <Button
            leftSection={<Plus size={14} />}
            onClick={openCreateDef}
            data-testid="create-def-btn"
          >
            {t("list.newItemBtn")}
          </Button>
        </Group>

        {/* Definition list */}
        {displayDefinitions.length === 0 ? (
          <EmptyState message={t("list.empty")} />
        ) : (
          <Table.ScrollContainer minWidth={480}>
            <Table highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t("list.colName")}</Table.Th>
                  <Table.Th>{t("list.colKind")}</Table.Th>
                  <Table.Th>{t("list.colUnit")}</Table.Th>
                  <Table.Th>{t("list.colCategory")}</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {displayDefinitions.map((def) => (
                  <Table.Tr key={def.id} data-testid={`def-row-${def.id}`}>
                    <Table.Td>
                      <Anchor component={Link} to={`/items/${def.id}`} size="sm" fw={500}>
                        {def.name}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="light">
                        {t(`kinds.${def.kind.code}`, { defaultValue: def.kind.name })}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{def.unit}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed">
                        {def.category_id != null
                          ? (categories.find((c) => c.id === def.category_id)?.name ?? "—")
                          : "—"}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} justify="flex-end" wrap="nowrap">
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          aria-label={t("list.editAriaLabel", { name: def.name })}
                          onClick={() => openEditDef(def)}
                          data-testid={`edit-def-${def.id}`}
                        >
                          <Edit2 size={14} />
                        </ActionIcon>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          color="red"
                          aria-label={t("list.deleteAriaLabel", { name: def.name })}
                          onClick={() => openDeleteDef(def)}
                          data-testid={`delete-def-${def.id}`}
                        >
                          <Trash2 size={14} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>

      {/* Create definition modal */}
      <DefinitionFormModal
        opened={defModal.kind === "create"}
        title={t("defForm.createTitle")}
        form={defForm}
        setForm={setDefForm}
        onSubmit={handleCreateDef}
        onClose={closeDefModal}
        busy={defBusy}
        error={defError}
        kinds={kinds}
        categories={categories}
        locations={locations}
      />

      {/* Edit definition modal */}
      <DefinitionFormModal
        opened={defModal.kind === "edit"}
        title={t("defForm.editTitle")}
        form={defForm}
        setForm={setDefForm}
        onSubmit={handleEditDef}
        onClose={closeDefModal}
        busy={defBusy}
        error={defError}
        kinds={kinds}
        categories={categories}
        locations={locations}
      />

      {/* Delete definition modal */}
      <Modal
        opened={defModal.kind === "delete"}
        onClose={closeDefModal}
        title={t("deleteDefModal.title")}
        size="sm"
      >
        <Stack gap="sm">
          {defError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {defError}
            </Alert>
          )}
          {!defError && (
            <Text size="sm">
              <Trans
                i18nKey="deleteDefModal.confirmation"
                ns="items"
                values={{ name: defModal.kind === "delete" ? defModal.def.name : "" }}
                components={{ bold: <b /> }}
              />
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDefModal} disabled={defBusy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!defError && (
              <Button
                color="red"
                onClick={handleDeleteDef}
                loading={defBusy}
                data-testid="confirm-delete-def-btn"
              >
                {t("common:actions.delete", "Delete")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {/* Barcode scan modal — scan entry point on the items list */}
      <BarcodeScanModal
        opened={scanOpen}
        onClose={() => setScanOpen(false)}
        onCreateWithCode={openCreateDefWithCode}
      />
    </PageShell>
  );
}

// ── ItemDetail page (definition detail + instances) ───────────────────────────

// ── Ledger action modal state ─────────────────────────────────────────────────

type LedgerActionKind = "intake" | "discard" | "adjust" | "move";

interface LedgerActionState {
  kind: LedgerActionKind;
  instanceId: number;
}

interface ConsumeFormState {
  quantity: string;
  note: string;
}

interface LedgerActionFormState {
  quantity: string;
  note: string;
  to_location_id: string; // for move
}

// ── ItemDetail ────────────────────────────────────────────────────────────────

export function ItemDetail() {
  const { t } = useTranslation("items");
  const { t: tStock } = useTranslation("stock");
  const { t: tInst } = useTranslation("instances");
  const { t: tCF } = useTranslation("customFields");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const defId = Number(id);

  const [def, setDef] = useState<DefinitionResponse | null>(null);
  const [instances, setInstances] = useState<InstanceResponse[]>([]);
  const [kinds, setKinds] = useState<KindResponse[]>([]);
  const [categories, setCategories] = useState<CategoryResponse[]>([]);
  const [locations, setLocations] = useState<LocationResponse[]>([]);
  const [allDefs, setAllDefs] = useState<DefinitionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Instance search
  const [instanceQ, setInstanceQ] = useState("");

  // Definition edit/delete modal
  const [defModal, setDefModal] = useState<DefModalState>({ kind: "none" });
  const [defForm, setDefForm] = useState<DefinitionFormState>(emptyDefForm());
  const [defBusy, setDefBusy] = useState(false);
  const [defError, setDefError] = useState<string | null>(null);

  // Instance modal
  const [instModal, setInstModal] = useState<InstModalState>({ kind: "none" });
  const [instForm, setInstForm] = useState<InstanceFormState>(
    emptyInstanceForm(defId),
  );
  const [instBusy, setInstBusy] = useState(false);
  const [instError, setInstError] = useState<string | null>(null);

  // Barcode scan modal (intake scan entry point on the detail page)
  const [detailScanOpen, setDetailScanOpen] = useState(false);

  // Consume (FIFO) modal
  const [consumeOpen, setConsumeOpen] = useState(false);
  const [consumeForm, setConsumeForm] = useState<ConsumeFormState>({ quantity: "", note: "" });
  const [consumeBusy, setConsumeBusy] = useState(false);
  const [consumeError, setConsumeError] = useState<string | null>(null);

  // Per-lot ledger action modal (intake / discard / adjust / move)
  const [ledgerAction, setLedgerAction] = useState<LedgerActionState | null>(null);
  const [ledgerForm, setLedgerForm] = useState<LedgerActionFormState>({ quantity: "", note: "", to_location_id: "" });
  const [ledgerBusy, setLedgerBusy] = useState(false);
  const [ledgerError, setLedgerError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [defRes, instsRes, kindsRes, catsRes, locsRes, allDefsRes] =
        await Promise.all([
          client.GET("/api/definitions/{definition_id}", {
            params: { path: { definition_id: defId } },
          }),
          client.GET("/api/instances", {
            params: { query: { definition_id: defId } },
          }),
          client.GET("/api/kinds"),
          client.GET("/api/categories", { params: { query: {} } }),
          client.GET("/api/locations", { params: { query: {} } }),
          client.GET("/api/definitions", { params: { query: {} } }),
        ]);
      if (defRes.error) {
        setLoadError(t("notFound"));
        return;
      }
      setDef(defRes.data ?? null);
      setInstances(instsRes.data ?? []);
      setKinds(kindsRes.data ?? []);
      setCategories(catsRes.data ?? []);
      setLocations(locsRes.data ?? []);
      setAllDefs(allDefsRes.data ?? []);
    } finally {
      setLoading(false);
    }
  }, [defId, t]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Search instances
  const searchInstances = useCallback(async () => {
    const params: { q?: string; definition_id?: number } = {
      definition_id: defId,
    };
    if (instanceQ.trim()) params.q = instanceQ.trim();
    const { data, error } = await client.GET("/api/instances", {
      params: { query: params },
    });
    if (!error && data) setInstances(data);
  }, [defId, instanceQ]);

  useEffect(() => {
    if (!loading) {
      searchInstances();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instanceQ]);

  // ── Definition CRUD ──────────────────────────────────────────────────────────

  function openEditDef() {
    if (!def) return;
    setDefForm({
      name: def.name,
      description: def.description ?? "",
      category_id: def.category_id != null ? String(def.category_id) : "",
      kind_id: String(def.kind_id),
      unit: def.unit,
      default_location_id:
        def.default_location_id != null ? String(def.default_location_id) : "",
      stock_tracking_mode: def.stock_tracking_mode ?? "exact",
      min_stock: def.min_stock != null ? String(def.min_stock) : "",
      default_best_before_days:
        def.default_best_before_days != null ? String(def.default_best_before_days) : "",
      reminder_lead_days:
        def.reminder_lead_days != null ? String(def.reminder_lead_days) : "",
      custom_fields: def.custom_fields ?? null,
    });
    setDefError(null);
    setDefModal({ kind: "edit", def });
  }

  function closeDefModal() {
    setDefModal({ kind: "none" });
    setDefError(null);
  }

  async function handleEditDef() {
    if (defModal.kind !== "edit") return;
    if (!defForm.name.trim()) return;
    setDefBusy(true);
    setDefError(null);
    try {
      const { error } = await client.PATCH(
        "/api/definitions/{definition_id}",
        {
          params: { path: { definition_id: defModal.def.id } },
          body: {
            name: defForm.name.trim(),
            description: defForm.description.trim() || null,
            category_id: defForm.category_id ? Number(defForm.category_id) : null,
            kind_id: defForm.kind_id ? Number(defForm.kind_id) : null,
            unit: defForm.unit.trim() || "pcs",
            default_location_id: defForm.default_location_id
              ? Number(defForm.default_location_id)
              : null,
            stock_tracking_mode: defForm.stock_tracking_mode || null,
            min_stock:
              defForm.stock_tracking_mode === "exact" && defForm.min_stock !== ""
                ? defForm.min_stock
                : null,
            default_best_before_days:
              defForm.default_best_before_days !== ""
                ? Number(defForm.default_best_before_days)
                : null,
            reminder_lead_days:
              defForm.reminder_lead_days !== ""
                ? Number(defForm.reminder_lead_days)
                : null,
            custom_fields: defForm.custom_fields ?? null,
          },
        },
      );
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      closeDefModal();
      notifySuccess(t("success.updated"));
      await loadAll();
    } finally {
      setDefBusy(false);
    }
  }

  async function handleDeleteDef() {
    if (!def) return;
    setDefBusy(true);
    setDefError(null);
    try {
      const { error } = await client.DELETE(
        "/api/definitions/{definition_id}",
        {
          params: { path: { definition_id: def.id } },
        },
      );
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      notifySuccess(t("success.deleted"));
      navigate("/items");
    } finally {
      setDefBusy(false);
    }
  }

  // ── Instance CRUD ──────────────────────────────────────────────────────────

  function openCreateInst() {
    const form = emptyInstanceForm(defId);
    // Pre-fill best_before_date from the definition's default shelf life (M3).
    // The user can override or clear; the server also auto-computes on create when omitted.
    if (def?.default_best_before_days != null) {
      const d = new Date();
      d.setUTCDate(d.getUTCDate() + def.default_best_before_days);
      const y = d.getUTCFullYear();
      const m = String(d.getUTCMonth() + 1).padStart(2, "0");
      const day = String(d.getUTCDate()).padStart(2, "0");
      form.best_before_date = `${y}-${m}-${day}`;
    }
    setInstForm(form);
    setInstError(null);
    setInstModal({ kind: "create", definitionId: defId });
  }

  function openEditInst(inst: InstanceResponse) {
    setInstForm({
      definition_id: String(inst.definition_id),
      location_id: inst.location_id != null ? String(inst.location_id) : "",
      quantity: inst.quantity ?? "1",
      stock_level: inst.stock_level ?? "",
      serial: inst.serial ?? "",
      model_number: inst.model_number ?? "",
      manufacturer: inst.manufacturer ?? "",
      best_before_date: inst.best_before_date ?? "",
      warranty_expires: inst.warranty_expires ?? "",
      warranty_details: inst.warranty_details ?? "",
      purchase_price: inst.purchase_price ?? "",
      purchase_date: inst.purchase_date ?? "",
      purchase_source: inst.purchase_source ?? "",
      custom_fields: inst.custom_fields ?? null,
    });
    setInstError(null);
    setInstModal({ kind: "edit", inst });
  }

  function openDeleteInst(inst: InstanceResponse) {
    setInstError(null);
    setInstModal({ kind: "delete", inst });
  }

  function closeInstModal() {
    setInstModal({ kind: "none" });
    setInstError(null);
  }

  async function handleCreateInst() {
    setInstBusy(true);
    setInstError(null);
    try {
      const serial = instForm.serial.trim() || null;
      const mode = def?.stock_tracking_mode ?? "exact";
      // For exact mode: use quantity (forced to 1 when serial is set).
      // For level/none mode: no quantity sent.
      const qty = mode === "exact" ? (serial != null ? "1" : instForm.quantity) : undefined;
      const stockLevel = mode === "level" ? (instForm.stock_level || null) : undefined;
      const { error } = await client.POST("/api/instances", {
        body: {
          definition_id: Number(instForm.definition_id),
          location_id: instForm.location_id ? Number(instForm.location_id) : null,
          quantity: qty,
          stock_level: stockLevel,
          serial,
          model_number: instForm.model_number.trim() || null,
          manufacturer: instForm.manufacturer.trim() || null,
          best_before_date: instForm.best_before_date.trim() || null,
          warranty_expires: instForm.warranty_expires.trim() || null,
          warranty_details: instForm.warranty_details.trim() || null,
          purchase_price: instForm.purchase_price.trim() || null,
          purchase_date: instForm.purchase_date.trim() || null,
          purchase_source: instForm.purchase_source.trim() || null,
          custom_fields: instForm.custom_fields ?? null,
        },
      });
      if (error) {
        setInstError(mapApiError(error));
        return;
      }
      closeInstModal();
      notifySuccess(t("success.instanceCreated"));
      await loadAll();
    } finally {
      setInstBusy(false);
    }
  }

  async function handleEditInst() {
    if (instModal.kind !== "edit") return;
    setInstBusy(true);
    setInstError(null);
    try {
      const serial = instForm.serial.trim() || null;
      const mode = def?.stock_tracking_mode ?? "exact";
      const stockLevel = mode === "level" ? (instForm.stock_level || null) : undefined;
      const { error } = await client.PATCH(
        "/api/instances/{instance_id}",
        {
          params: { path: { instance_id: instModal.inst.id } },
          body: {
            location_id: instForm.location_id ? Number(instForm.location_id) : null,
            // quantity is intentionally absent (M2 §2): quantity changes only
            // through the movement ledger (intake / discard / adjust / consume).
            stock_level: stockLevel,
            serial,
            model_number: instForm.model_number.trim() || null,
            manufacturer: instForm.manufacturer.trim() || null,
            best_before_date: instForm.best_before_date.trim() || null,
            warranty_expires: instForm.warranty_expires.trim() || null,
            warranty_details: instForm.warranty_details.trim() || null,
            purchase_price: instForm.purchase_price.trim() || null,
            purchase_date: instForm.purchase_date.trim() || null,
            purchase_source: instForm.purchase_source.trim() || null,
            custom_fields: instForm.custom_fields ?? null,
          },
        },
      );
      if (error) {
        setInstError(mapApiError(error));
        return;
      }
      closeInstModal();
      notifySuccess(t("success.instanceUpdated"));
      await loadAll();
    } finally {
      setInstBusy(false);
    }
  }

  async function handleDeleteInst() {
    if (instModal.kind !== "delete") return;
    setInstBusy(true);
    setInstError(null);
    try {
      const { error } = await client.DELETE(
        "/api/instances/{instance_id}",
        {
          params: { path: { instance_id: instModal.inst.id } },
        },
      );
      if (error) {
        setInstError(mapApiError(error));
        return;
      }
      closeInstModal();
      notifySuccess(t("success.instanceDeleted"));
      await loadAll();
    } finally {
      setInstBusy(false);
    }
  }

  // ── Consume (FIFO) ───────────────────────────────────────────────────────────

  function openConsume() {
    setConsumeForm({ quantity: "", note: "" });
    setConsumeError(null);
    setConsumeOpen(true);
  }

  function closeConsume() {
    setConsumeOpen(false);
    setConsumeError(null);
  }

  async function handleConsume() {
    if (!consumeForm.quantity.trim()) return;
    setConsumeBusy(true);
    setConsumeError(null);
    try {
      const { error } = await client.POST(
        "/api/definitions/{definition_id}/consume",
        {
          params: { path: { definition_id: defId } },
          body: {
            quantity: consumeForm.quantity,
            note: consumeForm.note.trim() || null,
          },
        },
      );
      if (error) {
        setConsumeError(mapApiError(error));
        return;
      }
      closeConsume();
      notifySuccess(tInst("success.consume"));
      await loadAll();
    } finally {
      setConsumeBusy(false);
    }
  }

  // ── Per-lot ledger actions ────────────────────────────────────────────────────

  function openLedgerAction(kind: LedgerActionKind, instanceId: number) {
    setLedgerForm({ quantity: "", note: "", to_location_id: "" });
    setLedgerError(null);
    setLedgerAction({ kind, instanceId });
  }

  function closeLedgerAction() {
    setLedgerAction(null);
    setLedgerError(null);
  }

  async function handleLedgerAction() {
    if (!ledgerAction) return;
    const { kind, instanceId } = ledgerAction;
    setLedgerBusy(true);
    setLedgerError(null);
    try {
      let error: unknown = null;
      if (kind === "intake") {
        const res = await client.POST("/api/instances/{instance_id}/intake", {
          params: { path: { instance_id: instanceId } },
          body: {
            quantity: ledgerForm.quantity,
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      } else if (kind === "discard") {
        const res = await client.POST("/api/instances/{instance_id}/discard", {
          params: { path: { instance_id: instanceId } },
          body: {
            quantity: ledgerForm.quantity,
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      } else if (kind === "adjust") {
        const res = await client.POST("/api/instances/{instance_id}/adjust", {
          params: { path: { instance_id: instanceId } },
          body: {
            quantity: ledgerForm.quantity,
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      } else if (kind === "move") {
        if (!ledgerForm.to_location_id) return;
        const res = await client.POST("/api/instances/{instance_id}/move", {
          params: { path: { instance_id: instanceId } },
          body: {
            to_location_id: Number(ledgerForm.to_location_id),
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      }
      if (error) {
        setLedgerError(mapApiError(error));
        return;
      }
      closeLedgerAction();
      const successKey = kind as keyof typeof tInst;
      notifySuccess(tInst(`success.${successKey}` as never, { defaultValue: "Done." }));
      await loadAll();
    } finally {
      setLedgerBusy(false);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;
  if (!def) return <ErrorState message={t("notFound")} />;

  // Low-stock badge: client-side derived from loaded instances vs min_stock.
  // Rationale: instances are already loaded; using them avoids an extra
  // GET /low-stock call. The Step-8 dashboard tile MUST use GET /low-stock
  // and must not re-derive; this per-definition badge uses in-memory data.
  const isLowStock =
    def.stock_tracking_mode === "exact" &&
    def.min_stock != null &&
    (() => {
      const total = instances.reduce((sum, inst) => {
        if (inst.quantity == null) return sum;
        return sum + parseFloat(inst.quantity);
      }, 0);
      return total < parseFloat(def.min_stock);
    })();

  // Data-driven column visibility: only show a column if at least one lot has a
  // value for that field. Qty, Location, and the actions column are always shown.
  const showSerial = instances.some((i) => !!i.serial);
  const showManufacturer = instances.some((i) => !!i.manufacturer);
  const showWarranty = instances.some((i) => !!i.warranty_expires);
  const showBestBefore = instances.some((i) => !!i.best_before_date);

  const catName =
    def.category_id != null
      ? (categories.find((c) => c.id === def.category_id)?.name ?? "—")
      : "—";
  const locName =
    def.default_location_id != null
      ? (locations.find((l) => l.id === def.default_location_id)?.name ?? "—")
      : "—";

  return (
    <Stack gap="lg">
      {/* Back link */}
      <Group>
        <Anchor component={Link} to="/items" size="sm" c="dimmed">
          <Group gap={4}>
            <ArrowLeft size={14} />
            {t("detail.backLink")}
          </Group>
        </Anchor>
      </Group>

      {/* Definition header + actions */}
      <Group justify="space-between" wrap="nowrap">
        <Title order={2}>{def.name}</Title>
        <Group gap={8}>
          <Button
            size="xs"
            variant="light"
            leftSection={<Edit2 size={12} />}
            onClick={openEditDef}
            data-testid="edit-def-btn"
          >
            {t("detail.editBtn")}
          </Button>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<Trash2 size={12} />}
            onClick={() => {
              setDefError(null);
              setDefModal({ kind: "delete", def });
            }}
            data-testid="delete-def-btn"
          >
            {t("detail.deleteBtn")}
          </Button>
        </Group>
      </Group>

      {/* Definition metadata card */}
      <Card>
        <Stack gap="sm">
          {def.description && (
            <Text size="sm" c="dimmed">
              {def.description}
            </Text>
          )}
          <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                {t("detail.kindLabel")}
              </Text>
              <Badge size="sm" variant="light" style={{ alignSelf: "flex-start" }}>
                {t(`kinds.${def.kind.code}`, { defaultValue: def.kind.name })}
              </Badge>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                {t("detail.unitLabel")}
              </Text>
              <Text size="sm">{def.unit}</Text>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                {t("detail.categoryLabel")}
              </Text>
              <Text size="sm">{catName}</Text>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                {t("detail.defaultLocationLabel")}
              </Text>
              <Text size="sm">{locName}</Text>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                {t("detail.trackingModeLabel")}
              </Text>
              <Badge
                size="sm"
                variant="outline"
                color={def.stock_tracking_mode === "exact" ? "blue" : def.stock_tracking_mode === "level" ? "teal" : "gray"}
                style={{ alignSelf: "flex-start" }}
                data-testid="def-tracking-mode-badge"
              >
                {tStock(`trackingMode.${def.stock_tracking_mode}`, {
                  defaultValue: def.stock_tracking_mode,
                })}
              </Badge>
            </Stack>
            {def.stock_tracking_mode === "exact" && def.min_stock != null && (
              <Stack gap={2}>
                <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                  {t("detail.minStockLabel")}
                </Text>
                <Text size="sm" data-testid="def-min-stock-value">
                  {formatQuantity(def.min_stock)}
                </Text>
              </Stack>
            )}
            {def.default_best_before_days != null && (
              <Stack gap={2}>
                <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                  {t("detail.defaultBestBeforeDaysLabel")}
                </Text>
                <Text size="sm" data-testid="def-default-best-before-days-value">
                  {t("expiry:shelfLifeDisplay", { days: def.default_best_before_days })}
                </Text>
              </Stack>
            )}
            {def.reminder_lead_days != null && (
              <Stack gap={2}>
                <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                  {t("detail.reminderLeadDaysLabel")}
                </Text>
                <Text size="sm" data-testid="def-reminder-lead-days-value">
                  {t("detail.reminderLeadDaysValue", { days: def.reminder_lead_days })}
                </Text>
              </Stack>
            )}
          </SimpleGrid>
          {def.custom_fields && Object.keys(def.custom_fields).length > 0 && (
            <>
              <Divider my="xs" />
              <Stack gap="xs">
                <Text size="xs" c="dimmed" fw={500} tt="uppercase">
                  {tCF("sectionTitle")}
                </Text>
                <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="xs">
                  {Object.entries(def.custom_fields).map(([key, val]) => (
                    <Stack key={key} gap={2} data-testid={`def-cf-display-${key}`}>
                      <Text size="xs" c="dimmed" fw={500}>{key}</Text>
                      <Text size="sm">
                        {val === null ? "—" : val === true ? "true" : val === false ? "false" : String(val)}
                      </Text>
                    </Stack>
                  ))}
                </SimpleGrid>
              </Stack>
            </>
          )}
        </Stack>
      </Card>

      <Divider />

      {/* Attachments */}
      <AttachmentPanel modelType="item_definition" modelId={defId} />

      <Divider />

      {/* Tags */}
      <TagPanel modelType="item_definition" modelId={defId} />

      <Divider />

      {/* Notes */}
      <NotePanel modelType="item_definition" modelId={defId} />

      <Divider />

      {/* Barcodes */}
      <BarcodePanel definitionId={defId} />

      <Divider />

      {/* Instances section */}
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap">
          <Group gap={8} align="center">
            <Title order={4}>{t("detail.instancesTitle")}</Title>
            {isLowStock && (
              <Badge size="sm" color="red" variant="filled" data-testid="low-stock-badge">
                {tStock("lowStockBadge")}
              </Badge>
            )}
          </Group>
          <Group gap={8}>
            {def.stock_tracking_mode === "exact" && (
              <Button
                size="xs"
                variant="light"
                color="orange"
                onClick={openConsume}
                data-testid="consume-btn"
              >
                {tStock("actions.consume")}
              </Button>
            )}
            <Button
              size="xs"
              leftSection={<Zap size={12} />}
              variant="light"
              onClick={() => setDetailScanOpen(true)}
              data-testid="detail-scan-btn"
            >
              {t("barcode:scanBtn")}
            </Button>
            <Button
              size="xs"
              leftSection={<Plus size={12} />}
              onClick={openCreateInst}
              data-testid="register-instance-btn"
            >
              {t("detail.registerInstanceBtn")}
            </Button>
          </Group>
        </Group>

        {/* Instance search */}
        <TextInput
          placeholder={t("detail.instanceSearchPlaceholder")}
          leftSection={<Search size={14} />}
          value={instanceQ}
          onChange={(e) => setInstanceQ(e.currentTarget.value)}
          data-testid="instance-search-input"
        />

        {instances.length === 0 ? (
          <EmptyState message={t("detail.instancesEmpty")} />
        ) : (
          <Table.ScrollContainer minWidth={560}>
            <Table highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  {showSerial && <Table.Th>{t("detail.colSerial")}</Table.Th>}
                  <Table.Th>{t("detail.colQty")}</Table.Th>
                  <Table.Th>{t("detail.colLocation")}</Table.Th>
                  {showManufacturer && <Table.Th>{t("detail.colManufacturer")}</Table.Th>}
                  {showWarranty && <Table.Th>{t("detail.colWarranty")}</Table.Th>}
                  {showBestBefore && <Table.Th>{t("detail.colBestBefore")}</Table.Th>}
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {instances.map((inst) => {
                  const rowAriaLabel = inst.serial
                    ? t("detail.lotRowAriaLabel", { serial: inst.serial })
                    : inst.best_before_date
                      ? t("detail.lotRowAriaLabelBestBefore", { date: inst.best_before_date })
                      : t("detail.lotRowAriaLabelId", { id: inst.id });
                  return (
                    <Table.Tr
                      key={inst.id}
                      data-testid={`inst-row-${inst.id}`}
                      onClick={() => navigate(`/instances/${inst.id}`)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          if (e.key === " ") e.preventDefault();
                          navigate(`/instances/${inst.id}`);
                        }
                      }}
                      tabIndex={0}
                      role="button"
                      aria-label={rowAriaLabel}
                      style={{ cursor: "pointer" }}
                    >
                      {showSerial && (
                        <Table.Td>
                          <Text size="sm" fw={500}>{inst.serial ?? <Text span c="dimmed" size="sm">—</Text>}</Text>
                        </Table.Td>
                      )}
                      <Table.Td>
                        {/* Render by mode: exact (or unset default) → quantity, level → badge, none → — */}
                        {(def.stock_tracking_mode ?? "exact") !== "level" && (def.stock_tracking_mode ?? "exact") !== "none" ? (
                          <Text size="sm" data-testid={`inst-qty-${inst.id}`}>{formatQuantity(inst.quantity)}</Text>
                        ) : def.stock_tracking_mode === "level" && inst.stock_level ? (
                          <Badge
                            size="sm"
                            color={inst.stock_level === "high" ? "green" : inst.stock_level === "medium" ? "yellow" : "red"}
                            variant="light"
                            data-testid={`inst-level-badge-${inst.id}`}
                          >
                            {tStock(`stockLevel.${inst.stock_level}`, { defaultValue: inst.stock_level })}
                          </Badge>
                        ) : (
                          <Text size="sm" c="dimmed">—</Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text size="sm" c="dimmed">
                          {inst.location_id != null
                            ? (locations.find((l) => l.id === inst.location_id)
                                ?.name ?? inst.location_id)
                            : "—"}
                        </Text>
                      </Table.Td>
                      {showManufacturer && (
                        <Table.Td>
                          <Text size="sm">{inst.manufacturer ?? "—"}</Text>
                        </Table.Td>
                      )}
                      {showWarranty && (
                        <Table.Td>
                          <Text size="sm">{inst.warranty_expires ? formatDate(inst.warranty_expires) : "—"}</Text>
                        </Table.Td>
                      )}
                      {showBestBefore && (
                        <Table.Td>
                          <Group gap={4} align="center" wrap="nowrap">
                            {inst.best_before_date ? (
                              <Text size="sm">{formatDate(inst.best_before_date)}</Text>
                            ) : (
                              <Text size="sm" c="dimmed">—</Text>
                            )}
                            <ExpiryBadge bestBeforeDate={inst.best_before_date} />
                          </Group>
                        </Table.Td>
                      )}
                      <Table.Td onClick={(e) => e.stopPropagation()}>
                        <Group gap={4} justify="flex-end" wrap="nowrap">
                          <ActionIcon
                            size="sm"
                            variant="subtle"
                            aria-label={t("detail.editInstanceAriaLabel", { id: inst.id })}
                            onClick={(e) => { e.stopPropagation(); openEditInst(inst); }}
                            data-testid={`edit-inst-${inst.id}`}
                          >
                            <Edit2 size={14} />
                          </ActionIcon>
                          {/* Per-lot ledger action menu (exact mode only) */}
                          {def.stock_tracking_mode === "exact" && (
                            <Menu shadow="md" position="bottom-end" withinPortal>
                              <Menu.Target>
                                <ActionIcon
                                  size="sm"
                                  variant="subtle"
                                  aria-label={tInst("detail.actionsTitle")}
                                  data-testid={`lot-actions-${inst.id}`}
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <MoreVertical size={14} />
                                </ActionIcon>
                              </Menu.Target>
                              <Menu.Dropdown>
                                <Menu.Item
                                  onClick={(e) => { e.stopPropagation(); openLedgerAction("intake", inst.id); }}
                                  data-testid={`lot-intake-${inst.id}`}
                                >
                                  {tStock("actions.intake")}
                                </Menu.Item>
                                <Menu.Item
                                  onClick={(e) => { e.stopPropagation(); openLedgerAction("adjust", inst.id); }}
                                  data-testid={`lot-adjust-${inst.id}`}
                                >
                                  {tStock("actions.adjust")}
                                </Menu.Item>
                                <Menu.Item
                                  onClick={(e) => { e.stopPropagation(); openLedgerAction("discard", inst.id); }}
                                  data-testid={`lot-discard-${inst.id}`}
                                >
                                  {tStock("actions.discard")}
                                </Menu.Item>
                                <Menu.Item
                                  onClick={(e) => { e.stopPropagation(); openLedgerAction("move", inst.id); }}
                                  data-testid={`lot-move-${inst.id}`}
                                >
                                  {tStock("actions.move")}
                                </Menu.Item>
                              </Menu.Dropdown>
                            </Menu>
                          )}
                          <ActionIcon
                            size="sm"
                            variant="subtle"
                            color="red"
                            aria-label={t("detail.deleteInstanceAriaLabel", { id: inst.id })}
                            onClick={(e) => { e.stopPropagation(); openDeleteInst(inst); }}
                            data-testid={`delete-inst-${inst.id}`}
                          >
                            <Trash2 size={14} />
                          </ActionIcon>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>

      {/* Consume (FIFO) modal */}
      <Modal opened={consumeOpen} onClose={closeConsume} title={tStock("consumeModal.title")} size="sm">
        <Stack gap="sm">
          {consumeError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="consume-error-alert">
              {consumeError}
            </Alert>
          )}
          <Text size="xs" c="dimmed">{tStock("consumeModal.hint")}</Text>
          <NumberInput
            label={tStock("consumeModal.quantityLabel")}
            value={consumeForm.quantity === "" ? "" : Number(consumeForm.quantity)}
            onChange={(v) => setConsumeForm((f) => ({ ...f, quantity: v === "" ? "" : String(v) }))}
            min={0}
            allowDecimal
            required
            data-testid="consume-quantity-input"
          />
          <TextInput
            label={tStock("consumeModal.noteLabel")}
            value={consumeForm.note}
            onChange={(e) => {
              const value = e.currentTarget.value;
              setConsumeForm((f) => ({ ...f, note: value }));
            }}
            data-testid="consume-note-input"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeConsume} disabled={consumeBusy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={handleConsume}
              loading={consumeBusy}
              disabled={!consumeForm.quantity}
              data-testid="consume-submit-btn"
            >
              {tStock("actions.consume")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Per-lot ledger action modal */}
      {ledgerAction && (
        <Modal
          opened={!!ledgerAction}
          onClose={closeLedgerAction}
          title={tStock(`${ledgerAction.kind}Modal.title` as never, { defaultValue: ledgerAction.kind })}
          size="sm"
        >
          <Stack gap="sm">
            {ledgerError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="ledger-error-alert">
                {ledgerError}
              </Alert>
            )}
            {ledgerAction.kind === "adjust" && (
              <Text size="xs" c="dimmed">{tStock("adjustModal.hint")}</Text>
            )}
            {ledgerAction.kind !== "move" && (
              <NumberInput
                label={tStock(`${ledgerAction.kind}Modal.quantityLabel` as never, { defaultValue: "Quantity" })}
                value={ledgerForm.quantity === "" ? "" : Number(ledgerForm.quantity)}
                onChange={(v) => setLedgerForm((f) => ({ ...f, quantity: v === "" ? "" : String(v) }))}
                min={0}
                allowDecimal
                required
                data-testid="ledger-quantity-input"
              />
            )}
            {ledgerAction.kind === "move" && (
              <Select
                label={tStock("moveModal.locationLabel")}
                data={locations.map((l) => ({ value: String(l.id), label: l.name }))}
                value={ledgerForm.to_location_id}
                onChange={(v) => setLedgerForm((f) => ({ ...f, to_location_id: v ?? "" }))}
                required
                data-testid="ledger-location-select"
              />
            )}
            <TextInput
              label={tStock(`${ledgerAction.kind}Modal.noteLabel` as never, { defaultValue: "Note (optional)" })}
              value={ledgerForm.note}
              onChange={(e) => {
                const value = e.currentTarget.value;
                setLedgerForm((f) => ({ ...f, note: value }));
              }}
              data-testid="ledger-note-input"
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={closeLedgerAction} disabled={ledgerBusy}>
                {t("common:actions.cancel", "Cancel")}
              </Button>
              <Button
                onClick={handleLedgerAction}
                loading={ledgerBusy}
                disabled={
                  ledgerAction.kind !== "move"
                    ? !ledgerForm.quantity
                    : !ledgerForm.to_location_id
                }
                data-testid="ledger-submit-btn"
              >
                {tStock(`actions.${ledgerAction.kind}` as never, { defaultValue: ledgerAction.kind })}
              </Button>
            </Group>
          </Stack>
        </Modal>
      )}

      {/* Edit definition modal */}
      <DefinitionFormModal
        opened={defModal.kind === "edit"}
        title={t("defForm.editTitle")}
        form={defForm}
        setForm={setDefForm}
        onSubmit={handleEditDef}
        onClose={closeDefModal}
        busy={defBusy}
        error={defError}
        kinds={kinds}
        categories={categories}
        locations={locations}
      />

      {/* Delete definition modal */}
      <Modal
        opened={defModal.kind === "delete"}
        onClose={closeDefModal}
        title={t("deleteDefModal.title")}
        size="sm"
      >
        <Stack gap="sm">
          {defError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {defError}
            </Alert>
          )}
          {!defError && (
            <Text size="sm">
              <Trans
                i18nKey="deleteDefModal.confirmationWithInstances"
                ns="items"
                values={{ name: def.name }}
                components={{ bold: <b /> }}
              />
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDefModal} disabled={defBusy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!defError && (
              <Button
                color="red"
                onClick={handleDeleteDef}
                loading={defBusy}
                data-testid="confirm-delete-def-btn"
              >
                {t("common:actions.delete", "Delete")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {/* Create instance modal */}
      <InstanceFormModal
        opened={instModal.kind === "create"}
        title={t("instanceForm.createTitle")}
        form={instForm}
        setForm={setInstForm}
        onSubmit={handleCreateInst}
        onClose={closeInstModal}
        busy={instBusy}
        error={instError}
        definitions={allDefs}
        locations={locations}
        lockDefinition
        trackingMode={def?.stock_tracking_mode ?? "exact"}
        isEdit={false}
        definitionDefaultBestBeforeDays={def?.default_best_before_days}
      />

      {/* Edit instance modal */}
      <InstanceFormModal
        opened={instModal.kind === "edit"}
        title={t("instanceForm.editTitle")}
        form={instForm}
        setForm={setInstForm}
        onSubmit={handleEditInst}
        onClose={closeInstModal}
        busy={instBusy}
        error={instError}
        definitions={allDefs}
        locations={locations}
        lockDefinition
        trackingMode={def?.stock_tracking_mode ?? "exact"}
        isEdit={true}
        definitionDefaultBestBeforeDays={def?.default_best_before_days}
      />

      {/* Delete instance modal */}
      <Modal
        opened={instModal.kind === "delete"}
        onClose={closeInstModal}
        title={t("deleteInstanceModal.title")}
        size="sm"
      >
        <Stack gap="sm">
          {instError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {instError}
            </Alert>
          )}
          {!instError && (
            <Text size="sm">
              <Trans
                i18nKey="deleteInstanceModal.confirmation"
                ns="items"
                values={{ id: instModal.kind === "delete" ? instModal.inst.id : "" }}
                components={{ bold: <b /> }}
              />
            </Text>
          )}
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={closeInstModal}
              disabled={instBusy}
            >
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!instError && (
              <Button
                color="red"
                onClick={handleDeleteInst}
                loading={instBusy}
                data-testid="confirm-delete-inst-btn"
              >
                {t("common:actions.delete", "Delete")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {/* Barcode scan modal — intake/instance-create scan entry point */}
      <BarcodeScanModal
        opened={detailScanOpen}
        onClose={() => setDetailScanOpen(false)}
        onAddLot={(definitionId) => {
          if (definitionId === defId) {
            openCreateInst();
          } else {
            navigate(`/items/${definitionId}`);
          }
        }}
      />
    </Stack>
  );
}
