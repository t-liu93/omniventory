/**
 * Shopping list page — persisted household-shared list (M7 Step 6).
 *
 * Displays open items first, then a collapsible "purchased" section.
 * All data read from GET /shopping-list?include_purchased=true — no client-side
 * re-derivation of the list (only the open/purchased split is done by the
 * client on the already-resolved server data).
 *
 * Features:
 *   - Source badge: "Auto" (low-stock-derived) vs "Manual" (user-entered)
 *   - Add manual item: definition-linked or free-text name + qty/unit/note
 *   - Inline edit quantity/note via modal
 *   - Check off:
 *       · Free-text items (no definition_id): immediately checks off (no intake)
 *       · Definition-linked items: shows a modal with
 *           "Just check off" (no body) or
 *           "Check off & add to stock" (intake params: location + quantity)
 *           The intake form reuses the same location-Select + quantity-NumberInput
 *           pattern as the M2 InstanceFormModal, pre-filled with desired_quantity.
 *   - Uncheck, delete
 *   - "Clear purchased" button, "Refresh" button (POST /shopping-list/refresh)
 *   - Empty state via EmptyState component
 *
 * Permissions: VIEW to see the list; EDIT for all mutations.
 * Viewers see the list read-only (no add/edit/check/delete controls).
 */
import { useState, useEffect, useCallback } from "react";
import {
  Stack,
  Group,
  Text,
  Button,
  Badge,
  Table,
  Checkbox,
  ActionIcon,
  Modal,
  TextInput,
  Textarea,
  NumberInput,
  Select,
  Alert,
  Collapse,
  Divider,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  Plus,
  RefreshCw,
  Trash2,
  Edit2,
  AlertCircle,
  ChevronDown,
  ChevronRight,
} from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess, notifyError } from "../components/notify";
import { useAuth } from "../auth/AuthContext";
import type { components } from "../api/schema";
import { PageShell } from "../components/PageShell";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { EmptyState } from "../components/EmptyState";
import { formatQuantity } from "../i18n/format";

// ── Types ─────────────────────────────────────────────────────────────────────

type ShoppingListItem = components["schemas"]["ShoppingListItemResponse"];
type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type LocationResponse = components["schemas"]["LocationResponse"];

// ── Form state interfaces ─────────────────────────────────────────────────────

interface AddFormState {
  definition_id: string;
  name: string;
  desired_quantity: string;
  unit: string;
  note: string;
}

interface EditFormState {
  desired_quantity: string;
  note: string;
}

interface CheckFormState {
  location_id: string;
  quantity: string;
}

const emptyAddForm = (): AddFormState => ({
  definition_id: "",
  name: "",
  desired_quantity: "",
  unit: "",
  note: "",
});

// ── Main component ─────────────────────────────────────────────────────────────

export function ShoppingList() {
  const { t } = useTranslation("shoppingList");
  const { can } = useAuth();
  const canEdit = can("EDIT");

  // ── Data state ─────────────────────────────────────────────────────────────
  const [items, setItems] = useState<ShoppingListItem[] | null>(null);
  const [definitions, setDefinitions] = useState<DefinitionResponse[]>([]);
  const [locations, setLocations] = useState<LocationResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Purchased section expand/collapse
  const [purchasedOpen, { toggle: togglePurchased }] = useDisclosure(false);

  // ── Add modal state ────────────────────────────────────────────────────────
  const [addOpen, { open: openAdd, close: closeAdd }] = useDisclosure(false);
  const [addForm, setAddForm] = useState<AddFormState>(emptyAddForm);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // ── Edit modal state ───────────────────────────────────────────────────────
  const [editItem, setEditItem] = useState<ShoppingListItem | null>(null);
  const [editForm, setEditForm] = useState<EditFormState>({
    desired_quantity: "",
    note: "",
  });
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // ── Check-off modal state ──────────────────────────────────────────────────
  const [checkItem, setCheckItem] = useState<ShoppingListItem | null>(null);
  const [checkForm, setCheckForm] = useState<CheckFormState>({
    location_id: "",
    quantity: "",
  });
  const [checkBusy, setCheckBusy] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  // ── Per-row action busy states ─────────────────────────────────────────────
  const [clearBusy, setClearBusy] = useState(false);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [uncheckingId, setUncheckingId] = useState<number | null>(null);

  // ── Data loading ───────────────────────────────────────────────────────────

  /**
   * Reload the shopping list (open + purchased) via GET.
   * Called after every mutation to keep the view in sync.
   */
  const loadItems = useCallback(async () => {
    const { data, error: apiError } = await client.GET("/api/shopping-list", {
      params: { query: { include_purchased: true } },
    });
    if (apiError || !Array.isArray(data)) {
      setError(t("errors.loadFailed"));
      return;
    }
    setItems(data);
    setError(null);
  }, [t]);

  // Initial load: items + definitions (for the add-modal picker) + locations
  // (for the check-off intake picker).
  useEffect(() => {
    let cancelled = false;

    async function loadAll() {
      setLoading(true);
      setError(null);

      const [itemsRes, defsRes, locsRes] = await Promise.all([
        client.GET("/api/shopping-list", {
          params: { query: { include_purchased: true } },
        }),
        client.GET("/api/definitions"),
        client.GET("/api/locations"),
      ]);

      if (cancelled) return;

      if (itemsRes.error || !Array.isArray(itemsRes.data)) {
        setError(t("errors.loadFailed"));
        setLoading(false);
        return;
      }
      setItems(itemsRes.data);

      if (defsRes.data && Array.isArray(defsRes.data)) {
        setDefinitions(defsRes.data);
      }
      if (locsRes.data && Array.isArray(locsRes.data)) {
        setLocations(locsRes.data);
      }

      setLoading(false);
    }

    void loadAll();
    return () => {
      cancelled = true;
    };
  }, [t]);

  // ── Client-side open/purchased split ──────────────────────────────────────
  // All reads go through GET /shopping-list — no re-derivation of the list.
  // Only the open/purchased split is done client-side on the server-provided data.
  const openItems = (items ?? []).filter((i) => i.purchased_at === null);
  const purchasedItems = (items ?? []).filter((i) => i.purchased_at !== null);

  // ── Add action ─────────────────────────────────────────────────────────────

  async function handleAdd() {
    setAddBusy(true);
    setAddError(null);

    const body: components["schemas"]["ShoppingListItemCreate"] = {
      definition_id: addForm.definition_id
        ? Number(addForm.definition_id)
        : undefined,
      name: addForm.name || undefined,
      desired_quantity: addForm.desired_quantity || undefined,
      unit: addForm.unit || undefined,
      note: addForm.note || undefined,
    };

    const { error: apiError } = await client.POST("/api/shopping-list", {
      body,
    });
    if (apiError) {
      setAddError(mapApiError(apiError));
      setAddBusy(false);
      return;
    }

    notifySuccess(t("success.added"));
    setAddForm(emptyAddForm());
    closeAdd();
    setAddBusy(false);
    await loadItems();
  }

  // ── Edit action ────────────────────────────────────────────────────────────

  function openEditModal(item: ShoppingListItem) {
    setEditItem(item);
    setEditForm({
      desired_quantity: item.desired_quantity ?? "",
      note: item.note ?? "",
    });
    setEditError(null);
  }

  async function handleEdit() {
    if (!editItem) return;
    setEditBusy(true);
    setEditError(null);

    const body: components["schemas"]["ShoppingListItemUpdate"] = {
      desired_quantity: editForm.desired_quantity || null,
      note: editForm.note || null,
    };

    const { error: apiError } = await client.PATCH(
      "/api/shopping-list/{item_id}",
      {
        params: { path: { item_id: editItem.id } },
        body,
      },
    );
    if (apiError) {
      setEditError(mapApiError(apiError));
      setEditBusy(false);
      return;
    }

    notifySuccess(t("success.edited"));
    setEditItem(null);
    setEditBusy(false);
    await loadItems();
  }

  // ── Check-off action ───────────────────────────────────────────────────────

  /**
   * Handle checkbox click:
   *   - Already purchased → uncheck immediately
   *   - Free-text item (no definition_id) → check off immediately (no intake)
   *   - Definition-linked → open the check-off modal (intake optional)
   */
  function handleCheckboxClick(item: ShoppingListItem) {
    if (item.purchased_at !== null) {
      void handleUncheck(item.id);
      return;
    }
    if (item.definition_id === null) {
      // Free-text items: check off immediately without intake
      void handleCheckOff(item.id, false, null, null);
      return;
    }
    // Definition-linked: open the modal so the user can optionally intake stock
    setCheckItem(item);
    setCheckForm({
      location_id: "",
      quantity: item.desired_quantity ?? "",
    });
    setCheckError(null);
  }

  /**
   * Execute the check-off POST.
   *
   * @param itemId     Shopping list item id
   * @param withIntake Whether to pass intake params (location + quantity)
   * @param locationId Optional location id for intake
   * @param quantity   Optional quantity string for intake
   */
  async function handleCheckOff(
    itemId: number,
    withIntake: boolean,
    locationId: string | null,
    quantity: string | null,
  ) {
    setCheckBusy(true);
    setCheckError(null);

    let apiCall;
    if (withIntake) {
      apiCall = client.POST("/api/shopping-list/{item_id}/check", {
        params: { path: { item_id: itemId } },
        body: {
          intake: {
            location_id: locationId ? Number(locationId) : null,
            quantity: quantity || null,
          },
        },
      });
    } else {
      // No body: just stamps purchased_at (Step 1 check-off behaviour)
      apiCall = client.POST("/api/shopping-list/{item_id}/check", {
        params: { path: { item_id: itemId } },
      });
    }

    const { error: apiError } = await apiCall;
    if (apiError) {
      setCheckError(mapApiError(apiError));
      setCheckBusy(false);
      return;
    }

    notifySuccess(
      withIntake ? t("success.checkedWithIntake") : t("success.checked"),
    );
    setCheckItem(null);
    setCheckBusy(false);
    await loadItems();
  }

  // ── Uncheck action ─────────────────────────────────────────────────────────

  async function handleUncheck(itemId: number) {
    setUncheckingId(itemId);
    const { error: apiError } = await client.POST(
      "/api/shopping-list/{item_id}/uncheck",
      { params: { path: { item_id: itemId } } },
    );
    setUncheckingId(null);
    if (apiError) {
      notifyError(mapApiError(apiError));
      return;
    }
    notifySuccess(t("success.unchecked"));
    await loadItems();
  }

  // ── Delete action ──────────────────────────────────────────────────────────

  async function handleDelete(itemId: number) {
    setDeletingId(itemId);
    const { error: apiError } = await client.DELETE(
      "/api/shopping-list/{item_id}",
      { params: { path: { item_id: itemId } } },
    );
    setDeletingId(null);
    if (apiError) {
      notifyError(mapApiError(apiError));
      return;
    }
    notifySuccess(t("success.deleted"));
    await loadItems();
  }

  // ── Clear purchased action ─────────────────────────────────────────────────

  async function handleClearPurchased() {
    setClearBusy(true);
    const { error: apiError } =
      await client.POST("/api/shopping-list/clear-purchased");
    setClearBusy(false);
    if (apiError) {
      notifyError(mapApiError(apiError));
      return;
    }
    notifySuccess(t("success.cleared"));
    await loadItems();
  }

  // ── Refresh action ─────────────────────────────────────────────────────────

  async function handleRefresh() {
    setRefreshBusy(true);
    const { error: apiError } =
      await client.POST("/api/shopping-list/refresh");
    setRefreshBusy(false);
    if (apiError) {
      notifyError(mapApiError(apiError));
      return;
    }
    notifySuccess(t("success.refreshed"));
    // POST /refresh returns the open list, but we reload via GET to include
    // purchased items too (consistent data source for the full page).
    await loadItems();
  }

  // ── Definition & location option lists ────────────────────────────────────

  const defOptions = [
    { value: "", label: t("addModal.definitionLabel") },
    ...definitions.map((d) => ({ value: String(d.id), label: d.name })),
  ];

  const locationOptions = [
    { value: "", label: t("checkModal.noLocation") },
    ...locations.map((l) => ({ value: String(l.id), label: l.name })),
  ];

  // ── Render helpers ─────────────────────────────────────────────────────────

  function SourceBadge({ source }: { source: string }) {
    return (
      <Badge
        color={source === "auto" ? "blue" : "gray"}
        size="sm"
        variant="light"
        data-testid={`source-badge-${source}`}
      >
        {t(`source.${source}`)}
      </Badge>
    );
  }

  function ItemRow({
    item,
    purchased,
  }: {
    item: ShoppingListItem;
    purchased: boolean;
  }) {
    const resolvedName = item.name ?? t("col.name");
    const displayQty = item.desired_quantity
      ? formatQuantity(item.desired_quantity)
      : null;

    return (
      <Table.Tr key={item.id} data-testid={`shopping-row-${item.id}`}>
        {/* Checkbox column */}
        <Table.Td style={{ width: 40 }}>
          {canEdit ? (
            <Checkbox
              checked={purchased}
              onChange={() => handleCheckboxClick(item)}
              aria-label={purchased ? t("actions.uncheck") : t("actions.checkOff")}
              data-testid={`check-${item.id}`}
              disabled={uncheckingId === item.id}
            />
          ) : (
            <Checkbox
              checked={purchased}
              disabled
              readOnly
              aria-label={t("actions.checkOff")}
            />
          )}
        </Table.Td>

        {/* Name column */}
        <Table.Td data-testid={`name-${item.id}`}>
          <Text
            size="sm"
            td={purchased ? "line-through" : undefined}
            c={purchased ? "dimmed" : undefined}
          >
            {resolvedName}
          </Text>
        </Table.Td>

        {/* Source badge column */}
        <Table.Td style={{ width: 90 }}>
          <SourceBadge source={item.source} />
        </Table.Td>

        {/* Quantity + unit column */}
        <Table.Td style={{ width: 130 }} data-testid={`qty-${item.id}`}>
          {displayQty !== null ? (
            <Text size="sm">
              {displayQty}
              {item.unit ? ` ${item.unit}` : ""}
            </Text>
          ) : (
            <Text size="sm" c="dimmed">
              —
            </Text>
          )}
        </Table.Td>

        {/* Note column */}
        <Table.Td data-testid={`note-${item.id}`}>
          {item.note ? (
            <Text size="sm" c="dimmed" truncate maw={200}>
              {item.note}
            </Text>
          ) : null}
        </Table.Td>

        {/* Actions column (EDIT role only) */}
        {canEdit && (
          <Table.Td style={{ width: 80 }}>
            <Group gap={4} wrap="nowrap">
              <ActionIcon
                variant="subtle"
                size="sm"
                onClick={() => openEditModal(item)}
                aria-label={t("actions.edit")}
                data-testid={`edit-${item.id}`}
              >
                <Edit2 size={14} />
              </ActionIcon>
              <ActionIcon
                variant="subtle"
                size="sm"
                color="red"
                onClick={() => void handleDelete(item.id)}
                loading={deletingId === item.id}
                aria-label={t("actions.delete")}
                data-testid={`delete-${item.id}`}
              >
                <Trash2 size={14} />
              </ActionIcon>
            </Group>
          </Table.Td>
        )}
      </Table.Tr>
    );
  }

  // ── Page actions bar ───────────────────────────────────────────────────────

  const pageActions = (
    <Group gap="sm">
      {canEdit && (
        <Button
          variant="default"
          size="sm"
          leftSection={<RefreshCw size={14} />}
          onClick={() => void handleRefresh()}
          loading={refreshBusy}
          data-testid="refresh-btn"
        >
          {t("actions.refresh")}
        </Button>
      )}
      {canEdit && purchasedItems.length > 0 && (
        <Button
          variant="default"
          size="sm"
          onClick={() => void handleClearPurchased()}
          loading={clearBusy}
          data-testid="clear-purchased-btn"
        >
          {t("actions.clearPurchased")}
        </Button>
      )}
      {canEdit && (
        <Button
          size="sm"
          leftSection={<Plus size={14} />}
          onClick={openAdd}
          data-testid="add-item-btn"
        >
          {t("actions.addItem")}
        </Button>
      )}
    </Group>
  );

  // ── Main render ────────────────────────────────────────────────────────────

  return (
    <PageShell title={t("title")} actions={pageActions}>
      {loading && <LoadingState />}

      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && items !== null && openItems.length === 0 && purchasedItems.length === 0 && (
        <div data-testid="shopping-empty">
          <EmptyState message={t("empty")} />
        </div>
      )}

      {!loading && !error && items !== null && (openItems.length > 0 || purchasedItems.length > 0) && (
        <Stack gap="md">
          {/* ── Open items ───────────────────────────────────────────────── */}
          {openItems.length > 0 && (
            <Stack gap="xs">
              <Text fw={600} size="sm" c="dimmed" data-testid="open-section-label">
                {t("section.open")}
              </Text>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ width: 40 }} />
                    <Table.Th>{t("col.name")}</Table.Th>
                    <Table.Th style={{ width: 90 }}>{t("col.source")}</Table.Th>
                    <Table.Th style={{ width: 130 }}>{t("col.quantity")}</Table.Th>
                    <Table.Th>{t("col.note")}</Table.Th>
                    {canEdit && <Table.Th style={{ width: 80 }} />}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {openItems.map((item) => (
                    <ItemRow key={item.id} item={item} purchased={false} />
                  ))}
                </Table.Tbody>
              </Table>
            </Stack>
          )}

          {/* ── Purchased section (collapsible) ──────────────────────────── */}
          {purchasedItems.length > 0 && (
            <Stack gap="xs">
              <Divider />
              <Group
                style={{ cursor: "pointer", userSelect: "none" }}
                onClick={togglePurchased}
                data-testid="purchased-toggle"
              >
                {purchasedOpen ? (
                  <ChevronDown size={14} />
                ) : (
                  <ChevronRight size={14} />
                )}
                <Text fw={600} size="sm" c="dimmed">
                  {t("section.purchased", { count: purchasedItems.length })}
                </Text>
              </Group>
              <Collapse expanded={purchasedOpen}>
                <Table>
                  <Table.Tbody>
                    {purchasedItems.map((item) => (
                      <ItemRow key={item.id} item={item} purchased={true} />
                    ))}
                  </Table.Tbody>
                </Table>
              </Collapse>
            </Stack>
          )}
        </Stack>
      )}

      {/* ── Add item modal ──────────────────────────────────────────────────── */}
      <Modal
        opened={addOpen}
        onClose={() => {
          closeAdd();
          setAddForm(emptyAddForm());
          setAddError(null);
        }}
        title={t("addModal.title")}
      >
        <Stack gap="sm">
          {addError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
              data-testid="add-error-alert"
            >
              {addError}
            </Alert>
          )}
          <Select
            label={t("addModal.definitionLabel")}
            data={defOptions}
            value={addForm.definition_id}
            onChange={(v) =>
              setAddForm((f) => ({ ...f, definition_id: v ?? "" }))
            }
            clearable
            searchable
            data-testid="add-definition-select"
          />
          <TextInput
            label={t("addModal.nameLabel")}
            placeholder={t("addModal.namePlaceholder")}
            value={addForm.name}
            onChange={(e) =>
              setAddForm((f) => ({ ...f, name: e.currentTarget.value }))
            }
            data-testid="add-name-input"
          />
          <Text size="xs" c="dimmed">
            {t("addModal.definitionOrNameHint")}
          </Text>
          <NumberInput
            label={t("addModal.quantityLabel")}
            value={addForm.desired_quantity !== "" ? Number(addForm.desired_quantity) : ""}
            onChange={(v) =>
              setAddForm((f) => ({
                ...f,
                desired_quantity: v === "" ? "" : String(v),
              }))
            }
            min={0}
            allowDecimal
            data-testid="add-quantity-input"
          />
          <TextInput
            label={t("addModal.unitLabel")}
            value={addForm.unit}
            onChange={(e) =>
              setAddForm((f) => ({ ...f, unit: e.currentTarget.value }))
            }
            data-testid="add-unit-input"
          />
          <Textarea
            label={t("addModal.noteLabel")}
            value={addForm.note}
            onChange={(e) =>
              setAddForm((f) => ({ ...f, note: e.currentTarget.value }))
            }
            autosize
            minRows={2}
            data-testid="add-note-input"
          />
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => {
                closeAdd();
                setAddForm(emptyAddForm());
                setAddError(null);
              }}
              disabled={addBusy}
            >
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() => void handleAdd()}
              loading={addBusy}
              data-testid="add-submit-btn"
            >
              {t("addModal.submit")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ── Edit item modal ─────────────────────────────────────────────────── */}
      <Modal
        opened={editItem !== null}
        onClose={() => {
          setEditItem(null);
          setEditError(null);
        }}
        title={t("editModal.title")}
      >
        <Stack gap="sm">
          {editError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
              data-testid="edit-error-alert"
            >
              {editError}
            </Alert>
          )}
          <NumberInput
            label={t("editModal.quantityLabel")}
            value={
              editForm.desired_quantity !== ""
                ? Number(editForm.desired_quantity)
                : ""
            }
            onChange={(v) =>
              setEditForm((f) => ({
                ...f,
                desired_quantity: v === "" ? "" : String(v),
              }))
            }
            min={0}
            allowDecimal
            data-testid="edit-quantity-input"
          />
          <Textarea
            label={t("editModal.noteLabel")}
            value={editForm.note}
            onChange={(e) =>
              setEditForm((f) => ({ ...f, note: e.currentTarget.value }))
            }
            autosize
            minRows={2}
            data-testid="edit-note-input"
          />
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => {
                setEditItem(null);
                setEditError(null);
              }}
              disabled={editBusy}
            >
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() => void handleEdit()}
              loading={editBusy}
              data-testid="edit-submit-btn"
            >
              {t("editModal.submit")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ── Check-off modal (definition-linked items) ───────────────────────── */}
      {/*
       * Intake form reuses the same location-Select + quantity-NumberInput
       * pattern as the M2 InstanceFormModal (the two fields exposed by
       * ShoppingListIntake). Quantity is pre-filled from the item's
       * desired_quantity so the user only needs to confirm or adjust.
       */}
      <Modal
        opened={checkItem !== null}
        onClose={() => {
          setCheckItem(null);
          setCheckError(null);
        }}
        title={t("checkModal.title")}
      >
        <Stack gap="sm">
          {checkError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
              data-testid="check-error-alert"
            >
              {checkError}
            </Alert>
          )}
          <Text size="sm" fw={600}>
            {t("checkModal.intakeSection")}
          </Text>
          <Text size="xs" c="dimmed">
            {t("checkModal.intakeHint")}
          </Text>

          {/* Location picker — mirrors M2 InstanceFormModal's location Select */}
          <Select
            label={t("checkModal.locationLabel")}
            data={locationOptions}
            value={checkForm.location_id}
            onChange={(v) =>
              setCheckForm((f) => ({ ...f, location_id: v ?? "" }))
            }
            clearable
            data-testid="check-location-select"
          />

          {/* Quantity — mirrors M2 InstanceFormModal's quantity NumberInput,
              pre-filled with the item's desired_quantity */}
          <NumberInput
            label={t("checkModal.quantityLabel")}
            value={
              checkForm.quantity !== "" ? Number(checkForm.quantity) : ""
            }
            onChange={(v) =>
              setCheckForm((f) => ({
                ...f,
                quantity: v === "" ? "" : String(v),
              }))
            }
            min={0}
            allowDecimal
            data-testid="check-quantity-input"
          />

          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => {
                if (checkItem) {
                  void handleCheckOff(checkItem.id, false, null, null);
                }
              }}
              loading={checkBusy}
              data-testid="just-check-btn"
            >
              {t("checkModal.justCheckOff")}
            </Button>
            <Button
              onClick={() => {
                if (checkItem) {
                  void handleCheckOff(
                    checkItem.id,
                    true,
                    checkForm.location_id || null,
                    checkForm.quantity || null,
                  );
                }
              }}
              loading={checkBusy}
              data-testid="check-intake-btn"
            >
              {t("checkModal.addToStock")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </PageShell>
  );
}
