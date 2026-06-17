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
 */
import { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Stack,
  Group,
  Text,
  Title,
  Button,
  TextInput,
  Textarea,
  Select,
  Modal,
  Alert,
  Table,
  Badge,
  Anchor,
  Divider,
  ActionIcon,
} from "@mantine/core";
import { Plus, Edit2, Trash2, AlertCircle, ArrowLeft, Search } from "react-feather";
import { useTranslation, Trans } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import type { components } from "../api/schema";
import { PageShell } from "../components/PageShell";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import {
  InstanceFormModal,
  type InstanceFormState,
} from "../components/InstanceFormModal";
import { formatQuantity } from "../utils";

// ── Schema types ─────────────────────────────────────────────────────────────

type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type InstanceResponse = components["schemas"]["InstanceResponse"];
type KindResponse = components["schemas"]["KindResponse"];
type CategoryResponse = components["schemas"]["CategoryResponse"];
type LocationResponse = components["schemas"]["LocationResponse"];

// ── Definition form state ────────────────────────────────────────────────────

interface DefinitionFormState {
  name: string;
  description: string;
  category_id: string; // select value (id as string or "")
  kind_id: string;
  unit: string;
  default_location_id: string;
}

const emptyDefForm = (): DefinitionFormState => ({
  name: "",
  description: "",
  category_id: "",
  kind_id: "",
  unit: "pcs",
  default_location_id: "",
});

const emptyInstanceForm = (definitionId?: number): InstanceFormState => ({
  definition_id: definitionId != null ? String(definitionId) : "",
  location_id: "",
  quantity: "1",
  serial: "",
  model_number: "",
  manufacturer: "",
  warranty_expires: "",
  warranty_details: "",
  purchase_price: "",
  purchase_date: "",
  purchase_source: "",
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
  const kindOptions = kinds.map((k) => ({
    value: String(k.id),
    label: k.name,
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
          onChange={(e) => setForm((f) => ({ ...f, name: e.currentTarget.value }))}
          data-autofocus
          data-testid="def-name-input"
        />
        <Textarea
          label={t("defForm.descriptionLabel")}
          value={form.description}
          onChange={(e) =>
            setForm((f) => ({ ...f, description: e.currentTarget.value }))
          }
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
          onChange={(e) => setForm((f) => ({ ...f, unit: e.currentTarget.value }))}
          placeholder={t("defForm.unitPlaceholder")}
        />
        <Select
          label={t("defForm.defaultLocationLabel")}
          data={locationOptions}
          value={form.default_location_id}
          onChange={(v) => setForm((f) => ({ ...f, default_location_id: v ?? "" }))}
          clearable
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
  const [definitions, setDefinitions] = useState<DefinitionResponse[]>([]);
  const [kinds, setKinds] = useState<KindResponse[]>([]);
  const [categories, setCategories] = useState<CategoryResponse[]>([]);
  const [locations, setLocations] = useState<LocationResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");

  const [defModal, setDefModal] = useState<DefModalState>({ kind: "none" });
  const [defForm, setDefForm] = useState<DefinitionFormState>(emptyDefForm());
  const [defBusy, setDefBusy] = useState(false);
  const [defError, setDefError] = useState<string | null>(null);

  // Load all reference data on mount
  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [defsRes, kindsRes, catsRes, locsRes] = await Promise.all([
        client.GET("/api/definitions", { params: { query: {} } }),
        client.GET("/api/kinds"),
        client.GET("/api/categories", { params: { query: {} } }),
        client.GET("/api/locations", { params: { query: {} } }),
      ]);
      if (defsRes.error) {
        setLoadError(t("loadError"));
        return;
      }
      setDefinitions(defsRes.data ?? []);
      setKinds(kindsRes.data ?? []);
      setCategories(catsRes.data ?? []);
      setLocations(locsRes.data ?? []);
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

  // ── Definition CRUD ──────────────────────────────────────────────────────────

  function openCreateDef() {
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
      const { error } = await client.POST("/api/definitions", {
        body: {
          name: defForm.name.trim(),
          description: defForm.description.trim() || null,
          category_id: defForm.category_id ? Number(defForm.category_id) : null,
          kind_id: defForm.kind_id ? Number(defForm.kind_id) : null,
          unit: defForm.unit.trim() || "pcs",
          default_location_id: defForm.default_location_id
            ? Number(defForm.default_location_id)
            : null,
        },
      });
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      closeDefModal();
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
          },
        },
      );
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      closeDefModal();
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

  return (
    <PageShell title={t("page.title")}>
      <Stack gap="md">
        {/* Search + category filter + create button */}
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
          <Button
            leftSection={<Plus size={14} />}
            onClick={openCreateDef}
            data-testid="create-def-btn"
          >
            {t("list.newItemBtn")}
          </Button>
        </Group>

        {/* Definition list */}
        {definitions.length === 0 ? (
          <EmptyState message={t("list.empty")} />
        ) : (
          <Table highlightOnHover>
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
              {definitions.map((def) => (
                <Table.Tr key={def.id} data-testid={`def-row-${def.id}`}>
                  <Table.Td>
                    <Anchor component={Link} to={`/items/${def.id}`} size="sm">
                      {def.name}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>
                    <Badge size="xs" variant="light">
                      {def.kind.name}
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
                        size="xs"
                        variant="subtle"
                        aria-label={t("list.editAriaLabel", { name: def.name })}
                        onClick={() => openEditDef(def)}
                        data-testid={`edit-def-${def.id}`}
                      >
                        <Edit2 size={12} />
                      </ActionIcon>
                      <ActionIcon
                        size="xs"
                        variant="subtle"
                        color="red"
                        aria-label={t("list.deleteAriaLabel", { name: def.name })}
                        onClick={() => openDeleteDef(def)}
                        data-testid={`delete-def-${def.id}`}
                      >
                        <Trash2 size={12} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
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
    </PageShell>
  );
}

// ── ItemDetail page (definition detail + instances) ───────────────────────────

export function ItemDetail() {
  const { t } = useTranslation("items");
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
          },
        },
      );
      if (error) {
        setDefError(mapApiError(error));
        return;
      }
      closeDefModal();
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
      navigate("/items");
    } finally {
      setDefBusy(false);
    }
  }

  // ── Instance CRUD ──────────────────────────────────────────────────────────

  function openCreateInst() {
    setInstForm(emptyInstanceForm(defId));
    setInstError(null);
    setInstModal({ kind: "create", definitionId: defId });
  }

  function openEditInst(inst: InstanceResponse) {
    setInstForm({
      definition_id: String(inst.definition_id),
      location_id: inst.location_id != null ? String(inst.location_id) : "",
      quantity: inst.quantity,
      serial: inst.serial ?? "",
      model_number: inst.model_number ?? "",
      manufacturer: inst.manufacturer ?? "",
      warranty_expires: inst.warranty_expires ?? "",
      warranty_details: inst.warranty_details ?? "",
      purchase_price: inst.purchase_price ?? "",
      purchase_date: inst.purchase_date ?? "",
      purchase_source: inst.purchase_source ?? "",
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
      const qty = serial != null ? "1" : instForm.quantity;
      const { error } = await client.POST("/api/instances", {
        body: {
          definition_id: Number(instForm.definition_id),
          location_id: instForm.location_id ? Number(instForm.location_id) : null,
          quantity: qty,
          serial,
          model_number: instForm.model_number.trim() || null,
          manufacturer: instForm.manufacturer.trim() || null,
          warranty_expires: instForm.warranty_expires.trim() || null,
          warranty_details: instForm.warranty_details.trim() || null,
          purchase_price: instForm.purchase_price.trim() || null,
          purchase_date: instForm.purchase_date.trim() || null,
          purchase_source: instForm.purchase_source.trim() || null,
        },
      });
      if (error) {
        setInstError(mapApiError(error));
        return;
      }
      closeInstModal();
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
      const qty = serial != null ? "1" : instForm.quantity;
      const { error } = await client.PATCH(
        "/api/instances/{instance_id}",
        {
          params: { path: { instance_id: instModal.inst.id } },
          body: {
            location_id: instForm.location_id ? Number(instForm.location_id) : null,
            quantity: qty,
            serial,
            model_number: instForm.model_number.trim() || null,
            manufacturer: instForm.manufacturer.trim() || null,
            warranty_expires: instForm.warranty_expires.trim() || null,
            warranty_details: instForm.warranty_details.trim() || null,
            purchase_price: instForm.purchase_price.trim() || null,
            purchase_date: instForm.purchase_date.trim() || null,
            purchase_source: instForm.purchase_source.trim() || null,
          },
        },
      );
      if (error) {
        setInstError(mapApiError(error));
        return;
      }
      closeInstModal();
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
      await loadAll();
    } finally {
      setInstBusy(false);
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;
  if (!def) return <ErrorState message={t("notFound")} />;

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

      {/* Definition header */}
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

      {/* Definition metadata */}
      <Stack gap={4}>
        {def.description && (
          <Text size="sm" c="dimmed">
            {def.description}
          </Text>
        )}
        <Group gap="lg" wrap="wrap">
          <Group gap={4} wrap="nowrap" component="span">
            <Text size="sm" span fw={500}>{t("detail.kindLabel")}</Text>
            <Badge size="xs" variant="light">
              {def.kind.name}
            </Badge>
          </Group>
          <Text size="sm">
            <Text span fw={500}>{t("detail.unitLabel")}</Text>
            {def.unit}
          </Text>
          <Text size="sm">
            <Text span fw={500}>{t("detail.categoryLabel")}</Text>
            {catName}
          </Text>
          <Text size="sm">
            <Text span fw={500}>{t("detail.defaultLocationLabel")}</Text>
            {locName}
          </Text>
        </Group>
      </Stack>

      <Divider />

      {/* Instances section */}
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap">
          <Title order={4}>{t("detail.instancesTitle")}</Title>
          <Button
            size="xs"
            leftSection={<Plus size={12} />}
            onClick={openCreateInst}
            data-testid="register-instance-btn"
          >
            {t("detail.registerInstanceBtn")}
          </Button>
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
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("detail.colSerial")}</Table.Th>
                <Table.Th>{t("detail.colQty")}</Table.Th>
                <Table.Th>{t("detail.colLocation")}</Table.Th>
                <Table.Th>{t("detail.colManufacturer")}</Table.Th>
                <Table.Th>{t("detail.colWarranty")}</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {instances.map((inst) => (
                <Table.Tr key={inst.id} data-testid={`inst-row-${inst.id}`}>
                  <Table.Td>
                    <Anchor
                      component={Link}
                      to={`/instances/${inst.id}`}
                      size="sm"
                    >
                      {inst.serial ?? (
                        <Text span c="dimmed" size="sm">
                          —
                        </Text>
                      )}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{formatQuantity(inst.quantity)}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {inst.location_id != null
                        ? (locations.find((l) => l.id === inst.location_id)
                            ?.name ?? inst.location_id)
                        : "—"}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{inst.manufacturer ?? "—"}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{inst.warranty_expires ?? "—"}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      <ActionIcon
                        size="xs"
                        variant="subtle"
                        aria-label={t("detail.editInstanceAriaLabel", { id: inst.id })}
                        onClick={() => openEditInst(inst)}
                        data-testid={`edit-inst-${inst.id}`}
                      >
                        <Edit2 size={12} />
                      </ActionIcon>
                      <ActionIcon
                        size="xs"
                        variant="subtle"
                        color="red"
                        aria-label={t("detail.deleteInstanceAriaLabel", { id: inst.id })}
                        onClick={() => openDeleteInst(inst)}
                        data-testid={`delete-inst-${inst.id}`}
                      >
                        <Trash2 size={12} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>

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
    </Stack>
  );
}
