/**
 * BarcodeScanModal — lookup + branch modal that wraps BarcodeScanner.
 *
 * Flow:
 *  1. Renders BarcodeScanner (camera + manual entry).
 *  2. On detect: calls GET /api/barcodes/lookup?code=.
 *  3. Known result (found=true, definition present):
 *       - Shows "Item found: <name>" with "View item" and "Add a lot" buttons.
 *       - "View item" → navigate to /items/:id.
 *       - "Add a lot" → calls onAddLot(definitionId); caller opens intake modal.
 *  4. Unknown result (found=false):
 *       - Shows "Unknown barcode: <code>" with "Create item with this code" button.
 *       - Calls onCreateWithCode(code); caller opens create-definition modal.
 *  5. "Scan again" button resets to the scan phase.
 *  6. Closing the modal always resets state.
 *
 * M5 Step 11.
 */

import { useState, useCallback, useEffect } from "react";
import {
  Modal,
  Stack,
  Text,
  Button,
  Group,
  Alert,
  Loader,
} from "@mantine/core";
import { CheckCircle, HelpCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { client } from "../api/client";
import { BarcodeScanner } from "./BarcodeScanner";
import type { components } from "../api/schema";

// ── Types ─────────────────────────────────────────────────────────────────────

type BarcodeLookupResponse = components["schemas"]["BarcodeLookupResponse"];

type ScanPhase = "scan" | "looking-up" | "known" | "unknown";

export interface BarcodeScanModalProps {
  opened: boolean;
  onClose: () => void;
  /**
   * Called when the user taps "Add a lot" for a known barcode.
   * Receives the matched definition id.  When omitted the modal just
   * navigates to the item (same as "View item").
   */
  onAddLot?: (definitionId: number) => void;
  /**
   * Called when the user taps "Create item with this code" for an unknown
   * barcode.  Receives the scanned code string so the caller can pre-bind it.
   */
  onCreateWithCode?: (code: string) => void;
}

// ── BarcodeScanModal ──────────────────────────────────────────────────────────

export function BarcodeScanModal({
  opened,
  onClose,
  onAddLot,
  onCreateWithCode,
}: BarcodeScanModalProps) {
  const { t } = useTranslation("barcode");
  const navigate = useNavigate();

  const [phase, setPhase] = useState<ScanPhase>("scan");
  const [lookup, setLookup] = useState<BarcodeLookupResponse | null>(null);
  const [scannedCode, setScannedCode] = useState<string>("");

  // Reset state whenever the modal is opened so stale results don't linger.
  useEffect(() => {
    if (opened) {
      setPhase("scan");
      setLookup(null);
      setScannedCode("");
    }
  }, [opened]);

  function resetToScan() {
    setPhase("scan");
    setLookup(null);
    setScannedCode("");
  }

  function handleClose() {
    resetToScan();
    onClose();
  }

  const handleDetect = useCallback(
    async (code: string) => {
      setScannedCode(code);
      setPhase("looking-up");
      const { data } = await client.GET("/api/barcodes/lookup", {
        params: { query: { code } },
      });
      if (data?.found && data.definition) {
        setLookup(data);
        setPhase("known");
      } else {
        setLookup(data ?? null);
        setPhase("unknown");
      }
    },
    [],
  );

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={t("scanBtn")}
      size="sm"
      data-testid="barcode-scan-modal"
    >
      <Stack gap="md">
        {/* ── Phase: scan ────────────────────────────────────────────────── */}
        {phase === "scan" && <BarcodeScanner onDetect={handleDetect} />}

        {/* ── Phase: looking-up ──────────────────────────────────────────── */}
        {phase === "looking-up" && (
          <Group justify="center" gap="xs">
            <Loader size="sm" />
            <Text size="sm">{t("scanning")}</Text>
          </Group>
        )}

        {/* ── Phase: known ───────────────────────────────────────────────── */}
        {phase === "known" && lookup?.definition && (
          <Stack gap="sm">
            <Alert
              icon={<CheckCircle size={16} />}
              color="green"
              variant="light"
              data-testid="lookup-known-alert"
            >
              {t("lookupKnown.message", { name: lookup.definition.name })}
            </Alert>
            <Group gap="xs">
              <Button
                variant="light"
                onClick={() => {
                  navigate(`/items/${lookup.definition!.id}`);
                  handleClose();
                }}
                data-testid="view-item-btn"
              >
                {t("lookupKnown.viewItem")}
              </Button>
              <Button
                onClick={() => {
                  if (onAddLot) {
                    onAddLot(lookup.definition!.id);
                  } else {
                    navigate(`/items/${lookup.definition!.id}`);
                  }
                  handleClose();
                }}
                data-testid="add-lot-btn"
              >
                {t("lookupKnown.addLot")}
              </Button>
            </Group>
            <Button
              variant="subtle"
              size="xs"
              onClick={resetToScan}
              data-testid="scan-again-btn"
            >
              {t("lookupKnown.scanAgain")}
            </Button>
          </Stack>
        )}

        {/* ── Phase: unknown ─────────────────────────────────────────────── */}
        {phase === "unknown" && (
          <Stack gap="sm">
            <Alert
              icon={<HelpCircle size={16} />}
              color="blue"
              variant="light"
              data-testid="lookup-unknown-alert"
            >
              {t("lookupUnknown.message", { code: scannedCode })}
            </Alert>
            {onCreateWithCode && (
              <Button
                onClick={() => {
                  onCreateWithCode(scannedCode);
                  handleClose();
                }}
                data-testid="create-item-btn"
              >
                {t("lookupUnknown.createItem")}
              </Button>
            )}
            <Button
              variant="subtle"
              size="xs"
              onClick={resetToScan}
              data-testid="scan-again-unknown-btn"
            >
              {t("lookupUnknown.scanAgain")}
            </Button>
          </Stack>
        )}
      </Stack>
    </Modal>
  );
}
