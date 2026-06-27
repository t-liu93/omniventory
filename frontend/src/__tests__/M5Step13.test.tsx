/**
 * M5 Step 13 — Data export UI.
 *
 * Coverage (per M5 §7.5, §9 Step 13, §10 Step 13):
 *
 * 1. exportUrl() pure function — all entity × format combinations
 *    a. item_definitions + csv
 *    b. item_definitions + json
 *    c. stock_instances + csv
 *    d. stock_instances + json
 *    e. locations + csv
 *    f. locations + json
 *
 * 2. ExportMenu — clicking CSV triggers a download of the right URL/format
 *    a. item_definitions CSV
 *    b. item_definitions JSON
 *    c. stock_instances CSV
 *    d. locations JSON
 *
 * 3. ExportMenu on the Items host page
 *    a. Both item_definitions and stock_instances menus are rendered with distinct labels
 *    b. Opening the item_definitions menu shows CSV + JSON entries
 *    c. The two Items-toolbar buttons carry distinct visible labels (not both "Export")
 *
 * 4. i18n catalog parity — export namespace en+zh have identical keys
 *    a. key sets match
 *    b. export.menuLabel resolves in both en and zh
 *    c. entityLabel keys resolve to distinct, non-empty strings in en and zh
 *
 * Conventions: vitest + Testing Library; typed client mocked; pinned to 'en';
 * no @testing-library/jest-dom (use .toBeDefined() / .toBeNull() like siblings).
 * Download triggering is verified by spying on HTMLAnchorElement.prototype.click.
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
import { MemoryRouter } from "react-router-dom";

import { exportUrl } from "../components/exportUtils.js";
import { ExportMenu } from "../components/ExportMenu.js";
import { Items } from "../pages/Items.js";
import i18n from "../i18n/index.js";

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
    PATCH: vi.fn(),
    DELETE: vi.fn(),
  },
}));

import { client } from "../api/client.js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

// ── Helpers ───────────────────────────────────────────────────────────────────

function wrapMenu(entity: "item_definitions" | "stock_instances" | "locations") {
  return render(
    <MantineProvider>
      <ExportMenu entity={entity} />
    </MantineProvider>,
  );
}

// ── 1. exportUrl() pure function ──────────────────────────────────────────────

describe("exportUrl — pure URL builder", () => {
  it("item_definitions + csv → /api/export/item_definitions?format=csv", () => {
    expect(exportUrl("item_definitions", "csv")).toBe(
      "/api/export/item_definitions?format=csv",
    );
  });

  it("item_definitions + json → /api/export/item_definitions?format=json", () => {
    expect(exportUrl("item_definitions", "json")).toBe(
      "/api/export/item_definitions?format=json",
    );
  });

  it("stock_instances + csv → /api/export/stock_instances?format=csv", () => {
    expect(exportUrl("stock_instances", "csv")).toBe(
      "/api/export/stock_instances?format=csv",
    );
  });

  it("stock_instances + json → /api/export/stock_instances?format=json", () => {
    expect(exportUrl("stock_instances", "json")).toBe(
      "/api/export/stock_instances?format=json",
    );
  });

  it("locations + csv → /api/export/locations?format=csv", () => {
    expect(exportUrl("locations", "csv")).toBe(
      "/api/export/locations?format=csv",
    );
  });

  it("locations + json → /api/export/locations?format=json", () => {
    expect(exportUrl("locations", "json")).toBe(
      "/api/export/locations?format=json",
    );
  });
});

// ── 2. ExportMenu — download trigger ─────────────────────────────────────────

describe("ExportMenu — download trigger", () => {
  /**
   * Capture every URL that a programmatic anchor click is attempted for.
   * The spy intercepts HTMLAnchorElement.prototype.click and records the
   * fully-resolved href at the time of the call.
   */
  let clickedUrls: string[];

  beforeEach(async () => {
    await i18n.changeLanguage("en");
    clickedUrls = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clickedUrls.push(this.href);
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("clicking Export CSV triggers anchor download for item_definitions CSV", async () => {
    wrapMenu("item_definitions");

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-menu-item_definitions"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("export-csv-item_definitions")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-csv-item_definitions"));
    });

    expect(
      clickedUrls.some((u) =>
        u.includes("/api/export/item_definitions?format=csv"),
      ),
    ).toBe(true);
  });

  it("clicking Export JSON triggers anchor download for item_definitions JSON", async () => {
    wrapMenu("item_definitions");

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-menu-item_definitions"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("export-json-item_definitions")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-json-item_definitions"));
    });

    expect(
      clickedUrls.some((u) =>
        u.includes("/api/export/item_definitions?format=json"),
      ),
    ).toBe(true);
  });

  it("clicking Export CSV triggers anchor download for stock_instances CSV", async () => {
    wrapMenu("stock_instances");

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-menu-stock_instances"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("export-csv-stock_instances")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-csv-stock_instances"));
    });

    expect(
      clickedUrls.some((u) =>
        u.includes("/api/export/stock_instances?format=csv"),
      ),
    ).toBe(true);
  });

  it("clicking Export JSON triggers anchor download for locations JSON", async () => {
    wrapMenu("locations");

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-menu-locations"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("export-json-locations")).toBeDefined(),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("export-json-locations"));
    });

    expect(
      clickedUrls.some((u) =>
        u.includes("/api/export/locations?format=json"),
      ),
    ).toBe(true);
  });
});

// ── 3. ExportMenu on the Items host page ──────────────────────────────────────

describe("ExportMenu entries on the Items page", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    // Return empty lists for all GET calls so the page loads without error.
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: [],
      error: undefined,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  function renderItemsPage() {
    return render(
      <MemoryRouter>
        <MantineProvider>
          <Items />
        </MantineProvider>
      </MemoryRouter>,
    );
  }

  it("renders export menus for item_definitions and stock_instances with distinct labels", async () => {
    renderItemsPage();

    await waitFor(() =>
      expect(screen.getByTestId("export-menu-item_definitions")).toBeDefined(),
    );
    expect(screen.getByTestId("export-menu-stock_instances")).toBeDefined();

    // Buttons must carry distinct entity-specific labels (not both plain "Export").
    const defBtn = screen.getByTestId("export-menu-item_definitions");
    const instBtn = screen.getByTestId("export-menu-stock_instances");
    expect(defBtn.textContent).toBe("Export items");
    expect(instBtn.textContent).toBe("Export instances");
    expect(defBtn.textContent).not.toBe(instBtn.textContent);
  });

  it("item_definitions export menu contains CSV and JSON entries (en)", async () => {
    renderItemsPage();

    await waitFor(() =>
      expect(screen.getByTestId("export-menu-item_definitions")).toBeDefined(),
    );

    // Open the dropdown
    await act(async () => {
      fireEvent.click(screen.getByTestId("export-menu-item_definitions"));
    });

    await waitFor(() =>
      expect(screen.getByTestId("export-csv-item_definitions")).toBeDefined(),
    );
    expect(screen.getByTestId("export-json-item_definitions")).toBeDefined();

    // Verify English labels
    expect(screen.getByTestId("export-csv-item_definitions").textContent).toBe(
      "Export CSV",
    );
    expect(screen.getByTestId("export-json-item_definitions").textContent).toBe(
      "Export JSON",
    );
  });

  it("the two export buttons are distinguishable by their accessible name (en)", async () => {
    renderItemsPage();

    // Both menus must be findable by their unique button text alone,
    // confirming a screen-reader user can tell them apart.
    await waitFor(() =>
      expect(screen.getByText("Export items")).toBeDefined(),
    );
    expect(screen.getByText("Export instances")).toBeDefined();
    // There must be NO generic "Export" button (the old indistinguishable label).
    expect(screen.queryByText("Export")).toBeNull();
  });
});

// ── 4. i18n catalog parity — export namespace ─────────────────────────────────

import enExport from "../i18n/locales/en/export.json";
import zhExport from "../i18n/locales/zh/export.json";

function collectKeys(obj: unknown, prefix = ""): string[] {
  if (typeof obj !== "object" || obj === null) return [prefix];
  const keys: string[] = [];
  for (const [key, value] of Object.entries(
    obj as Record<string, unknown>,
  )) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value)
    ) {
      keys.push(...collectKeys(value, path));
    } else {
      keys.push(path);
    }
  }
  return keys;
}

describe("i18n catalog parity — export namespace", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("en and zh export namespace have identical keys", () => {
    const enKeys = collectKeys(enExport).sort();
    const zhKeys = collectKeys(zhExport).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(
      missingInZh,
      "Keys in en/export missing from zh/export",
    ).toEqual([]);
    expect(
      extraInZh,
      "Extra keys in zh/export not in en/export",
    ).toEqual([]);
  });

  it("export.menuLabel resolves in both en and zh", async () => {
    expect(i18n.t("menuLabel", { ns: "export" })).toBe(enExport.menuLabel);

    await i18n.changeLanguage("zh");
    const zhVal = i18n.t("menuLabel", { ns: "export" });
    expect(zhVal).not.toBe(enExport.menuLabel);
    expect(zhVal.trim().length).toBeGreaterThan(0);
  });

  it("entityLabel keys resolve to distinct non-empty strings in en and zh", async () => {
    await i18n.changeLanguage("en");

    const enDef = i18n.t("entityLabel.item_definitions", { ns: "export" });
    const enInst = i18n.t("entityLabel.stock_instances", { ns: "export" });
    const enLoc = i18n.t("entityLabel.locations", { ns: "export" });

    // All three must be non-empty.
    expect(enDef.trim().length).toBeGreaterThan(0);
    expect(enInst.trim().length).toBeGreaterThan(0);
    expect(enLoc.trim().length).toBeGreaterThan(0);
    // All three must be distinct.
    expect(enDef).not.toBe(enInst);
    expect(enInst).not.toBe(enLoc);
    expect(enDef).not.toBe(enLoc);

    await i18n.changeLanguage("zh");

    const zhDef = i18n.t("entityLabel.item_definitions", { ns: "export" });
    const zhInst = i18n.t("entityLabel.stock_instances", { ns: "export" });
    const zhLoc = i18n.t("entityLabel.locations", { ns: "export" });

    // zh strings must differ from en strings (not falling through to English fallback).
    expect(zhDef).not.toBe(enDef);
    expect(zhInst).not.toBe(enInst);
    expect(zhLoc).not.toBe(enLoc);
    // All three zh strings must be non-empty and distinct.
    expect(zhDef.trim().length).toBeGreaterThan(0);
    expect(zhInst.trim().length).toBeGreaterThan(0);
    expect(zhLoc.trim().length).toBeGreaterThan(0);
    expect(zhDef).not.toBe(zhInst);
  });
});
