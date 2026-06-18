/**
 * InstanceDetail page — shows the full details of a single stock instance.
 *
 * Route: /instances/:id
 *
 * Provides: view, edit, and delete of the instance.
 * Links back to the parent definition at /items/:id.
 *
 * Data access: exclusively via the typed openapi-fetch client.
 *
 * M2 Step 7: movement-history table, per-lot action buttons (Intake/Move/Adjust/
 * Discard), and Reverse (undo) action on reversible rows.
 *
 * Reversibility rule (client-side, §4.4):
 *   A row is reversible iff it is NOT itself a reversal
 *   (reverses_movement_id == null) AND no other row in the loaded history
 *   has reverses_movement_id == this.id.
 */
import { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  Stack,
  Group,
  Text,
  Title,
  Button,
  Badge,
  Anchor,
  Modal,
  Alert,
  Divider,
  SimpleGrid,
  Card,
  Table,
  TextInput,
  NumberInput,
  Select,
} from "@mantine/core";
import { Edit2, Trash2, AlertCircle, ArrowLeft } from "react-feather";
import { useTranslation, Trans } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess } from "../components/notify";
import type { components } from "../api/schema";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import {
  InstanceFormModal,
  type InstanceFormState,
} from "../components/InstanceFormModal";
import { formatDate, formatQuantity } from "../i18n/format";

// ── Schema types ─────────────────────────────────────────────────────────────

type InstanceResponse = components["schemas"]["InstanceResponse"];
type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type LocationResponse = components["schemas"]["LocationResponse"];
type MovementResponse = components["schemas"]["MovementResponse"];

function instToForm(inst: InstanceResponse): InstanceFormState {
  return {
    definition_id: String(inst.definition_id),
    location_id: inst.location_id != null ? String(inst.location_id) : "",
    quantity: inst.quantity ?? "1",
    stock_level: inst.stock_level ?? "",
    serial: inst.serial ?? "",
    model_number: inst.model_number ?? "",
    manufacturer: inst.manufacturer ?? "",
    warranty_expires: inst.warranty_expires ?? "",
    warranty_details: inst.warranty_details ?? "",
    purchase_price: inst.purchase_price ?? "",
    purchase_date: inst.purchase_date ?? "",
    purchase_source: inst.purchase_source ?? "",
  };
}

const emptyForm: InstanceFormState = {
  definition_id: "",
  location_id: "",
  quantity: "1",
  stock_level: "",
  serial: "",
  model_number: "",
  manufacturer: "",
  warranty_expires: "",
  warranty_details: "",
  purchase_price: "",
  purchase_date: "",
  purchase_source: "",
};

// ── Detail field helper ───────────────────────────────────────────────────────

function DetailField({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed" fw={500}>
        {label}
      </Text>
      <Text size="sm">{value ?? "—"}</Text>
    </Stack>
  );
}

// ── Ledger action form state ──────────────────────────────────────────────────

type LedgerActionKind = "intake" | "discard" | "adjust" | "move";

interface LedgerActionFormState {
  quantity: string;
  note: string;
  to_location_id: string;
}

// ── InstanceDetail ────────────────────────────────────────────────────────────

export function InstanceDetail() {
  const { t } = useTranslation("instances");
  const { t: tStock } = useTranslation("stock");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const instId = Number(id);

  const [inst, setInst] = useState<InstanceResponse | null>(null);
  const [def, setDef] = useState<DefinitionResponse | null>(null);
  const [locations, setLocations] = useState<LocationResponse[]>([]);
  const [allDefs, setAllDefs] = useState<DefinitionResponse[]>([]);
  const [movements, setMovements] = useState<MovementResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState<InstanceFormState>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Per-lot ledger action modal
  const [ledgerAction, setLedgerAction] = useState<LedgerActionKind | null>(null);
  const [ledgerForm, setLedgerForm] = useState<LedgerActionFormState>({ quantity: "", note: "", to_location_id: "" });
  const [ledgerBusy, setLedgerBusy] = useState(false);
  const [ledgerError, setLedgerError] = useState<string | null>(null);

  // Reverse modal state
  const [reverseMovementId, setReverseMovementId] = useState<number | null>(null);
  const [reverseNote, setReverseNote] = useState("");
  const [reverseBusy, setReverseBusy] = useState(false);
  const [reverseError, setReverseError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const instRes = await client.GET("/api/instances/{instance_id}", {
        params: { path: { instance_id: instId } },
      });
      if (instRes.error || !instRes.data) {
        setLoadError(t("loadError"));
        return;
      }
      const instance = instRes.data;
      setInst(instance);

      const [defRes, locsRes, allDefsRes, movRes] = await Promise.all([
        client.GET("/api/definitions/{definition_id}", {
          params: { path: { definition_id: instance.definition_id } },
        }),
        client.GET("/api/locations", { params: { query: {} } }),
        client.GET("/api/definitions", { params: { query: {} } }),
        client.GET("/api/instances/{instance_id}/movements", {
          params: { path: { instance_id: instId } },
        }),
      ]);
      setDef(defRes.data ?? null);
      setLocations(locsRes.data ?? []);
      setAllDefs(allDefsRes.data ?? []);
      setMovements(movRes.data ?? []);
    } finally {
      setLoading(false);
    }
  }, [instId, t]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // ── Reload movements only (after a ledger action / reverse) ──────────────────

  const reloadMovements = useCallback(async () => {
    const [instRes, movRes] = await Promise.all([
      client.GET("/api/instances/{instance_id}", {
        params: { path: { instance_id: instId } },
      }),
      client.GET("/api/instances/{instance_id}/movements", {
        params: { path: { instance_id: instId } },
      }),
    ]);
    if (instRes.data) setInst(instRes.data);
    setMovements(movRes.data ?? []);
  }, [instId]);

  // ── Edit / delete ─────────────────────────────────────────────────────────────

  function openEdit() {
    if (!inst) return;
    setForm(instToForm(inst));
    setActionError(null);
    setEditOpen(true);
  }

  async function handleEdit() {
    setBusy(true);
    setActionError(null);
    try {
      const serial = form.serial.trim() || null;
      const mode = def?.stock_tracking_mode ?? "exact";
      const stockLevel = mode === "level" ? (form.stock_level || null) : undefined;
      const { error } = await client.PATCH("/api/instances/{instance_id}", {
        params: { path: { instance_id: instId } },
        body: {
          location_id: form.location_id ? Number(form.location_id) : null,
          // quantity intentionally absent (M2 §2): changes only via ledger.
          stock_level: stockLevel,
          serial,
          model_number: form.model_number.trim() || null,
          manufacturer: form.manufacturer.trim() || null,
          warranty_expires: form.warranty_expires.trim() || null,
          warranty_details: form.warranty_details.trim() || null,
          purchase_price: form.purchase_price.trim() || null,
          purchase_date: form.purchase_date.trim() || null,
          purchase_source: form.purchase_source.trim() || null,
        },
      });
      if (error) {
        setActionError(mapApiError(error));
        return;
      }
      setEditOpen(false);
      notifySuccess(t("success.updated"));
      await loadAll();
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    setActionError(null);
    try {
      const { error } = await client.DELETE("/api/instances/{instance_id}", {
        params: { path: { instance_id: instId } },
      });
      if (error) {
        setActionError(mapApiError(error));
        return;
      }
      notifySuccess(t("success.deleted"));
      if (inst) {
        navigate(`/items/${inst.definition_id}`);
      } else {
        navigate("/items");
      }
    } finally {
      setBusy(false);
    }
  }

  // ── Per-lot ledger actions ────────────────────────────────────────────────────

  function openLedgerAction(kind: LedgerActionKind) {
    setLedgerForm({ quantity: "", note: "", to_location_id: "" });
    setLedgerError(null);
    setLedgerAction(kind);
  }

  function closeLedgerAction() {
    setLedgerAction(null);
    setLedgerError(null);
  }

  async function handleLedgerAction() {
    if (!ledgerAction) return;
    setLedgerBusy(true);
    setLedgerError(null);
    try {
      let error: unknown = null;
      if (ledgerAction === "intake") {
        const res = await client.POST("/api/instances/{instance_id}/intake", {
          params: { path: { instance_id: instId } },
          body: {
            quantity: ledgerForm.quantity,
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      } else if (ledgerAction === "discard") {
        const res = await client.POST("/api/instances/{instance_id}/discard", {
          params: { path: { instance_id: instId } },
          body: {
            quantity: ledgerForm.quantity,
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      } else if (ledgerAction === "adjust") {
        const res = await client.POST("/api/instances/{instance_id}/adjust", {
          params: { path: { instance_id: instId } },
          body: {
            quantity: ledgerForm.quantity,
            note: ledgerForm.note.trim() || null,
          },
        });
        error = res.error;
      } else if (ledgerAction === "move") {
        if (!ledgerForm.to_location_id) return;
        const res = await client.POST("/api/instances/{instance_id}/move", {
          params: { path: { instance_id: instId } },
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
      notifySuccess(t(`success.${ledgerAction}` as never, { defaultValue: "Done." }));
      await reloadMovements();
    } finally {
      setLedgerBusy(false);
    }
  }

  // ── Reverse (undo) ────────────────────────────────────────────────────────────

  function openReverse(movementId: number) {
    setReverseMovementId(movementId);
    setReverseNote("");
    setReverseError(null);
  }

  function closeReverse() {
    setReverseMovementId(null);
    setReverseError(null);
  }

  async function handleReverse() {
    if (reverseMovementId == null) return;
    setReverseBusy(true);
    setReverseError(null);
    try {
      const { error } = await client.POST("/api/movements/{movement_id}/reverse", {
        params: { path: { movement_id: reverseMovementId } },
        body: { note: reverseNote.trim() || null },
      });
      if (error) {
        setReverseError(mapApiError(error));
        return;
      }
      closeReverse();
      notifySuccess(t("success.reverse"));
      await reloadMovements();
    } finally {
      setReverseBusy(false);
    }
  }

  // ── Reversibility: compute client-side ───────────────────────────────────────
  // A movement is reversible iff:
  //   1. It is not itself a reversal (reverses_movement_id == null).
  //   2. No other row in the loaded history has reverses_movement_id == this.id.
  function isReversible(mov: MovementResponse): boolean {
    if (mov.reverses_movement_id !== null) return false;
    return !movements.some((m) => m.reverses_movement_id === mov.id);
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;
  if (!inst) return <ErrorState message={t("loadError")} />;

  const locName =
    inst.location_id != null
      ? (locations.find((l) => l.id === inst.location_id)?.name ??
          String(inst.location_id))
      : "—";

  const mode = def?.stock_tracking_mode ?? "exact";

  return (
    <Stack gap="lg">
      {/* Back link */}
      <Group>
        <Anchor
          component={Link}
          to={`/items/${inst.definition_id}`}
          size="sm"
          c="dimmed"
        >
          <Group gap={4}>
            <ArrowLeft size={14} />
            {def ? def.name : `Definition #${inst.definition_id}`}
          </Group>
        </Anchor>
      </Group>

      {/* Header */}
      <Group justify="space-between" wrap="nowrap">
        <Stack gap={2}>
          <Title order={2}>
            {inst.serial
              ? t("detail.serialTitle", { serial: inst.serial })
              : t("detail.instanceTitle", { id: inst.id })}
          </Title>
          {def && (
            <Group gap={6} wrap="nowrap">
              <Text size="sm" c="dimmed">{def.name}</Text>
              <Badge size="xs" variant="light">
                {t(`items:kinds.${def.kind.code}`, { defaultValue: def.kind.name })}
              </Badge>
            </Group>
          )}
        </Stack>
        <Group gap={8}>
          <Button
            size="xs"
            variant="light"
            leftSection={<Edit2 size={12} />}
            onClick={openEdit}
            data-testid="edit-inst-btn"
          >
            {t("detail.editBtn")}
          </Button>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<Trash2 size={12} />}
            onClick={() => {
              setActionError(null);
              setDeleteOpen(true);
            }}
            data-testid="delete-inst-btn"
          >
            {t("detail.deleteBtn")}
          </Button>
        </Group>
      </Group>

      <Divider />

      {/* Detail fields */}
      <Card>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="md">
          {/* Quantity / level / none — render by mode */}
          {mode === "exact" ? (
            <DetailField label={t("detail.quantityField")} value={formatQuantity(inst.quantity)} />
          ) : mode === "level" ? (
            <Stack gap={2}>
              <Text size="xs" c="dimmed" fw={500}>{t("detail.quantityField")}</Text>
              {inst.stock_level ? (
                <Badge
                  size="sm"
                  color={inst.stock_level === "high" ? "green" : inst.stock_level === "medium" ? "yellow" : "red"}
                  variant="light"
                  style={{ alignSelf: "flex-start" }}
                  aria-label={t("detail.levelBadgeAriaLabel", { level: inst.stock_level })}
                  data-testid="inst-level-badge"
                >
                  {tStock(`stockLevel.${inst.stock_level}`, { defaultValue: inst.stock_level })}
                </Badge>
              ) : (
                <Text size="sm" c="dimmed">—</Text>
              )}
            </Stack>
          ) : (
            <DetailField label={t("detail.quantityField")} value="—" />
          )}
          <DetailField label={t("detail.locationField")} value={locName} />
          <DetailField label={t("detail.serialField")} value={inst.serial} />
          <DetailField label={t("detail.modelNumberField")} value={inst.model_number} />
          <DetailField label={t("detail.manufacturerField")} value={inst.manufacturer} />
          <DetailField label={t("detail.warrantyExpiresField")} value={formatDate(inst.warranty_expires)} />
          <DetailField label={t("detail.warrantyDetailsField")} value={inst.warranty_details} />
          <DetailField label={t("detail.purchasePriceField")} value={inst.purchase_price} />
          <DetailField label={t("detail.purchaseDateField")} value={formatDate(inst.purchase_date)} />
          <DetailField label={t("detail.purchaseSourceField")} value={inst.purchase_source} />
          <DetailField
            label={t("detail.createdField")}
            value={formatDate(inst.created_at)}
          />
        </SimpleGrid>
      </Card>

      {/* Per-lot action buttons (exact mode only) */}
      {mode === "exact" && (
        <Stack gap="xs">
          <Text size="sm" fw={500}>{t("detail.actionsTitle")}</Text>
          <Group gap={8}>
            <Button
              size="xs"
              variant="light"
              onClick={() => openLedgerAction("intake")}
              data-testid="lot-intake-btn"
            >
              {tStock("actions.intake")}
            </Button>
            <Button
              size="xs"
              variant="light"
              onClick={() => openLedgerAction("adjust")}
              data-testid="lot-adjust-btn"
            >
              {tStock("actions.adjust")}
            </Button>
            <Button
              size="xs"
              variant="light"
              onClick={() => openLedgerAction("discard")}
              data-testid="lot-discard-btn"
            >
              {tStock("actions.discard")}
            </Button>
            <Button
              size="xs"
              variant="light"
              onClick={() => openLedgerAction("move")}
              data-testid="lot-move-btn"
            >
              {tStock("actions.move")}
            </Button>
          </Group>
        </Stack>
      )}

      {/* Movement history table */}
      {mode === "exact" && (
        <>
          <Divider />
          <Stack gap="sm">
            <Title order={4}>{tStock("history.title")}</Title>
            {movements.length === 0 ? (
              <Text size="sm" c="dimmed" data-testid="history-empty">{tStock("history.empty")}</Text>
            ) : (
              <Table.ScrollContainer minWidth={680}>
                <Table highlightOnHover verticalSpacing="sm">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{tStock("history.colType")}</Table.Th>
                      <Table.Th>{tStock("history.colDelta")}</Table.Th>
                      <Table.Th>{tStock("history.colFrom")}</Table.Th>
                      <Table.Th>{tStock("history.colTo")}</Table.Th>
                      <Table.Th>{tStock("history.colOccurredAt")}</Table.Th>
                      <Table.Th>{tStock("history.colActor")}</Table.Th>
                      <Table.Th>{tStock("history.colReversalOf")}</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {movements.map((mov) => {
                      const fromLoc = mov.from_location_id
                        ? (locations.find((l) => l.id === mov.from_location_id)?.name ?? String(mov.from_location_id))
                        : "—";
                      const toLoc = mov.to_location_id
                        ? (locations.find((l) => l.id === mov.to_location_id)?.name ?? String(mov.to_location_id))
                        : "—";
                      const deltaPositive = parseFloat(mov.quantity_delta) > 0;
                      const deltaZero = parseFloat(mov.quantity_delta) === 0;
                      return (
                        <Table.Tr key={mov.id} data-testid={`movement-row-${mov.id}`}>
                          <Table.Td>
                            <Badge size="sm" variant="light" color="gray">
                              {tStock(`movementType.${mov.type}`, { defaultValue: mov.type })}
                            </Badge>
                          </Table.Td>
                          <Table.Td>
                            <Text
                              size="sm"
                              c={deltaZero ? "dimmed" : deltaPositive ? "green" : "red"}
                              data-testid={`movement-delta-${mov.id}`}
                            >
                              {deltaZero ? "0" : (deltaPositive ? "+" : "") + formatQuantity(mov.quantity_delta)}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            <Text size="sm" c="dimmed">{fromLoc}</Text>
                          </Table.Td>
                          <Table.Td>
                            <Text size="sm" c="dimmed">{toLoc}</Text>
                          </Table.Td>
                          <Table.Td>
                            <Text size="sm">{formatDate(mov.occurred_at)}</Text>
                          </Table.Td>
                          <Table.Td>
                            <Text size="sm" c="dimmed" data-testid={`movement-actor-${mov.id}`}>
                              {mov.user_id != null
                                ? String(mov.user_id)
                                : tStock("history.unknownActor")}
                            </Text>
                          </Table.Td>
                          <Table.Td>
                            {mov.reverses_movement_id != null ? (
                              <Text size="sm" c="dimmed" data-testid={`reversal-link-${mov.id}`}>
                                {tStock("history.reversalOf", { id: mov.reverses_movement_id })}
                              </Text>
                            ) : (
                              <Text size="sm" c="dimmed">—</Text>
                            )}
                          </Table.Td>
                          <Table.Td>
                            {isReversible(mov) && (
                              <Button
                                size="xs"
                                variant="subtle"
                                color="orange"
                                onClick={() => openReverse(mov.id)}
                                data-testid={`reverse-btn-${mov.id}`}
                              >
                                {tStock("history.reverseBtn")}
                              </Button>
                            )}
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </Stack>
        </>
      )}

      {/* Edit modal */}
      <InstanceFormModal
        opened={editOpen}
        title={t("items:instanceForm.editTitle")}
        form={form}
        setForm={setForm}
        onSubmit={handleEdit}
        onClose={() => {
          setEditOpen(false);
          setActionError(null);
        }}
        busy={busy}
        error={actionError}
        definitions={allDefs}
        locations={locations}
        lockDefinition
        trackingMode={def?.stock_tracking_mode ?? "exact"}
        isEdit={true}
      />

      {/* Delete confirmation modal */}
      <Modal
        opened={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title={t("deleteModal.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          {!actionError && (
            <Text size="sm">
              <Trans
                i18nKey="deleteModal.confirmation"
                ns="instances"
                values={{ id: inst.id }}
                components={{ bold: <b /> }}
              />
            </Text>
          )}
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => setDeleteOpen(false)}
              disabled={busy}
            >
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!actionError && (
              <Button
                color="red"
                onClick={handleDelete}
                loading={busy}
                data-testid="confirm-delete-inst-btn"
              >
                {t("common:actions.delete", "Delete")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {/* Per-lot ledger action modal */}
      {ledgerAction && (
        <Modal
          opened={!!ledgerAction}
          onClose={closeLedgerAction}
          title={tStock(`${ledgerAction}Modal.title` as never, { defaultValue: ledgerAction })}
          size="sm"
        >
          <Stack gap="sm">
            {ledgerError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="ledger-error-alert">
                {ledgerError}
              </Alert>
            )}
            {ledgerAction === "adjust" && (
              <Text size="xs" c="dimmed">{tStock("adjustModal.hint")}</Text>
            )}
            {ledgerAction !== "move" && (
              <NumberInput
                label={tStock(`${ledgerAction}Modal.quantityLabel` as never, { defaultValue: "Quantity" })}
                value={ledgerForm.quantity === "" ? "" : Number(ledgerForm.quantity)}
                onChange={(v) => setLedgerForm((f) => ({ ...f, quantity: v === "" ? "" : String(v) }))}
                min={0}
                allowDecimal
                required
                data-testid="ledger-quantity-input"
              />
            )}
            {ledgerAction === "move" && (
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
              label={tStock(`${ledgerAction}Modal.noteLabel` as never, { defaultValue: "Note (optional)" })}
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
                  ledgerAction !== "move"
                    ? !ledgerForm.quantity
                    : !ledgerForm.to_location_id
                }
                data-testid="ledger-submit-btn"
              >
                {tStock(`actions.${ledgerAction}` as never, { defaultValue: ledgerAction })}
              </Button>
            </Group>
          </Stack>
        </Modal>
      )}

      {/* Reverse movement modal */}
      <Modal
        opened={reverseMovementId !== null}
        onClose={closeReverse}
        title={tStock("reverseModal.title")}
        size="sm"
      >
        <Stack gap="sm">
          {reverseError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="reverse-error-alert">
              {reverseError}
            </Alert>
          )}
          <Text size="xs" c="dimmed">{tStock("reverseModal.hint")}</Text>
          <TextInput
            label={tStock("reverseModal.noteLabel")}
            value={reverseNote}
            onChange={(e) => setReverseNote(e.currentTarget.value)}
            data-testid="reverse-note-input"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeReverse} disabled={reverseBusy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              color="orange"
              onClick={handleReverse}
              loading={reverseBusy}
              data-testid="reverse-submit-btn"
            >
              {tStock("actions.reverse")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
