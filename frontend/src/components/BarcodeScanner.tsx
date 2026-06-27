/**
 * BarcodeScanner — camera + manual-entry fallback for barcode scanning.
 *
 * Features:
 *  - Camera: wraps `@zxing/browser` BrowserMultiFormatReader for live 1D + 2D
 *    decode (EAN, UPC, QR, Code128, …).  Requests environment-facing camera.
 *    `decodeFromConstraints` returns IScannerControls; `.stop()` stops the scan.
 *  - Manual fallback: always visible TextInput so the user can type a code
 *    directly without a camera.  Works in non-HTTPS contexts (e.g. tests).
 *  - Camera permission denial and no-camera errors are caught and surfaced
 *    as a localized message; manual entry stays available.
 *  - Cleanup: `controls.stop()` is called on unmount and when the user stops
 *    the scanner explicitly, so the camera stream is always released.
 *
 * Mockability (tests):
 *  `@zxing/browser` is a regular module import — `vi.mock('@zxing/browser')`
 *  replaces `BrowserMultiFormatReader` with a jest-fn, so no real camera is
 *  accessed in tests.  The mock's `decodeFromConstraints` can return a Promise
 *  that resolves with `{ stop: vi.fn() }`, and the decode callback (3rd arg)
 *  can be captured from `mock.calls[0][2]` and called manually.
 *
 * HTTPS note: cameras require a secure context (HTTPS or localhost).
 * Non-secure origins will produce a `NotAllowedError` which is caught and
 * surfaced via the `permissionDenied` i18n key.
 *
 * M5 Step 11.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { BrowserMultiFormatReader } from "@zxing/browser";
import type { IScannerControls } from "@zxing/browser";
import {
  Stack,
  Group,
  Text,
  TextInput,
  Button,
  Alert,
} from "@mantine/core";
import { AlertCircle, Camera, CameraOff } from "react-feather";
import { useTranslation } from "react-i18next";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface BarcodeScannerProps {
  /** Called with the decoded/entered code (trimmed, non-empty). */
  onDetect: (code: string) => void;
}

// ── BarcodeScanner ────────────────────────────────────────────────────────────

export function BarcodeScanner({ onDetect }: BarcodeScannerProps) {
  const { t } = useTranslation("barcode");

  const videoRef = useRef<HTMLVideoElement>(null);
  /** IScannerControls returned by decodeFromConstraints; null when not scanning. */
  const controlsRef = useRef<IScannerControls | null>(null);

  const [scanning, setScanning] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [manualCode, setManualCode] = useState("");

  /** Stop the active scanner and release the camera stream. */
  const stopScanner = useCallback(() => {
    if (controlsRef.current) {
      controlsRef.current.stop();
      controlsRef.current = null;
    }
    setScanning(false);
  }, []);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (controlsRef.current) {
        controlsRef.current.stop();
        controlsRef.current = null;
      }
    };
  }, []);

  /** Start the camera scanner.  Sets cameraError on permission/device failure. */
  const startScanner = useCallback(async () => {
    setCameraError(null);
    setScanning(true);
    try {
      const reader = new BrowserMultiFormatReader();
      const controls = await reader.decodeFromConstraints(
        { video: { facingMode: "environment" } },
        videoRef.current as HTMLVideoElement,
        (result, error) => {
          if (result) {
            stopScanner();
            onDetect(result.getText());
          }
          // Ignore NotFoundException — no barcode in frame is expected.
          if (error && error.name !== "NotFoundException") {
            // Non-fatal scan errors; startup failures are caught below.
          }
        },
      );
      // Store controls so we can stop on demand or unmount.
      controlsRef.current = controls;
    } catch (err: unknown) {
      setScanning(false);
      controlsRef.current = null;
      if (err instanceof Error) {
        if (
          err.name === "NotAllowedError" ||
          err.name === "PermissionDeniedError"
        ) {
          setCameraError(t("permissionDenied"));
        } else {
          setCameraError(t("noCameraAvailable"));
        }
      } else {
        setCameraError(t("noCameraAvailable"));
      }
    }
  }, [onDetect, stopScanner, t]);

  /** Submit the manual entry field. */
  const handleManualSubmit = useCallback(() => {
    const code = manualCode.trim();
    if (code) {
      setManualCode("");
      onDetect(code);
    }
  }, [manualCode, onDetect]);

  return (
    <Stack gap="sm">
      {/* Camera section ─────────────────────────────────────────────────── */}
      <Stack gap="xs">
        {/* The video element is always in the DOM so the ref is valid; hidden
            when the scanner is not running (display:none keeps the element). */}
        <video
          ref={videoRef}
          data-testid="scanner-video"
          style={{
            width: "100%",
            maxHeight: 240,
            display: scanning ? "block" : "none",
            borderRadius: 4,
            objectFit: "cover",
            background: "#000",
          }}
        />

        {cameraError && (
          <Alert
            icon={<AlertCircle size={16} />}
            color="orange"
            variant="light"
            data-testid="camera-error-alert"
          >
            {cameraError}
          </Alert>
        )}

        <Group gap="xs">
          {!scanning ? (
            <Button
              leftSection={<Camera size={14} />}
              onClick={() => void startScanner()}
              size="sm"
              variant="light"
              data-testid="start-camera-btn"
            >
              {t("startCamera")}
            </Button>
          ) : (
            <Button
              leftSection={<CameraOff size={14} />}
              onClick={stopScanner}
              size="sm"
              variant="subtle"
              data-testid="stop-camera-btn"
            >
              {t("stopCamera")}
            </Button>
          )}
          {scanning && (
            <Text size="sm" c="dimmed" data-testid="scanning-label">
              {t("scanning")}
            </Text>
          )}
        </Group>
      </Stack>

      {/* Manual entry fallback ───────────────────────────────────────────── */}
      <Stack gap="xs">
        <Text size="sm" c="dimmed">
          {t("manualEntry")}
        </Text>
        <Group gap="xs" wrap="nowrap">
          <TextInput
            placeholder={t("manualPlaceholder")}
            value={manualCode}
            onChange={(e) => setManualCode(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleManualSubmit();
            }}
            style={{ flex: 1 }}
            data-testid="manual-code-input"
          />
          <Button
            onClick={handleManualSubmit}
            disabled={!manualCode.trim()}
            data-testid="manual-submit-btn"
          >
            {t("manualSubmit")}
          </Button>
        </Group>
      </Stack>
    </Stack>
  );
}
