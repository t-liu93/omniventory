/**
 * M2 Step 8 — frontend tests.
 *
 * Coverage (per §5 "Frontend" / §7.6 / §10 Step 8 blind-review points):
 *
 * 1. Low-stock dashboard tile (Dashboard.tsx / LowStockCard):
 *    a. Renders count badge + short list from GET /api/low-stock response:
 *       - exact mode item: shows current / threshold rendered with formatQuantity.
 *       - level mode item: shows "Low" indicator (no numbers).
 *    b. Empty state renders when GET /api/low-stock returns [].
 *    c. Link to /low-stock view is present when items exist.
 *    d. Link is ABSENT in empty state.
 *    e. Does NOT re-derive the rule client-side (only calls the endpoint once,
 *       never filters or computes anything itself).
 *
 * 2. Low-stock full view (LowStock.tsx):
 *    a. Renders all items from GET /api/low-stock:
 *       - exact mode: shows current / threshold columns.
 *       - level mode: shows "Low" badge, threshold column shows "—".
 *    b. Empty state renders when GET /api/low-stock returns [].
 *
 * 3. Navigation: link from Dashboard tile navigates to /low-stock.
 *
 * Conventions: vitest + Testing Library, mock the typed client, pinned to "en".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard.js";
import { LowStock } from "../pages/LowStock.js";
import i18n from "../i18n/index.js";

/** Mock the typed client module. */
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
type AnyResult = any;

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** exact-mode item: AA Batteries, current 3, threshold 4 */
const exactLowItem = {
  definition_id: 42,
  name: "AA Batteries",
  mode: "exact",
  reason: "below_min_stock",
  current: "3.000000",
  threshold: "4.000000",
};

/** level-mode item: Assorted Screws, stock_level = low */
const levelLowItem = {
  definition_id: 43,
  name: "Assorted Screws",
  mode: "level",
  reason: "level_low",
  current: null,
  threshold: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <MantineProvider>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/low-stock" element={<LowStock />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderLowStockPage() {
  return render(
    <MemoryRouter initialEntries={["/low-stock"]}>
      <MantineProvider>
        <Routes>
          <Route path="/low-stock" element={<LowStock />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── Tests: Dashboard tile — count + list ─────────────────────────────────────

describe("Dashboard — low-stock tile: count + list", () => {
  it("renders count badge and item list when GET /api/low-stock returns items", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem, levelLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-count-badge")).toBeDefined();
    });

    // Count badge text
    expect(screen.getByTestId("low-stock-count-badge").textContent).toMatch(/2 items low/i);

    // Short list is visible
    expect(screen.getByTestId("low-stock-list")).toBeDefined();
  });

  it("renders exact-mode item with current / threshold via formatQuantity", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-item-${exactLowItem.definition_id}`)).toBeDefined();
    });

    const item = screen.getByTestId(`low-stock-item-${exactLowItem.definition_id}`);
    // Should contain the name
    expect(item.textContent).toMatch(/AA Batteries/);
    // Should contain formatted current / threshold (formatQuantity strips trailing zeros)
    expect(item.textContent).toMatch(/3/);
    expect(item.textContent).toMatch(/4/);
  });

  it("renders level-mode item with 'Low' indicator (no numbers)", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [levelLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-item-${levelLowItem.definition_id}`)).toBeDefined();
    });

    const item = screen.getByTestId(`low-stock-item-${levelLowItem.definition_id}`);
    expect(item.textContent).toMatch(/Assorted Screws/);
    // Should show "Low" text (from stock.stockLevel.low)
    expect(item.textContent).toMatch(/low/i);
    // Should NOT show numeric threshold
    expect(item.textContent).not.toMatch(/\/\s*4/);
  });
});

// ── Tests: Dashboard tile — fetch error ──────────────────────────────────────

describe("Dashboard — low-stock tile: fetch error", () => {
  it("shows load-error indicator (not 'all good') when GET /api/low-stock fails", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      error: { detail: "Internal server error" },
      response: new Response(null, { status: 500 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-load-error")).toBeDefined();
    });

    // Must NOT show the misleading "all good" empty state
    expect(screen.queryByTestId("low-stock-empty-state")).toBeNull();
    // Must NOT show count badge
    expect(screen.queryByTestId("low-stock-count-badge")).toBeNull();
  });
});

// ── Tests: Dashboard tile — empty state ──────────────────────────────────────

describe("Dashboard — low-stock tile: empty state", () => {
  it("renders empty state message when GET /api/low-stock returns []", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-empty-state")).toBeDefined();
    });

    expect(screen.getByTestId("low-stock-empty-state").textContent).toMatch(
      /all stock levels look good/i,
    );
  });

  it("does NOT show count badge in empty state", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-empty-state")).toBeDefined();
    });

    expect(screen.queryByTestId("low-stock-count-badge")).toBeNull();
  });

  it("does NOT show the view-all link in empty state", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-empty-state")).toBeDefined();
    });

    expect(screen.queryByTestId("low-stock-view-link")).toBeNull();
  });
});

// ── Tests: Dashboard tile — navigation link ───────────────────────────────────

describe("Dashboard — low-stock tile: link to view", () => {
  it("shows a link to /low-stock when items are present", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-view-link")).toBeDefined();
    });

    const link = screen.getByTestId("low-stock-view-link") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/low-stock");
  });
});

// ── Tests: LowStock full-view page ───────────────────────────────────────────

describe("LowStock page — full list", () => {
  it("renders exact-mode item with current and threshold columns", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-row-${exactLowItem.definition_id}`)).toBeDefined();
    });

    // Current column: formatted quantity
    const currentCell = screen.getByTestId(`low-stock-current-${exactLowItem.definition_id}`);
    expect(currentCell.textContent).toMatch(/3/);

    // Threshold column: formatted threshold
    const thresholdCell = screen.getByTestId(`low-stock-threshold-${exactLowItem.definition_id}`);
    expect(thresholdCell.textContent).toMatch(/4/);
  });

  it("renders level-mode item with Low badge and — threshold", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [levelLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-row-${levelLowItem.definition_id}`)).toBeDefined();
    });

    // Current column: "Low" badge
    expect(screen.getByTestId(`low-stock-level-${levelLowItem.definition_id}`)).toBeDefined();
    expect(
      screen.getByTestId(`low-stock-level-${levelLowItem.definition_id}`).textContent,
    ).toMatch(/low/i);

    // Threshold column: "—"
    const thresholdCell = screen.getByTestId(`low-stock-threshold-${levelLowItem.definition_id}`);
    expect(thresholdCell.textContent).toMatch(/—/);
  });

  it("renders empty state when GET /api/low-stock returns []", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-empty")).toBeDefined();
    });

    expect(screen.getByTestId("low-stock-empty").textContent).toMatch(
      /all stock levels look good/i,
    );
  });

  it("renders the page heading", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /low-stock alert/i }),
      ).toBeDefined();
    });
  });

  it("renders localized ErrorState when GET /api/low-stock fails", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      error: { detail: "Internal server error" },
      response: new Response(null, { status: 500 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      // ErrorState renders with role="alert"
      expect(screen.getByRole("alert")).toBeDefined();
    });

    // Must show the localized load-error message
    expect(screen.getByRole("alert").textContent).toMatch(/failed to load low-stock data/i);
    // Must NOT show empty state
    expect(screen.queryByTestId("low-stock-empty")).toBeNull();
  });
});

// ── Tests: LowStock page — item name links to item detail ────────────────────

describe("LowStock page — item name links to item detail", () => {
  it("renders item name as a link to /items/:definition_id for an exact-mode item", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-row-${exactLowItem.definition_id}`)).toBeDefined();
    });

    const link = screen.getByRole("link", { name: /AA Batteries/i }) as HTMLAnchorElement;
    expect(link).toBeDefined();
    expect(link.getAttribute("href")).toBe(`/items/${exactLowItem.definition_id}`);
  });

  it("renders item name as a link to /items/:definition_id for a level-mode item", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [levelLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-row-${levelLowItem.definition_id}`)).toBeDefined();
    });

    const link = screen.getByRole("link", { name: /Assorted Screws/i }) as HTMLAnchorElement;
    expect(link).toBeDefined();
    expect(link.getAttribute("href")).toBe(`/items/${levelLowItem.definition_id}`);
  });

  it("renders each item name as a link when multiple items are present", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem, levelLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderLowStockPage();

    await waitFor(() => {
      expect(screen.getByTestId(`low-stock-row-${exactLowItem.definition_id}`)).toBeDefined();
    });

    const link1 = screen.getByRole("link", { name: /AA Batteries/i }) as HTMLAnchorElement;
    expect(link1.getAttribute("href")).toBe(`/items/${exactLowItem.definition_id}`);

    const link2 = screen.getByRole("link", { name: /Assorted Screws/i }) as HTMLAnchorElement;
    expect(link2.getAttribute("href")).toBe(`/items/${levelLowItem.definition_id}`);
  });
});

// ── Tests: navigation from dashboard tile to /low-stock ──────────────────────

describe("Dashboard tile → LowStock view navigation", () => {
  it("clicking the view-all link shows the LowStock page", async () => {
    // First call (dashboard): return 1 item to show the link
    // Second call (LowStock page after navigation): return same item
    vi.mocked(client.GET).mockResolvedValue({
      data: [exactLowItem],
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderDashboard();

    // Wait for the link to appear
    await waitFor(() => {
      expect(screen.getByTestId("low-stock-view-link")).toBeDefined();
    });

    // Click the link — MemoryRouter navigates to /low-stock
    screen.getByTestId("low-stock-view-link").click();

    // The LowStock page should now be rendered (heading shows)
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /low-stock alert/i }),
      ).toBeDefined();
    });

    // And the item row should be visible
    await waitFor(() => {
      expect(
        screen.getByTestId(`low-stock-row-${exactLowItem.definition_id}`),
      ).toBeDefined();
    });
  });
});
