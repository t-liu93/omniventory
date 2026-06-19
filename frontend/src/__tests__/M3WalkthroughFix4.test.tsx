/**
 * M3 Walkthrough Fix 4 — ItemDetail lot-row UX tests.
 *
 * Coverage:
 *
 * 1. Whole-row clickable:
 *    a. Clicking a lot row navigates to /instances/:id.
 *    b. Pressing Enter on a focused row navigates.
 *    c. Pressing Space on a focused row navigates.
 *
 * 2. Action buttons do NOT trigger row navigation (stopPropagation):
 *    a. Clicking the edit ActionIcon does NOT navigate.
 *    b. Clicking the delete ActionIcon does NOT navigate.
 *    c. (exact mode) Clicking the lot-actions menu trigger does NOT navigate.
 *
 * 3. Data-driven column visibility:
 *    a. Milk-like lots (no serial/mfr/warranty, has best_before):
 *       - Serial, Manufacturer, Warranty headers ABSENT.
 *       - Best Before header PRESENT.
 *    b. Durable-like lots (serial/mfr/warranty present, no best_before):
 *       - Serial, Manufacturer, Warranty headers PRESENT.
 *       - Best Before header ABSENT.
 *    c. Empty instances: all four optional columns hidden; no crash.
 *
 * Conventions: vitest + Testing Library, mock the typed client, pinned to "en".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { ItemDetail } from "../pages/Items.js";
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

// ── Fixtures ──────────────────────────────────────────────────────────────────

const kindConsumable = {
  id: 2,
  code: "consumable",
  name: "Consumable",
  is_system: true,
  created_at: "2025-01-01T00:00:00Z",
};

const kindDurable = {
  id: 1,
  code: "durable",
  name: "Durable",
  is_system: true,
  created_at: "2025-01-01T00:00:00Z",
};

const locationFridge = {
  id: 5,
  name: "Fridge",
  description: null,
  parent_id: null,
  item_instance_id: null,
  container_asset_label: null,
  created_at: "2025-01-01T00:00:00Z",
};

const locationGarage = {
  id: 1,
  name: "Garage",
  description: null,
  parent_id: null,
  item_instance_id: null,
  container_asset_label: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** A milk-like consumable definition (best-before days set, no serial/mfr/warranty). */
const defMilk = {
  id: 10,
  name: "Milk",
  description: null,
  category_id: null,
  kind_id: 2,
  kind: kindConsumable,
  unit: "L",
  default_location_id: 5,
  stock_tracking_mode: "exact",
  min_stock: null,
  default_best_before_days: 7,
  created_at: "2025-01-01T00:00:00Z",
};

/** A durable-like definition (serial/mfr/warranty fields present). */
const defHammer = {
  id: 20,
  name: "Rotary Hammer",
  description: null,
  category_id: null,
  kind_id: 1,
  kind: kindDurable,
  unit: "pcs",
  default_location_id: 1,
  stock_tracking_mode: "exact",
  min_stock: null,
  default_best_before_days: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Milk lot — has best_before_date, no serial/mfr/warranty. */
const instanceMilk = {
  id: 101,
  definition_id: 10,
  location_id: 5,
  quantity: "2.000000",
  stock_level: null,
  serial: null,
  model_number: null,
  manufacturer: null,
  best_before_date: "2026-06-26",
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Hammer lot — has serial/mfr/warranty, no best_before. */
const instanceHammer = {
  id: 201,
  definition_id: 20,
  location_id: 1,
  quantity: "1",
  stock_level: null,
  serial: "SN-HAMMER-1",
  model_number: "GBH 2-26",
  manufacturer: "Bosch",
  best_before_date: null,
  warranty_expires: "2028-06-01",
  warranty_details: "3-year limited",
  purchase_price: "299.00",
  purchase_date: "2025-01-01",
  purchase_source: "Local store",
  created_at: "2025-01-01T00:00:00Z",
};

// ── Render helpers ────────────────────────────────────────────────────────────

function renderMilkDetail() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/definitions/{definition_id}") {
      return { data: defMilk, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/instances") {
      return { data: [instanceMilk], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/kinds") {
      return { data: [kindConsumable], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/categories") {
      return { data: [], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/locations") {
      return { data: [locationFridge], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/definitions") {
      return { data: [defMilk], response: new Response(null, { status: 200 }) };
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
  });

  return render(
    <MemoryRouter initialEntries={["/items/10"]}>
      <MantineProvider>
        <Routes>
          <Route path="/items/:id" element={<ItemDetail />} />
          <Route path="/instances/:id" element={<div data-testid="instance-detail-page">Instance Detail</div>} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderHammerDetail() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/definitions/{definition_id}") {
      return { data: defHammer, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/instances") {
      return { data: [instanceHammer], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/kinds") {
      return { data: [kindDurable], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/categories") {
      return { data: [], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/locations") {
      return { data: [locationGarage], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/definitions") {
      return { data: [defHammer], response: new Response(null, { status: 200 }) };
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
  });

  return render(
    <MemoryRouter initialEntries={["/items/20"]}>
      <MantineProvider>
        <Routes>
          <Route path="/items/:id" element={<ItemDetail />} />
          <Route path="/instances/:id" element={<div data-testid="instance-detail-page">Instance Detail</div>} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderEmptyInstances() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/definitions/{definition_id}") {
      return { data: defMilk, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/instances") {
      return { data: [], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/kinds") {
      return { data: [kindConsumable], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/categories") {
      return { data: [], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/locations") {
      return { data: [locationFridge], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/definitions") {
      return { data: [defMilk], response: new Response(null, { status: 200 }) };
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
  });

  return render(
    <MemoryRouter initialEntries={["/items/10"]}>
      <MantineProvider>
        <Routes>
          <Route path="/items/:id" element={<ItemDetail />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

// ── 1. Whole-row clickable ────────────────────────────────────────────────────

describe("ItemDetail — clickable lot rows", () => {
  it("clicking a lot row navigates to /instances/:id", async () => {
    renderMilkDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-101")).toBeDefined();
    });

    const row = screen.getByTestId("inst-row-101");
    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getByTestId("instance-detail-page")).toBeDefined();
    });
  });

  it("pressing Enter on a focused lot row navigates to /instances/:id", async () => {
    renderMilkDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-101")).toBeDefined();
    });

    const row = screen.getByTestId("inst-row-101");
    fireEvent.keyDown(row, { key: "Enter" });

    await waitFor(() => {
      expect(screen.getByTestId("instance-detail-page")).toBeDefined();
    });
  });

  it("pressing Space on a focused lot row navigates to /instances/:id", async () => {
    renderMilkDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-101")).toBeDefined();
    });

    const row = screen.getByTestId("inst-row-101");
    fireEvent.keyDown(row, { key: " " });

    await waitFor(() => {
      expect(screen.getByTestId("instance-detail-page")).toBeDefined();
    });
  });

  it("lot row has role=button and tabIndex=0", async () => {
    renderMilkDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-101")).toBeDefined();
    });

    const row = screen.getByTestId("inst-row-101");
    expect(row.getAttribute("role")).toBe("button");
    expect(row.getAttribute("tabindex")).toBe("0");
  });
});

// ── 2. Action buttons do NOT navigate ────────────────────────────────────────

describe("ItemDetail — action buttons don't trigger row navigation", () => {
  it("clicking the edit button does NOT navigate (stopPropagation)", async () => {
    renderHammerDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-201")).toBeDefined();
    });

    const editBtn = screen.getByTestId("edit-inst-201");
    fireEvent.click(editBtn);

    // The instance detail page should NOT appear
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByTestId("instance-detail-page")).toBeNull();
    // We should still be on the item detail page
    expect(screen.getByTestId("inst-row-201")).toBeDefined();
  });

  it("clicking the delete button does NOT navigate (stopPropagation)", async () => {
    renderHammerDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-201")).toBeDefined();
    });

    const deleteBtn = screen.getByTestId("delete-inst-201");
    fireEvent.click(deleteBtn);

    // The instance detail page should NOT appear
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByTestId("instance-detail-page")).toBeNull();
    // A delete modal should appear instead
    expect(screen.getByTestId("inst-row-201")).toBeDefined();
  });

  it("clicking the lot-actions menu trigger does NOT navigate (stopPropagation)", async () => {
    renderHammerDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-201")).toBeDefined();
    });

    const actionsBtn = screen.getByTestId("lot-actions-201");
    fireEvent.click(actionsBtn);

    // The instance detail page should NOT appear
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByTestId("instance-detail-page")).toBeNull();
    // We should still be on the item detail page
    expect(screen.getByTestId("inst-row-201")).toBeDefined();
  });
});

// ── 3. Data-driven column visibility ─────────────────────────────────────────

describe("ItemDetail — data-driven column visibility (milk-like: best_before only)", () => {
  it("hides Serial, Manufacturer, Warranty column headers for milk lots", async () => {
    renderMilkDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-101")).toBeDefined();
    });

    // Optional columns that should be absent
    expect(screen.queryByRole("columnheader", { name: /serial/i })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /manufacturer/i })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /warranty/i })).toBeNull();

    // Best Before should be present (milk has best_before_date)
    expect(screen.getByRole("columnheader", { name: /best before/i })).toBeDefined();

    // Always-present columns
    expect(screen.getByRole("columnheader", { name: /qty/i })).toBeDefined();
    expect(screen.getByRole("columnheader", { name: /location/i })).toBeDefined();
  });
});

describe("ItemDetail — data-driven column visibility (durable-like: serial/mfr/warranty only)", () => {
  it("shows Serial, Manufacturer, Warranty headers and hides Best Before for hammer lots", async () => {
    renderHammerDetail();

    await waitFor(() => {
      expect(screen.getByTestId("inst-row-201")).toBeDefined();
    });

    // Optional columns that should be present
    expect(screen.getByRole("columnheader", { name: /serial/i })).toBeDefined();
    expect(screen.getByRole("columnheader", { name: /manufacturer/i })).toBeDefined();
    expect(screen.getByRole("columnheader", { name: /warranty/i })).toBeDefined();

    // Best Before should be absent (hammer has no best_before_date)
    expect(screen.queryByRole("columnheader", { name: /best before/i })).toBeNull();

    // Always-present columns
    expect(screen.getByRole("columnheader", { name: /qty/i })).toBeDefined();
    expect(screen.getByRole("columnheader", { name: /location/i })).toBeDefined();
  });
});

describe("ItemDetail — data-driven column visibility (empty instances)", () => {
  it("hides all four optional column headers when no instances exist", async () => {
    renderEmptyInstances();

    await waitFor(() => {
      // The empty state message should appear instead of the table
      expect(screen.getByText(/no instances yet/i)).toBeDefined();
    });

    // No optional column headers at all (table not rendered)
    expect(screen.queryByRole("columnheader", { name: /serial/i })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /manufacturer/i })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /warranty/i })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: /best before/i })).toBeNull();
  });
});
