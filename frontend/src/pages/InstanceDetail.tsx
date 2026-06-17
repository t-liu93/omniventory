/**
 * InstanceDetail page — shows the full details of a single stock instance.
 *
 * Route: /instances/:id
 *
 * Provides: view, edit, and delete of the instance.
 * Links back to the parent definition at /items/:id.
 *
 * Data access: exclusively via the typed openapi-fetch client.
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
  Paper,
} from "@mantine/core";
import { Edit2, Trash2, AlertCircle, ArrowLeft } from "react-feather";
import { useTranslation, Trans } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import type { components } from "../api/schema";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import {
  InstanceFormModal,
  type InstanceFormState,
} from "../components/InstanceFormModal";
import { formatQuantity } from "../utils";

// ── Schema types ─────────────────────────────────────────────────────────────

type InstanceResponse = components["schemas"]["InstanceResponse"];
type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type LocationResponse = components["schemas"]["LocationResponse"];

function instToForm(inst: InstanceResponse): InstanceFormState {
  return {
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
  };
}

const emptyForm: InstanceFormState = {
  definition_id: "",
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

// ── InstanceDetail ────────────────────────────────────────────────────────────

export function InstanceDetail() {
  const { t } = useTranslation("instances");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const instId = Number(id);

  const [inst, setInst] = useState<InstanceResponse | null>(null);
  const [def, setDef] = useState<DefinitionResponse | null>(null);
  const [locations, setLocations] = useState<LocationResponse[]>([]);
  const [allDefs, setAllDefs] = useState<DefinitionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [form, setForm] = useState<InstanceFormState>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

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

      const [defRes, locsRes, allDefsRes] = await Promise.all([
        client.GET("/api/definitions/{definition_id}", {
          params: { path: { definition_id: instance.definition_id } },
        }),
        client.GET("/api/locations", { params: { query: {} } }),
        client.GET("/api/definitions", { params: { query: {} } }),
      ]);
      setDef(defRes.data ?? null);
      setLocations(locsRes.data ?? []);
      setAllDefs(allDefsRes.data ?? []);
    } finally {
      setLoading(false);
    }
  }, [instId, t]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

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
      const qty = serial != null ? "1" : form.quantity;
      const { error } = await client.PATCH("/api/instances/{instance_id}", {
        params: { path: { instance_id: instId } },
        body: {
          location_id: form.location_id ? Number(form.location_id) : null,
          quantity: qty,
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
      if (inst) {
        navigate(`/items/${inst.definition_id}`);
      } else {
        navigate("/items");
      }
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;
  if (!inst) return <ErrorState message={t("loadError")} />;

  const locName =
    inst.location_id != null
      ? (locations.find((l) => l.id === inst.location_id)?.name ??
          String(inst.location_id))
      : "—";

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
                {def.kind.name}
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
      <Paper p="md" withBorder>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="md">
          <DetailField label={t("detail.quantityField")} value={formatQuantity(inst.quantity)} />
          <DetailField label={t("detail.locationField")} value={locName} />
          <DetailField label={t("detail.serialField")} value={inst.serial} />
          <DetailField label={t("detail.modelNumberField")} value={inst.model_number} />
          <DetailField label={t("detail.manufacturerField")} value={inst.manufacturer} />
          <DetailField label={t("detail.warrantyExpiresField")} value={inst.warranty_expires} />
          <DetailField label={t("detail.warrantyDetailsField")} value={inst.warranty_details} />
          <DetailField label={t("detail.purchasePriceField")} value={inst.purchase_price} />
          <DetailField label={t("detail.purchaseDateField")} value={inst.purchase_date} />
          <DetailField label={t("detail.purchaseSourceField")} value={inst.purchase_source} />
          <DetailField
            label={t("detail.createdField")}
            value={new Date(inst.created_at).toLocaleDateString()}
          />
        </SimpleGrid>
      </Paper>

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
    </Stack>
  );
}
