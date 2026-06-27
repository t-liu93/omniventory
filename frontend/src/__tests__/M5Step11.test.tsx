/**
 * M5 Step 11 — Barcode scanning + lookup + intake integration.
 *
 * Coverage (per M5 §5, §9 Step 11, §10 Step 11):
 *
 * 1. BarcodeScanner — manual-entry fallback:
 *    a. Renders manual-entry input and submit button.
 *    b. Entering a code and clicking submit calls onDetect with the trimmed code.
 *    c. Pressing Enter in the input also calls onDetect.
 *    d. Empty / whitespace-only code does not call onDetect.
 *
 * 2. BarcodeScanner — camera path (mocked @zxing/browser):
 *    a. Clicking "Start camera" calls BrowserMultiFormatReader.decodeFromConstraints.
 *    b. When the decode callback fires with a result, onDetect is called and the
 *       camera is stopped (reset() called).
 *    c. Clicking "Stop camera" calls reset().
 *    d. Camera permission error (rejected promise) surfaces the permissionDenied message.
 *
 * 3. BarcodeScanModal — lookup → known branch:
 *    a. Manual-entry code triggers GET /api/barcodes/lookup.
 *    b. Known result (found=true) shows the item name + "View item" + "Add a lot" buttons.
 *    c. "Add a lot" calls onAddLot(definitionId).
 *    d. "Scan again" resets to scan phase.
 *
 * 4. BarcodeScanModal — lookup → unknown branch:
 *    a. Unknown result (found=false) shows "Unknown barcode" alert + "Create item" button.
 *    b. "Create item" calls onCreateWithCode(code).
 *    c. When onCreateWithCode is omitted the button is not shown.
 *
 * 5. BarcodePanel — management:
 *    a. Loads and lists barcodes from GET /api/definitions/{id}/barcodes.
 *    b. Empty state shown when no barcodes.
 *    c. Add: filling the code field and clicking Add calls POST and refreshes the list.
 *    d. Duplicate error (barcode.duplicate) is surfaced via mapApiError.
 *    e. Remove: clicking remove icon → confirm modal → confirm calls DELETE and
 *       the barcode disappears from the list.
 *
 * 6. i18n catalog parity for 'barcode' namespace.
 *
 * Conventions: vitest + Testing Library; @zxing/browser mocked with vi.mock;
 * typed client mocked; pinned to 'en'; no @testing-library/jest-dom
 * (use .toBeDefined() / .toBeNull() like sibling M5 tests).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { BarcodeScanner } from "../components/BarcodeScanner.js";
import { BarcodeScanModal } from "../components/BarcodeScanModal.js";
import { BarcodePanel } from "../components/BarcodePanel.js";
import i18n from "../i18n/index.js";

import enBarcode from "../i18n/locales/en/barcode.json";
import zhBarcode from "../i18n/locales/zh/barcode.json";

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
    PUT: vi.fn(),
    PATCH: vi.fn(),
    DELETE: vi.fn(),
  },
}));

// Mock @zxing/browser so no real camera is ever accessed in tests.
vi.mock("@zxing/browser", () => ({
  BrowserMultiFormatReader: vi.fn(),
}));

import { client } from "../api/client.js";
import { BrowserMultiFormatReader } from "@zxing/browser";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

// ── Fixtures ──────────────────────────────────────────────────────────────────

const barcode1: Any = {
  id: 1,
  code: "1234567890123",
  symbology: "ean13",
  label: null,
  definition_id: 10,
  created_at: "2026-06-27T00:00:00Z",
};

const barcode2: Any = {
  id: 2,
  code: "QRTEST",
  symbology: "qr",
  label: "test label",
  definition_id: 10,
  created_at: "2026-06-27T00:00:00Z",
};

const defSummary: Any = { id: 42, name: "Apple Juice" };

const lookupKnownResponse: Any = {
  found: true,
  source: "internal",
  definition: defSummary,
  draft: null,
};

const lookupUnknownResponse: Any = {
  found: false,
  source: null,
  definition: null,
  draft: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <MantineProvider>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="*" element={<>{children}</>} />
        </Routes>
      </MemoryRouter>
    </MantineProvider>
  );
}

/** Build a mock reader object with controllable decodeFromConstraints. */
function makeMockReader() {
  let _capturedCallback: Any = null;
  const mockStop = vi.fn();
  const mockDecodeFromConstraints = vi.fn().mockImplementation(
    (_constraints: Any, _video: Any, callback: Any) => {
      _capturedCallback = callback;
      // Resolves with IScannerControls — the real API shape
      return Promise.resolve({ stop: mockStop });
    },
  );

  vi.mocked(BrowserMultiFormatReader as Any).mockReturnValue({
    decodeFromConstraints: mockDecodeFromConstraints,
  });

  return {
    decodeFromConstraints: mockDecodeFromConstraints,
    /** controls.stop — called by stopScanner() */
    stop: mockStop,
    fireDetect: (text: string) => {
      if (_capturedCallback) _capturedCallback({ getText: () => text }, null);
    },
  };
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
  vi.mocked(client.GET as Any).mockResolvedValue({ data: [], error: undefined });
  vi.mocked(client.POST as Any).mockResolvedValue({ data: null, error: undefined });
  vi.mocked(client.DELETE as Any).mockResolvedValue({ data: null, error: undefined });
  // Default no-op ZXing mock (prevents "not a constructor" errors)
  vi.mocked(BrowserMultiFormatReader as Any).mockReturnValue({
    decodeFromConstraints: vi.fn().mockResolvedValue({ stop: vi.fn() }),
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1. BarcodeScanner — manual entry ─────────────────────────────────────────

describe("BarcodeScanner — manual-entry fallback", () => {
  it("renders manual input and submit button", () => {
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);
    expect(screen.getByTestId("manual-code-input")).toBeDefined();
    expect(screen.getByTestId("manual-submit-btn")).toBeDefined();
  });

  it("clicking submit with a code calls onDetect with trimmed code", () => {
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "  CODE123  " },
    });
    fireEvent.click(screen.getByTestId("manual-submit-btn"));

    expect(onDetect).toHaveBeenCalledWith("CODE123");
  });

  it("pressing Enter in the input also calls onDetect", () => {
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "ENTER_CODE" },
    });
    fireEvent.keyDown(screen.getByTestId("manual-code-input"), { key: "Enter" });

    expect(onDetect).toHaveBeenCalledWith("ENTER_CODE");
  });

  it("whitespace-only code does not call onDetect", () => {
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByTestId("manual-submit-btn"));

    expect(onDetect).not.toHaveBeenCalled();
  });
});

// ── 2. BarcodeScanner — camera path (mocked ZXing) ───────────────────────────

describe("BarcodeScanner — camera path (mocked @zxing/browser)", () => {
  it("clicking Start camera calls decodeFromConstraints on the reader", async () => {
    const reader = makeMockReader();
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    await act(async () => {
      fireEvent.click(screen.getByTestId("start-camera-btn"));
    });

    expect(reader.decodeFromConstraints).toHaveBeenCalledOnce();
  });

  it("decode callback fires → onDetect called and reader reset", async () => {
    const reader = makeMockReader();
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    await act(async () => {
      fireEvent.click(screen.getByTestId("start-camera-btn"));
    });

    await act(async () => {
      reader.fireDetect("EAN_BARCODE");
    });

    expect(onDetect).toHaveBeenCalledWith("EAN_BARCODE");
    // controls.stop() is called by stopScanner after a successful decode
    expect(reader.stop).toHaveBeenCalled();
  });

  it("clicking Stop camera calls reader.reset()", async () => {
    const reader = makeMockReader();
    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    await act(async () => {
      fireEvent.click(screen.getByTestId("start-camera-btn"));
    });

    // After starting, the scanning label should appear
    await waitFor(() => expect(screen.getByTestId("stop-camera-btn")).toBeDefined());

    await act(async () => {
      fireEvent.click(screen.getByTestId("stop-camera-btn"));
    });

    // controls.stop() called by stopScanner when user clicks Stop camera
    expect(reader.stop).toHaveBeenCalled();
  });

  it("camera permission error surfaces permissionDenied message", async () => {
    const permError = Object.assign(new Error("not allowed"), {
      name: "NotAllowedError",
    });
    vi.mocked(BrowserMultiFormatReader as Any).mockReturnValue({
      decodeFromConstraints: vi.fn().mockRejectedValue(permError),
    });

    const onDetect = vi.fn();
    render(<Wrapper><BarcodeScanner onDetect={onDetect} /></Wrapper>);

    await act(async () => {
      fireEvent.click(screen.getByTestId("start-camera-btn"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("camera-error-alert")).toBeDefined(),
    );
    expect(screen.getByTestId("camera-error-alert").textContent).toContain(
      enBarcode.permissionDenied,
    );
  });
});

// ── 3. BarcodeScanModal — known branch ───────────────────────────────────────

describe("BarcodeScanModal — lookup → known branch", () => {
  it("manual entry → GET lookup called with the entered code", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupKnownResponse,
      error: undefined,
    });

    const onClose = vi.fn();
    render(
      <Wrapper>
        <BarcodeScanModal opened={true} onClose={onClose} />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "1234567890123" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });

    await waitFor(() =>
      expect(client.GET).toHaveBeenCalledWith(
        "/api/barcodes/lookup",
        expect.objectContaining({
          params: { query: { code: "1234567890123" } },
        }),
      ),
    );
  });

  it("known result shows item name + View item + Add a lot buttons", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupKnownResponse,
      error: undefined,
    });

    render(
      <Wrapper>
        <BarcodeScanModal opened={true} onClose={vi.fn()} />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "SOME_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("lookup-known-alert")).toBeDefined(),
    );
    expect(screen.getByTestId("view-item-btn")).toBeDefined();
    expect(screen.getByTestId("add-lot-btn")).toBeDefined();
    expect(screen.getByTestId("lookup-known-alert").textContent).toContain(
      defSummary.name,
    );
  });

  it("Add a lot button calls onAddLot with the definition id", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupKnownResponse,
      error: undefined,
    });

    const onAddLot = vi.fn();
    render(
      <Wrapper>
        <BarcodeScanModal opened={true} onClose={vi.fn()} onAddLot={onAddLot} />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "SOME_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("add-lot-btn")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("add-lot-btn"));

    expect(onAddLot).toHaveBeenCalledWith(defSummary.id);
  });

  it("Scan again button resets to scan phase", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupKnownResponse,
      error: undefined,
    });

    render(
      <Wrapper>
        <BarcodeScanModal opened={true} onClose={vi.fn()} />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "SOME_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("scan-again-btn")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("scan-again-btn"));

    // Should be back to scan phase — manual input visible again
    await waitFor(() =>
      expect(screen.getByTestId("manual-code-input")).toBeDefined(),
    );
    expect(screen.queryByTestId("lookup-known-alert")).toBeNull();
  });
});

// ── 4. BarcodeScanModal — unknown branch ─────────────────────────────────────

describe("BarcodeScanModal — lookup → unknown branch", () => {
  it("unknown result shows unknown-alert and Create item button", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupUnknownResponse,
      error: undefined,
    });

    const onCreateWithCode = vi.fn();
    render(
      <Wrapper>
        <BarcodeScanModal
          opened={true}
          onClose={vi.fn()}
          onCreateWithCode={onCreateWithCode}
        />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "UNKNOWN_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("lookup-unknown-alert")).toBeDefined(),
    );
    expect(screen.getByTestId("lookup-unknown-alert").textContent).toContain(
      "UNKNOWN_CODE",
    );
    expect(screen.getByTestId("create-item-btn")).toBeDefined();
  });

  it("Create item button calls onCreateWithCode with the scanned code", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupUnknownResponse,
      error: undefined,
    });

    const onCreateWithCode = vi.fn();
    render(
      <Wrapper>
        <BarcodeScanModal
          opened={true}
          onClose={vi.fn()}
          onCreateWithCode={onCreateWithCode}
        />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "NEW_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("create-item-btn")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("create-item-btn"));

    expect(onCreateWithCode).toHaveBeenCalledWith("NEW_CODE");
  });

  it("Create item button not rendered when onCreateWithCode is omitted", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: lookupUnknownResponse,
      error: undefined,
    });

    render(
      <Wrapper>
        <BarcodeScanModal opened={true} onClose={vi.fn()} />
      </Wrapper>,
    );

    fireEvent.change(screen.getByTestId("manual-code-input"), {
      target: { value: "SOME_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("manual-submit-btn"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("lookup-unknown-alert")).toBeDefined(),
    );
    expect(screen.queryByTestId("create-item-btn")).toBeNull();
  });
});

// ── 5. BarcodePanel — management ─────────────────────────────────────────────

describe("BarcodePanel — barcode management", () => {
  it("loads and displays barcodes from GET /api/definitions/{id}/barcodes", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: [barcode1, barcode2],
      error: undefined,
    });

    render(<Wrapper><BarcodePanel definitionId={10} /></Wrapper>);

    await waitFor(() =>
      expect(screen.getByTestId("barcode-row-1")).toBeDefined(),
    );
    expect(screen.getByTestId("barcode-code-1").textContent).toBe(barcode1.code);
    expect(screen.getByTestId("barcode-row-2")).toBeDefined();
  });

  it("shows empty state when no barcodes", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: [],
      error: undefined,
    });

    render(<Wrapper><BarcodePanel definitionId={10} /></Wrapper>);

    await waitFor(() =>
      expect(screen.getByTestId("barcode-empty-state")).toBeDefined(),
    );
    expect(screen.getByTestId("barcode-empty-state").textContent).toContain(
      enBarcode.emptyState,
    );
  });

  it("Add: fills code field + clicks Add → POST called + list refreshed", async () => {
    const newBarcode = { ...barcode1, id: 99, code: "NEW_EAN" };
    vi.mocked(client.GET as Any)
      .mockResolvedValueOnce({ data: [], error: undefined }) // initial load
      .mockResolvedValueOnce({ data: [newBarcode], error: undefined }); // reload after add
    vi.mocked(client.POST as Any).mockResolvedValue({
      data: newBarcode,
      error: undefined,
    });

    render(<Wrapper><BarcodePanel definitionId={10} /></Wrapper>);

    await waitFor(() =>
      expect(screen.getByTestId("barcode-empty-state")).toBeDefined(),
    );

    fireEvent.change(screen.getByTestId("barcode-code-input"), {
      target: { value: "NEW_EAN" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("add-barcode-btn"));
    });

    expect(client.POST).toHaveBeenCalledWith(
      "/api/definitions/{definition_id}/barcodes",
      expect.objectContaining({
        params: { path: { definition_id: 10 } },
        body: expect.objectContaining({ code: "NEW_EAN" }),
      }),
    );

    await waitFor(() =>
      expect(screen.getByTestId("barcode-row-99")).toBeDefined(),
    );
  });

  it("duplicate barcode error is surfaced via mapApiError", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({ data: [], error: undefined });
    vi.mocked(client.POST as Any).mockResolvedValue({
      data: null,
      error: { code: "barcode.duplicate", message: "duplicate" },
    });

    render(<Wrapper><BarcodePanel definitionId={10} /></Wrapper>);

    await waitFor(() =>
      expect(screen.getByTestId("barcode-empty-state")).toBeDefined(),
    );

    fireEvent.change(screen.getByTestId("barcode-code-input"), {
      target: { value: "DUP_CODE" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("add-barcode-btn"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("add-barcode-error")).toBeDefined(),
    );
    // The mapApiError call should have localized the barcode.duplicate key
    expect(screen.getByTestId("add-barcode-error").textContent).toContain(
      "already bound",
    );
  });

  it("Remove: click remove → confirm modal → DELETE called → barcode gone", async () => {
    vi.mocked(client.GET as Any)
      .mockResolvedValueOnce({ data: [barcode1], error: undefined }) // initial load
      .mockResolvedValueOnce({ data: [], error: undefined }); // reload after delete
    vi.mocked(client.DELETE as Any).mockResolvedValue({
      data: null,
      error: undefined,
    });

    render(<Wrapper><BarcodePanel definitionId={10} /></Wrapper>);

    await waitFor(() =>
      expect(screen.getByTestId("barcode-row-1")).toBeDefined(),
    );

    fireEvent.click(screen.getByTestId("remove-barcode-1"));

    // Confirm modal appears
    await waitFor(() =>
      expect(screen.getByTestId("confirm-remove-barcode-btn")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("confirm-remove-barcode-btn"));
    });

    expect(client.DELETE).toHaveBeenCalledWith(
      "/api/barcodes/{barcode_id}",
      expect.objectContaining({ params: { path: { barcode_id: 1 } } }),
    );

    await waitFor(() =>
      expect(screen.queryByTestId("barcode-row-1")).toBeNull(),
    );
  });
});

// ── 6. i18n catalog parity ────────────────────────────────────────────────────

function collectKeys(obj: unknown, prefix = ""): string[] {
  if (typeof obj !== "object" || obj === null) return [prefix];
  const keys: string[] = [];
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      keys.push(...collectKeys(value, path));
    } else {
      keys.push(path);
    }
  }
  return keys;
}

describe("i18n catalog parity — barcode namespace", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("en and zh barcode namespace have identical keys", () => {
    const enKeys = collectKeys(enBarcode).sort();
    const zhKeys = collectKeys(zhBarcode).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(missingInZh, "Keys in en/barcode missing from zh/barcode").toEqual([]);
    expect(extraInZh, "Extra keys in zh/barcode not in en/barcode").toEqual([]);
  });

  it("barcode.scanBtn resolves in both en and zh", async () => {
    expect(i18n.t("scanBtn", { ns: "barcode" })).toBe(enBarcode.scanBtn);

    await i18n.changeLanguage("zh");
    const zhVal = i18n.t("scanBtn", { ns: "barcode" });
    expect(zhVal).not.toBe(enBarcode.scanBtn);
    expect(zhVal.trim().length).toBeGreaterThan(0);
  });
});
