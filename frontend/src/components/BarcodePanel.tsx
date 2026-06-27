/**
 * BarcodePanel — barcode management for a single item definition.
 *
 * Features:
 *  - List: GET /api/definitions/{id}/barcodes — shows code + optional label.
 *  - Add: inline code + label fields → POST /api/definitions/{id}/barcodes.
 *    Duplicate code (409 barcode.duplicate) is surfaced via mapApiError.
 *  - Remove: per-row with a small confirm modal → DELETE /api/barcodes/{id}.
 *  - Empty / loading / error states.
 *
 * Pattern mirrors AttachmentPanel / TagPanel / NotePanel (M5 Step 8–10).
 *
 * M5 Step 11.
 */

import { useState, useEffect, useCallback } from "react";
import {
  Stack,
  Group,
  Text,
  Title,
  TextInput,
  Button,
  Alert,
  ActionIcon,
  Modal,
  Loader,
} from "@mantine/core";
import { Trash2, AlertCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess } from "./notify";
import type { components } from "../api/schema";

// ── Types ─────────────────────────────────────────────────────────────────────

type BarcodeResponse = components["schemas"]["BarcodeResponse"];

export interface BarcodePanelProps {
  definitionId: number;
}

// ── BarcodePanel ──────────────────────────────────────────────────────────────

export function BarcodePanel({ definitionId }: BarcodePanelProps) {
  const { t } = useTranslation("barcode");

  // ── List state ────────────────────────────────────────────────────────────
  const [barcodes, setBarcodes] = useState<BarcodeResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── Add state ─────────────────────────────────────────────────────────────
  const [newCode, setNewCode] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // ── Delete state ──────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<BarcodeResponse | null>(
    null,
  );
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // ── Load ──────────────────────────────────────────────────────────────────

  const loadBarcodes = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const { data, error } = await client.GET(
      "/api/definitions/{definition_id}/barcodes",
      { params: { path: { definition_id: definitionId } } },
    );
    setLoading(false);
    if (error) {
      setLoadError(mapApiError(error));
      return;
    }
    setBarcodes(data ?? []);
  }, [definitionId]);

  useEffect(() => {
    void loadBarcodes();
  }, [loadBarcodes]);

  // ── Add ───────────────────────────────────────────────────────────────────

  async function handleAdd() {
    if (!newCode.trim()) return;
    setAddBusy(true);
    setAddError(null);
    const { error } = await client.POST(
      "/api/definitions/{definition_id}/barcodes",
      {
        params: { path: { definition_id: definitionId } },
        body: {
          code: newCode.trim(),
          label: newLabel.trim() || null,
          symbology: "unknown",
        },
      },
    );
    setAddBusy(false);
    if (error) {
      setAddError(mapApiError(error));
      return;
    }
    setNewCode("");
    setNewLabel("");
    notifySuccess(t("success.added"));
    void loadBarcodes();
  }

  // ── Delete ────────────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError(null);
    const { error } = await client.DELETE("/api/barcodes/{barcode_id}", {
      params: { path: { barcode_id: deleteTarget.id } },
    });
    setDeleteBusy(false);
    if (error) {
      setDeleteError(mapApiError(error));
      return;
    }
    setDeleteTarget(null);
    notifySuccess(t("success.removed"));
    void loadBarcodes();
  }

  function closeDeleteModal() {
    setDeleteTarget(null);
    setDeleteError(null);
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Stack gap="sm" data-testid="barcode-panel">
      <Title order={5}>{t("sectionTitle")}</Title>

      {loading && <Loader size="xs" />}

      {loadError && (
        <Alert
          icon={<AlertCircle size={16} />}
          color="red"
          variant="light"
          data-testid="barcode-load-error"
        >
          {loadError}
        </Alert>
      )}

      {!loading && barcodes.length === 0 && (
        <Text
          size="sm"
          c="dimmed"
          data-testid="barcode-empty-state"
        >
          {t("emptyState")}
        </Text>
      )}

      {!loading &&
        barcodes.map((bc) => (
          <Group
            key={bc.id}
            justify="space-between"
            align="center"
            data-testid={`barcode-row-${bc.id}`}
          >
            <Stack gap={0}>
              <Text size="sm" ff="monospace" data-testid={`barcode-code-${bc.id}`}>
                {bc.code}
              </Text>
              {bc.label && (
                <Text size="xs" c="dimmed">
                  {bc.label}
                </Text>
              )}
            </Stack>
            <ActionIcon
              size="sm"
              variant="subtle"
              color="red"
              aria-label={t("removeBtn")}
              onClick={() => setDeleteTarget(bc)}
              data-testid={`remove-barcode-${bc.id}`}
            >
              <Trash2 size={14} />
            </ActionIcon>
          </Group>
        ))}

      {/* Add barcode ─────────────────────────────────────────────────────── */}
      <Stack gap="xs">
        {addError && (
          <Alert
            icon={<AlertCircle size={16} />}
            color="red"
            variant="light"
            data-testid="add-barcode-error"
          >
            {addError}
          </Alert>
        )}
        <Group gap="xs" wrap="nowrap">
          <TextInput
            placeholder={t("codePlaceholder")}
            value={newCode}
            onChange={(e) => setNewCode(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAdd();
            }}
            style={{ flex: 1 }}
            data-testid="barcode-code-input"
          />
          <TextInput
            placeholder={t("labelPlaceholder")}
            value={newLabel}
            onChange={(e) => setNewLabel(e.currentTarget.value)}
            style={{ minWidth: 120 }}
            data-testid="barcode-label-input"
          />
          <Button
            onClick={() => void handleAdd()}
            loading={addBusy}
            disabled={!newCode.trim()}
            data-testid="add-barcode-btn"
          >
            {t("addBtn")}
          </Button>
        </Group>
      </Stack>

      {/* Remove confirm modal ────────────────────────────────────────────── */}
      <Modal
        opened={!!deleteTarget}
        onClose={closeDeleteModal}
        title={t("removeConfirm.title")}
        size="sm"
      >
        <Stack gap="sm">
          {deleteError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
            >
              {deleteError}
            </Alert>
          )}
          <Text size="sm">
            {t("removeConfirm.text", { code: deleteTarget?.code ?? "" })}
          </Text>
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={closeDeleteModal}
              disabled={deleteBusy}
            >
              {t("removeConfirm.cancelBtn")}
            </Button>
            <Button
              color="red"
              onClick={() => void handleDelete()}
              loading={deleteBusy}
              data-testid="confirm-remove-barcode-btn"
            >
              {t("removeConfirm.confirmBtn")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
