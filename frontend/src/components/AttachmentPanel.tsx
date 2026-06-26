/**
 * AttachmentPanel — reusable owner-metadata component for managing file attachments.
 *
 * Features:
 *  - Upload: Mantine FileButton → multipart POST /api/attachments.
 *  - Gallery: responsive grid of the owner's attachments.
 *    · Image attachments: lazy <img> from media.url.
 *    · Non-image: react-feather File icon + original_filename as download link.
 *  - Delete: per-item with a small confirm modal.
 *  - Caption edit (optional title): inline TextInput → PATCH /api/attachments/{id}.
 *  - Empty / loading / error states.
 *
 * Multipart upload:
 *  openapi-fetch serialises `body` as JSON by default.  For multipart/form-data
 *  we pass a real `FormData` and supply a `bodySerializer` that returns it
 *  unchanged so the browser can set the boundary in Content-Type automatically.
 *  We never set Content-Type manually.
 *
 * M5 Step 8.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import {
  Stack,
  Group,
  Text,
  Title,
  Button,
  TextInput,
  Alert,
  SimpleGrid,
  Card,
  Modal,
  Loader,
  FileButton,
  ActionIcon,
  Anchor,
} from "@mantine/core";
import { File as FileIcon, Trash2, AlertCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess } from "./notify";
import type { components } from "../api/schema";

// ── Types ─────────────────────────────────────────────────────────────────────

type AttachmentResponse = components["schemas"]["AttachmentResponse"];

export type AttachmentOwnerType =
  | "item_definition"
  | "stock_instance"
  | "location";

export interface AttachmentPanelProps {
  modelType: AttachmentOwnerType;
  modelId: number;
}

// ── AttachmentPanel ───────────────────────────────────────────────────────────

export function AttachmentPanel({ modelType, modelId }: AttachmentPanelProps) {
  const { t } = useTranslation("attachments");

  // ── Attachment list state ─────────────────────────────────────────────────
  const [attachments, setAttachments] = useState<AttachmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── Upload state ──────────────────────────────────────────────────────────
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // resetRef lets us clear the hidden file input after a successful upload
  // so the same file can be re-uploaded if needed.
  const resetRef = useRef<() => void>(null);

  // ── Delete state ──────────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<AttachmentResponse | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // ── Caption edit state ────────────────────────────────────────────────────
  const [editCaption, setEditCaption] = useState<{
    id: number;
    value: string;
  } | null>(null);
  const [savingCaption, setSavingCaption] = useState(false);

  // ── Data loading ──────────────────────────────────────────────────────────

  const loadAttachments = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const { data, error } = await client.GET("/api/attachments", {
        params: { query: { model_type: modelType, model_id: modelId } },
      });
      if (error) {
        setLoadError(mapApiError(error));
        return;
      }
      // Defensive filter: only keep items with a valid media object, protecting
      // against stale or mis-routed test mocks returning non-attachment data.
      setAttachments((data ?? []).filter((att) => att?.media != null));
    } finally {
      setLoading(false);
    }
  }, [modelType, modelId]);

  useEffect(() => {
    void loadAttachments();
  }, [loadAttachments]);

  // ── Upload ────────────────────────────────────────────────────────────────

  async function handleUpload(file: File | null) {
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const fd = new FormData();
      fd.append("model_type", modelType);
      fd.append("model_id", String(modelId));
      fd.append("file", file);

      // Bypass openapi-fetch JSON serialisation: return the FormData as-is so
      // the browser sets the correct Content-Type with boundary.
      const { error } = await client.POST("/api/attachments", {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: fd as any,
        bodySerializer: (body) => body as unknown as string,
      });
      if (error) {
        setUploadError(mapApiError(error));
        return;
      }
      notifySuccess(t("success.uploaded"));
      resetRef.current?.();
      await loadAttachments();
    } finally {
      setUploading(false);
    }
  }

  // ── Delete ────────────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const { error } = await client.DELETE(
        "/api/attachments/{attachment_id}",
        {
          params: { path: { attachment_id: deleteTarget.id } },
        },
      );
      if (error) {
        setDeleteError(mapApiError(error));
        return;
      }
      setDeleteTarget(null);
      notifySuccess(t("success.deleted"));
      await loadAttachments();
    } finally {
      setDeleting(false);
    }
  }

  // ── Caption edit ──────────────────────────────────────────────────────────

  async function handleSaveCaption(id: number, value: string) {
    setSavingCaption(true);
    try {
      const { error } = await client.PATCH(
        "/api/attachments/{attachment_id}",
        {
          params: { path: { attachment_id: id } },
          body: { title: value.trim() || null },
        },
      );
      if (error) return; // silent — caption save failure is not critical
      notifySuccess(t("success.captionSaved"));
      setEditCaption(null);
      await loadAttachments();
    } finally {
      setSavingCaption(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Stack gap="sm" data-testid="attachment-panel">
      {/* Header: title + upload button */}
      <Group justify="space-between" align="center">
        <Title order={5}>{t("sectionTitle")}</Title>
        <FileButton
          resetRef={resetRef}
          onChange={handleUpload}
          accept="image/*,application/pdf,text/*,application/zip,application/octet-stream,.pdf,.doc,.docx,.xls,.xlsx"
        >
          {(props) => (
            <Button
              {...props}
              size="xs"
              variant="light"
              loading={uploading}
              data-testid="attachment-upload-btn"
            >
              {t("uploadBtn")}
            </Button>
          )}
        </FileButton>
      </Group>

      {/* Upload error */}
      {uploadError && (
        <Alert
          icon={<AlertCircle size={16} />}
          color="red"
          variant="light"
          data-testid="upload-error-alert"
        >
          {uploadError}
        </Alert>
      )}

      {/* Loading / error / empty / gallery */}
      {loading ? (
        <Loader size="sm" data-testid="attachment-loading" />
      ) : loadError ? (
        <Alert
          icon={<AlertCircle size={16} />}
          color="red"
          variant="light"
          data-testid="attachment-load-error"
        >
          {loadError}
        </Alert>
      ) : attachments.length === 0 ? (
        <Text size="sm" c="dimmed" data-testid="attachment-empty">
          {t("emptyState")}
        </Text>
      ) : (
        <SimpleGrid cols={{ base: 2, sm: 3, md: 4 }} spacing="xs">
          {attachments.map((att) => {
            const isImage = att.media.content_type.startsWith("image/");
            const captionEditing = editCaption?.id === att.id;

            return (
              <Card
                key={att.id}
                padding="xs"
                withBorder
                data-testid={`attachment-card-${att.id}`}
              >
                <Stack gap={6}>
                  {/* Image or file icon */}
                  {isImage ? (
                    <img
                      src={att.media.url}
                      alt={
                        att.title ??
                        att.original_filename ??
                        t("sectionTitle")
                      }
                      loading="lazy"
                      style={{
                        width: "100%",
                        aspectRatio: "1/1",
                        objectFit: "cover",
                        borderRadius: 4,
                        display: "block",
                      }}
                      data-testid={`attachment-img-${att.id}`}
                    />
                  ) : (
                    <Group
                      gap={6}
                      align="center"
                      wrap="nowrap"
                      data-testid={`attachment-file-${att.id}`}
                    >
                      <FileIcon size={20} style={{ flexShrink: 0 }} />
                      <Anchor
                        href={att.media.url}
                        download={att.original_filename ?? undefined}
                        size="xs"
                        style={{ flex: 1, wordBreak: "break-all" }}
                        data-testid={`attachment-download-${att.id}`}
                      >
                        {att.original_filename ?? t("downloadLabel")}
                      </Anchor>
                    </Group>
                  )}

                  {/* Caption — inline edit */}
                  {captionEditing ? (
                    <Group gap={4} align="flex-start">
                      <TextInput
                        size="xs"
                        placeholder={t("captionPlaceholder")}
                        value={editCaption.value}
                        onChange={(e) => {
                          const value = e.currentTarget.value;
                          setEditCaption((prev) =>
                            prev ? { ...prev, value } : null,
                          );
                        }}
                        style={{ flex: 1 }}
                        data-testid={`caption-input-${att.id}`}
                      />
                      <Button
                        size="xs"
                        variant="light"
                        loading={savingCaption}
                        onClick={() =>
                          handleSaveCaption(att.id, editCaption.value)
                        }
                        data-testid={`caption-save-btn-${att.id}`}
                      >
                        {t("captionSaveBtn")}
                      </Button>
                    </Group>
                  ) : (
                    <Text
                      size="xs"
                      c="dimmed"
                      style={{ cursor: "pointer" }}
                      onClick={() =>
                        setEditCaption({
                          id: att.id,
                          value: att.title ?? "",
                        })
                      }
                      data-testid={`caption-text-${att.id}`}
                    >
                      {att.title ?? t("captionPlaceholder")}
                    </Text>
                  )}

                  {/* Delete button */}
                  <ActionIcon
                    size="xs"
                    variant="subtle"
                    color="red"
                    onClick={() => {
                      setDeleteError(null);
                      setDeleteTarget(att);
                    }}
                    data-testid={`attachment-delete-btn-${att.id}`}
                    aria-label={t("deleteConfirm.title")}
                  >
                    <Trash2 size={12} />
                  </ActionIcon>
                </Stack>
              </Card>
            );
          })}
        </SimpleGrid>
      )}

      {/* Delete confirmation modal */}
      <Modal
        opened={deleteTarget !== null}
        onClose={() => {
          setDeleteTarget(null);
          setDeleteError(null);
        }}
        title={t("deleteConfirm.title")}
        size="sm"
      >
        <Stack gap="sm">
          {deleteError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
              data-testid="delete-error-alert"
            >
              {deleteError}
            </Alert>
          )}
          {!deleteError && (
            <Text size="sm">{t("deleteConfirm.text")}</Text>
          )}
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => {
                setDeleteTarget(null);
                setDeleteError(null);
              }}
              disabled={deleting}
            >
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!deleteError && (
              <Button
                color="red"
                onClick={handleDelete}
                loading={deleting}
                data-testid="confirm-delete-attachment-btn"
              >
                {t("deleteConfirm.confirmBtn")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
