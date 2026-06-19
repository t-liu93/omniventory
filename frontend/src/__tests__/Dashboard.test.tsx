/**
 * Dashboard page tests.
 *
 * After M2 Step 8:
 * - Card 1 (expiryCard): static placeholder — Best-before / Expiry (M3).
 * - Card 2 (durableCard): static placeholder linking to /items.
 * - Card 3 (lowStockCard): LIVE tile fetching GET /api/low-stock.
 *
 * The tile tests are in M2Step8.test.tsx.  This suite checks the overall
 * page heading + the static cards that remain unchanged.
 *
 * The API client is mocked because Dashboard now makes a GET /api/low-stock
 * call via the LowStockCard sub-component.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard.js";
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

beforeEach(async () => {
  await i18n.changeLanguage("en");
  // Default: return empty low-stock list so static cards are visible.
  vi.mocked(client.GET).mockResolvedValue({
    data: [],
    response: new Response(null, { status: 200 }),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any);
});

function renderDashboard() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <Dashboard />
      </MantineProvider>
    </MemoryRouter>,
  );
}

describe("Dashboard — page structure", () => {
  it("renders the page heading 'Dashboard'", () => {
    renderDashboard();
    expect(screen.getByRole("heading", { name: /dashboard/i })).toBeDefined();
  });

  it("renders the Best-before / Expiry card", () => {
    renderDashboard();
    expect(
      screen.getByRole("heading", { name: /best-before/i }),
    ).toBeDefined();
  });

  it("renders the Durable-goods card", () => {
    renderDashboard();
    expect(
      screen.getByRole("heading", { name: /durable-goods/i }),
    ).toBeDefined();
  });

  it("renders the Low-stock tile", () => {
    renderDashboard();
    expect(
      screen.getByRole("heading", { name: /low-stock alert/i }),
    ).toBeDefined();
  });

  it("durable-goods card links to the existing /items route", async () => {
    renderDashboard();
    await waitFor(() => {
      const link = screen.getByRole("link", { name: /items/i });
      expect(link).toBeDefined();
      expect((link as HTMLAnchorElement).getAttribute("href")).toBe("/items");
    });
  });
});
