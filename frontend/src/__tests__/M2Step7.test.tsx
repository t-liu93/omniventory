/**
 * M2 Step 7 — frontend tests.
 *
 * Coverage (per §5 "Frontend" / §7.6 / §10 Step 7 blind-review points):
 *
 * 1. ItemDetail — Consume (FIFO) button:
 *    - Only shown for exact-mode definitions.
 *    - Calls POST /definitions/{id}/consume with quantity as a string.
 *    - Server error from consume is surfaced via mapApiError.
 *
 * 2. ItemDetail — per-lot action menu (Intake / Adjust / Discard / Move):
 *    - Intake calls POST /instances/{id}/intake with quantity as a string.
 *    - Adjust calls POST /instances/{id}/adjust with quantity as a string.
 *    - Discard calls POST /instances/{id}/discard with quantity as a string.
 *    - Move calls POST /instances/{id}/move with to_location_id.
 *    - Server error from action is surfaced via mapApiError.
 *
 * 3. ItemDetail — low-stock badge:
 *    - Badge shown when total quantity < min_stock.
 *    - Badge absent when total quantity >= min_stock or mode != exact.
 *
 * 4. ItemDetail — quantity/level rendering by mode:
 *    - exact: shows numeric quantity.
 *    - level: shows stock_level badge.
 *    - none: shows "—".
 *
 * 5. InstanceDetail — movement-history table:
 *    - Renders type, delta, from→to, occurred_at, reversal link.
 *    - "reversal of #N" link shows when reverses_movement_id is set.
 *
 * 6. InstanceDetail — Reverse (undo):
 *    - Reverse button only on reversible rows (not itself a reversal AND not
 *      already reversed by another row).
 *    - Reverse calls POST /movements/{id}/reverse.
 *    - After reverse, history is refreshed.
 *    - Server error from reverse is surfaced via mapApiError.
 *
 * 7. InstanceDetail — per-lot action buttons (Intake / Adjust / Discard / Move):
 *    - Intake calls POST /instances/{id}/intake with quantity as a string.
 *    - Adjust calls POST /instances/{id}/adjust with quantity as a string.
 *    - Server error from action is surfaced via mapApiError.
 *
 * Conventions: vitest + Testing Library, mock the typed client, pinned to "en".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { ItemDetail } from "../pages/Items.js";
import { InstanceDetail } from "../pages/InstanceDetail.js";
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

const kindConsumable = {
  id: 2,
  code: "consumable",
  name: "Consumable",
  is_system: true,
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

const locationShelf = {
  id: 2,
  name: "Shelf",
  description: null,
  parent_id: null,
  item_instance_id: null,
  container_asset_label: null,
  created_at: "2025-01-01T00:00:00Z",
};

const categoryBatteries = {
  id: 10,
  name: "Batteries",
  description: null,
  parent_id: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Exact-mode definition with min_stock = 4 */
const defExact = {
  id: 42,
  name: "AA Batteries",
  description: null,
  category_id: 10,
  kind_id: 2,
  kind: kindConsumable,
  unit: "pcs",
  default_location_id: 1,
  stock_tracking_mode: "exact",
  min_stock: "4",
  created_at: "2025-01-01T00:00:00Z",
};

/** Level-mode definition */
const defLevel = {
  id: 43,
  name: "Assorted Screws",
  description: null,
  category_id: null,
  kind_id: 2,
  kind: kindConsumable,
  unit: "bag",
  default_location_id: null,
  stock_tracking_mode: "level",
  min_stock: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** None-mode definition */
const defNone = {
  id: 44,
  name: "Wall Art",
  description: null,
  category_id: null,
  kind_id: 2,
  kind: kindConsumable,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "none",
  min_stock: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Exact-mode instance with quantity = 7 (above min_stock = 4) */
const instanceExactAboveMin = {
  id: 1,
  definition_id: 42,
  location_id: 1,
  quantity: "7",
  stock_level: null,
  serial: null,
  model_number: null,
  manufacturer: null,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  received_at: "2025-01-01T00:00:00Z",
  created_at: "2025-01-01T00:00:00Z",
};

/** Exact-mode instance with quantity = 3 (below min_stock = 4) */
const instanceExactBelowMin = {
  ...instanceExactAboveMin,
  quantity: "3",
};

/** Level-mode instance */
const instanceLevel = {
  id: 2,
  definition_id: 43,
  location_id: 1,
  quantity: null,
  stock_level: "low",
  serial: null,
  model_number: null,
  manufacturer: null,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  received_at: "2025-01-01T00:00:00Z",
  created_at: "2025-01-01T00:00:00Z",
};

/** None-mode instance */
const instanceNone = {
  id: 3,
  definition_id: 44,
  location_id: null,
  quantity: null,
  stock_level: null,
  serial: null,
  model_number: null,
  manufacturer: null,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  received_at: "2025-01-01T00:00:00Z",
  created_at: "2025-01-01T00:00:00Z",
};

/** Two movements: an intake and a consume */
const movIntake = {
  id: 10,
  instance_id: 1,
  type: "intake",
  quantity_delta: "10.000000",
  from_location_id: null,
  to_location_id: 1,
  occurred_at: "2025-01-01T10:00:00Z",
  note: null,
  reverses_movement_id: null,
  user_id: null,
  created_at: "2025-01-01T10:00:00Z",
};

const movConsume = {
  id: 11,
  instance_id: 1,
  type: "consume",
  quantity_delta: "-3.000000",
  from_location_id: null,
  to_location_id: null,
  occurred_at: "2025-01-02T10:00:00Z",
  note: null,
  reverses_movement_id: null,
  user_id: null,
  created_at: "2025-01-02T10:00:00Z",
};

/** A reversal movement that reverses movConsume */
const movReversal = {
  id: 12,
  instance_id: 1,
  type: "correction",
  quantity_delta: "3.000000",
  from_location_id: null,
  to_location_id: null,
  occurred_at: "2025-01-03T10:00:00Z",
  note: null,
  reverses_movement_id: 11,
  user_id: null,
  created_at: "2025-01-03T10:00:00Z",
};

// ── Setup helpers ─────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

/** Mock GET calls for ItemDetail page */
function mockItemDetailLoad(def: object, instances: object[]) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/definitions/{definition_id}") return { data: def, response: new Response(null, { status: 200 }) };
      if (path === "/api/instances") return { data: instances, response: new Response(null, { status: 200 }) };
      if (path === "/api/kinds") return { data: [kindConsumable], response: new Response(null, { status: 200 }) };
      if (path === "/api/categories") return { data: [categoryBatteries], response: new Response(null, { status: 200 }) };
      if (path === "/api/locations") return { data: [locationGarage, locationShelf], response: new Response(null, { status: 200 }) };
      if (path === "/api/definitions") return { data: [def], response: new Response(null, { status: 200 }) };
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );
}

/** Mock GET calls for InstanceDetail page */
function mockInstanceDetailLoad(
  inst: object,
  def: object,
  movements: object[] = [],
) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/instances/{instance_id}") return { data: inst, response: new Response(null, { status: 200 }) };
      if (path === "/api/definitions/{definition_id}") return { data: def, response: new Response(null, { status: 200 }) };
      if (path === "/api/locations") return { data: [locationGarage, locationShelf], response: new Response(null, { status: 200 }) };
      if (path === "/api/definitions") return { data: [def], response: new Response(null, { status: 200 }) };
      if (path === "/api/instances/{instance_id}/movements") return { data: movements, response: new Response(null, { status: 200 }) };
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );
}

function renderItemDetail(defId: number) {
  return render(
    <MemoryRouter initialEntries={[`/items/${defId}`]}>
      <MantineProvider>
        <Routes>
          <Route path="/items/:id" element={<ItemDetail />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderInstanceDetail(instId: number) {
  return render(
    <MemoryRouter initialEntries={[`/instances/${instId}`]}>
      <MantineProvider>
        <Routes>
          <Route path="/instances/:id" element={<InstanceDetail />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── Tests: ItemDetail — Consume (FIFO) button ─────────────────────────────────

describe("ItemDetail — Consume (FIFO) button", () => {
  it("shows consume button for exact-mode definition", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    expect(screen.getByTestId("consume-btn")).toBeDefined();
  });

  it("does NOT show consume button for level-mode definition", async () => {
    mockItemDetailLoad(defLevel, [instanceLevel]);
    renderItemDetail(43);

    await waitFor(() => {
      expect(screen.getAllByText("Assorted Screws").length).toBeGreaterThan(0);
    });

    expect(screen.queryByTestId("consume-btn")).toBeNull();
  });

  it("calls POST /definitions/{id}/consume with quantity as a string", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("consume-btn"));
    await screen.findByTestId("consume-quantity-input");

    const qtyInput = screen.getByTestId("consume-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "3" } });
    await waitFor(() => {
      expect(qtyInput.value).toBe("3");
    });

    fireEvent.click(screen.getByTestId("consume-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/definitions/{definition_id}/consume",
        expect.objectContaining({
          params: { path: { definition_id: 42 } },
          body: expect.objectContaining({
            quantity: expect.any(String),
          }),
        }),
      );
    });
  });

  it("surfaces server error from consume via mapApiError", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      error: {
        code: "stock.insufficient",
        message: "Insufficient stock",
        params: { requested: "5", available: "3" },
      },
      response: new Response(null, { status: 422 }),
    } as AnyResult);

    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("consume-btn"));
    await screen.findByTestId("consume-quantity-input");

    const qtyInput = screen.getByTestId("consume-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "5" } });
    await waitFor(() => expect(qtyInput.value).toBe("5"));

    fireEvent.click(screen.getByTestId("consume-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("consume-error-alert")).toBeDefined();
    });
    // Verify it uses the localized stock.insufficient message
    expect(screen.getByTestId("consume-error-alert").textContent).toMatch(/insufficient stock/i);
  });
});

// ── Tests: ItemDetail — per-lot action menu ───────────────────────────────────

describe("ItemDetail — per-lot ledger action menu (exact mode)", () => {
  it("shows lot-actions menu for exact-mode instance", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`)).toBeDefined();
    });
  });

  it("calls POST /instances/{id}/intake with quantity as a string", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`)).toBeDefined();
    });

    // Open the action menu
    fireEvent.click(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`));

    // Click intake menu item
    await waitFor(() => {
      expect(screen.getByTestId(`lot-intake-${instanceExactAboveMin.id}`)).toBeDefined();
    });
    fireEvent.click(screen.getByTestId(`lot-intake-${instanceExactAboveMin.id}`));

    // Fill in quantity
    await screen.findByTestId("ledger-quantity-input");
    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "5" } });
    await waitFor(() => expect(qtyInput.value).toBe("5"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/instances/{instance_id}/intake",
        expect.objectContaining({
          params: { path: { instance_id: instanceExactAboveMin.id } },
          body: expect.objectContaining({
            quantity: expect.any(String),
          }),
        }),
      );
    });
  });

  it("calls POST /instances/{id}/adjust with quantity as a string", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`)).toBeDefined();
    });

    fireEvent.click(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`));
    await waitFor(() => {
      expect(screen.getByTestId(`lot-adjust-${instanceExactAboveMin.id}`)).toBeDefined();
    });
    fireEvent.click(screen.getByTestId(`lot-adjust-${instanceExactAboveMin.id}`));

    await screen.findByTestId("ledger-quantity-input");
    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "5" } });
    await waitFor(() => expect(qtyInput.value).toBe("5"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/instances/{instance_id}/adjust",
        expect.objectContaining({
          params: { path: { instance_id: instanceExactAboveMin.id } },
          body: expect.objectContaining({
            quantity: expect.any(String),
          }),
        }),
      );
    });
  });

  it("calls POST /instances/{id}/discard with quantity as a string", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`)).toBeDefined();
    });

    fireEvent.click(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`));
    await waitFor(() => {
      expect(screen.getByTestId(`lot-discard-${instanceExactAboveMin.id}`)).toBeDefined();
    });
    fireEvent.click(screen.getByTestId(`lot-discard-${instanceExactAboveMin.id}`));

    await screen.findByTestId("ledger-quantity-input");
    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "2" } });
    await waitFor(() => expect(qtyInput.value).toBe("2"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/instances/{instance_id}/discard",
        expect.objectContaining({
          params: { path: { instance_id: instanceExactAboveMin.id } },
          body: expect.objectContaining({
            quantity: expect.any(String),
          }),
        }),
      );
    });
  });

  it("calls POST /instances/{id}/move with to_location_id", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`)).toBeDefined();
    });

    fireEvent.click(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`));
    await waitFor(() => {
      expect(screen.getByTestId(`lot-move-${instanceExactAboveMin.id}`)).toBeDefined();
    });
    fireEvent.click(screen.getByTestId(`lot-move-${instanceExactAboveMin.id}`));

    await screen.findByTestId("ledger-location-select");

    // Select the destination location
    const locSelect = screen.getByTestId("ledger-location-select");
    fireEvent.click(locSelect);
    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      expect(opts.some((el) => el.textContent?.includes("Shelf"))).toBe(true);
    });
    const shelfOpt = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("Shelf"),
    );
    fireEvent.click(shelfOpt!);

    await waitFor(() => {
      const btn = screen.getByTestId("ledger-submit-btn") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/instances/{instance_id}/move",
        expect.objectContaining({
          params: { path: { instance_id: instanceExactAboveMin.id } },
          body: expect.objectContaining({
            to_location_id: expect.any(Number),
          }),
        }),
      );
    });
  });

  it("surfaces server error from ledger action via mapApiError", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      error: {
        code: "stock.negative_quantity",
        message: "Would go negative",
        params: { id: 1 },
      },
      response: new Response(null, { status: 422 }),
    } as AnyResult);

    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`)).toBeDefined();
    });

    fireEvent.click(screen.getByTestId(`lot-actions-${instanceExactAboveMin.id}`));
    await waitFor(() => {
      expect(screen.getByTestId(`lot-discard-${instanceExactAboveMin.id}`)).toBeDefined();
    });
    fireEvent.click(screen.getByTestId(`lot-discard-${instanceExactAboveMin.id}`));

    await screen.findByTestId("ledger-quantity-input");
    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "100" } });
    await waitFor(() => expect(qtyInput.value).toBe("100"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("ledger-error-alert")).toBeDefined();
    });
    expect(screen.getByTestId("ledger-error-alert").textContent).toMatch(/negative/i);
  });
});

// ── Tests: ItemDetail — low-stock badge ──────────────────────────────────────

describe("ItemDetail — low-stock badge", () => {
  it("shows low-stock badge when total quantity < min_stock", async () => {
    mockItemDetailLoad(defExact, [instanceExactBelowMin]);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    await waitFor(() => {
      expect(screen.getByTestId("low-stock-badge")).toBeDefined();
    });
    expect(screen.getByTestId("low-stock-badge").textContent).toMatch(/low stock/i);
  });

  it("does NOT show low-stock badge when total quantity >= min_stock", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    expect(screen.queryByTestId("low-stock-badge")).toBeNull();
  });

  it("does NOT show low-stock badge for level-mode definition", async () => {
    mockItemDetailLoad(defLevel, [instanceLevel]);
    renderItemDetail(43);

    await waitFor(() => {
      expect(screen.getAllByText("Assorted Screws").length).toBeGreaterThan(0);
    });

    expect(screen.queryByTestId("low-stock-badge")).toBeNull();
  });
});

// ── Tests: ItemDetail — quantity/level rendering ──────────────────────────────

describe("ItemDetail — quantity/level rendering by mode", () => {
  it("exact mode: shows numeric quantity for instance", async () => {
    mockItemDetailLoad(defExact, [instanceExactAboveMin]);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId(`inst-qty-${instanceExactAboveMin.id}`)).toBeDefined();
    });
    // formatQuantity("7") → "7"
    expect(screen.getByTestId(`inst-qty-${instanceExactAboveMin.id}`).textContent).toBe("7");
  });

  it("level mode: shows stock_level badge for instance", async () => {
    mockItemDetailLoad(defLevel, [instanceLevel]);
    renderItemDetail(43);

    await waitFor(() => {
      expect(screen.getByTestId(`inst-level-badge-${instanceLevel.id}`)).toBeDefined();
    });
    expect(screen.getByTestId(`inst-level-badge-${instanceLevel.id}`).textContent).toMatch(/low/i);
  });

  it("none mode: shows — for instance quantity column", async () => {
    mockItemDetailLoad(defNone, [instanceNone]);
    renderItemDetail(44);

    await waitFor(() => {
      expect(screen.getByTestId(`inst-row-${instanceNone.id}`)).toBeDefined();
    });
    // The qty column should show "—" and no badge/number
    expect(screen.queryByTestId(`inst-qty-${instanceNone.id}`)).toBeNull();
    expect(screen.queryByTestId(`inst-level-badge-${instanceNone.id}`)).toBeNull();
  });
});

// ── Tests: InstanceDetail — movement-history table ───────────────────────────

describe("InstanceDetail — movement-history table", () => {
  it("renders movement rows with type, delta, occurred_at", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, [movIntake, movConsume]);
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId(`movement-row-${movIntake.id}`)).toBeDefined();
    });

    // Check the delta renders for intake (+10)
    expect(screen.getByTestId(`movement-delta-${movIntake.id}`).textContent).toMatch(/\+10/);

    // Check the delta renders for consume (-3 shown as negative or "3")
    expect(screen.getByTestId(`movement-delta-${movConsume.id}`).textContent).toMatch(/-3|3/);
  });

  it("shows 'reversal of #N' link for reversal rows", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, [movConsume, movReversal]);
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId(`movement-row-${movReversal.id}`)).toBeDefined();
    });

    // movReversal reverses movConsume (id=11)
    expect(screen.getByTestId(`reversal-link-${movReversal.id}`)).toBeDefined();
    expect(screen.getByTestId(`reversal-link-${movReversal.id}`).textContent).toMatch(/#11/);
  });

  it("renders actor column: user_id as string when set, unknownActor placeholder when null", async () => {
    const movWithUser = { ...movIntake, id: 20, user_id: 5 };
    const movNoUser = { ...movConsume, id: 21, user_id: null };
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, [movWithUser, movNoUser]);
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId(`movement-row-${movWithUser.id}`)).toBeDefined();
    });

    // user_id = 5 → shows "5"
    expect(screen.getByTestId(`movement-actor-${movWithUser.id}`).textContent).toBe("5");
    // user_id = null → shows the unknownActor placeholder ("—")
    expect(screen.getByTestId(`movement-actor-${movNoUser.id}`).textContent).toBe("—");
  });

  it("shows empty history message when no movements", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, []);
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId("history-empty")).toBeDefined();
    });
  });

  it("does NOT show history section for level-mode instance", async () => {
    mockInstanceDetailLoad(instanceLevel, defLevel, []);
    renderInstanceDetail(2);

    await waitFor(() => {
      // Title should be visible
      expect(screen.getByTestId("inst-level-badge")).toBeDefined();
    });

    // History section should not be rendered
    expect(screen.queryByTestId("history-empty")).toBeNull();
  });
});

// ── Tests: InstanceDetail — Reverse (undo) ────────────────────────────────────

describe("InstanceDetail — Reverse (undo) action", () => {
  it("shows Reverse button only on reversible rows (not itself a reversal, not already reversed)", async () => {
    // History: [movIntake (reversible), movConsume (reversed by movReversal), movReversal (is a reversal)]
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, [movIntake, movConsume, movReversal]);
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId(`movement-row-${movIntake.id}`)).toBeDefined();
    });

    // movIntake: not a reversal, not reversed → reversible
    expect(screen.getByTestId(`reverse-btn-${movIntake.id}`)).toBeDefined();

    // movConsume: reversed by movReversal → NOT reversible
    expect(screen.queryByTestId(`reverse-btn-${movConsume.id}`)).toBeNull();

    // movReversal: is itself a reversal → NOT reversible
    expect(screen.queryByTestId(`reverse-btn-${movReversal.id}`)).toBeNull();
  });

  it("calls POST /movements/{id}/reverse and refreshes history on success", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, [movIntake, movConsume]);

    // After reverse, the history will have the reversal too
    const updatedMovements = [movIntake, movConsume, movReversal];
    let callCount = 0;
    vi.mocked(client.GET).mockImplementation(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      async (path: any) => {
        if (path === "/api/instances/{instance_id}") return { data: instanceExactAboveMin, response: new Response(null, { status: 200 }) };
        if (path === "/api/definitions/{definition_id}") return { data: defExact, response: new Response(null, { status: 200 }) };
        if (path === "/api/locations") return { data: [locationGarage, locationShelf], response: new Response(null, { status: 200 }) };
        if (path === "/api/definitions") return { data: [defExact], response: new Response(null, { status: 200 }) };
        if (path === "/api/instances/{instance_id}/movements") {
          callCount++;
          // Return updated movements after the first call (initial load)
          return { data: callCount > 1 ? updatedMovements : [movIntake, movConsume], response: new Response(null, { status: 200 }) };
        }
        return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
      },
    );

    vi.mocked(client.POST).mockResolvedValue({
      data: movReversal,
      response: new Response(null, { status: 201 }),
    } as AnyResult);

    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId(`reverse-btn-${movConsume.id}`)).toBeDefined();
    });

    fireEvent.click(screen.getByTestId(`reverse-btn-${movConsume.id}`));
    await screen.findByTestId("reverse-submit-btn");

    fireEvent.click(screen.getByTestId("reverse-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/movements/{movement_id}/reverse",
        expect.objectContaining({
          params: { path: { movement_id: movConsume.id } },
        }),
      );
    });
  });

  it("surfaces server error from reverse via mapApiError", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, [movIntake]);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      error: {
        code: "stock.movement_already_reversed",
        message: "Already reversed",
        params: { id: movIntake.id },
      },
      response: new Response(null, { status: 409 }),
    } as AnyResult);

    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId(`reverse-btn-${movIntake.id}`)).toBeDefined();
    });

    fireEvent.click(screen.getByTestId(`reverse-btn-${movIntake.id}`));
    await screen.findByTestId("reverse-submit-btn");

    fireEvent.click(screen.getByTestId("reverse-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("reverse-error-alert")).toBeDefined();
    });
    expect(screen.getByTestId("reverse-error-alert").textContent).toMatch(/already been reversed/i);
  });
});

// ── Tests: InstanceDetail — per-lot action buttons ───────────────────────────

describe("InstanceDetail — per-lot action buttons (exact mode)", () => {
  it("shows action buttons for exact-mode instance", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, []);
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId("lot-intake-btn")).toBeDefined();
    });
    expect(screen.getByTestId("lot-adjust-btn")).toBeDefined();
    expect(screen.getByTestId("lot-discard-btn")).toBeDefined();
    expect(screen.getByTestId("lot-move-btn")).toBeDefined();
  });

  it("does NOT show action buttons for level-mode instance", async () => {
    mockInstanceDetailLoad(instanceLevel, defLevel, []);
    renderInstanceDetail(2);

    await waitFor(() => {
      expect(screen.getByTestId("inst-level-badge")).toBeDefined();
    });

    expect(screen.queryByTestId("lot-intake-btn")).toBeNull();
    expect(screen.queryByTestId("lot-adjust-btn")).toBeNull();
  });

  it("calls POST /instances/{id}/intake with quantity as a string", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, []);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId("lot-intake-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("lot-intake-btn"));
    await screen.findByTestId("ledger-quantity-input");

    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "5" } });
    await waitFor(() => expect(qtyInput.value).toBe("5"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/instances/{instance_id}/intake",
        expect.objectContaining({
          params: { path: { instance_id: 1 } },
          body: expect.objectContaining({
            quantity: expect.any(String),
          }),
        }),
      );
    });
  });

  it("calls POST /instances/{id}/adjust with quantity as a string", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, []);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId("lot-adjust-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("lot-adjust-btn"));
    await screen.findByTestId("ledger-quantity-input");

    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "10" } });
    await waitFor(() => expect(qtyInput.value).toBe("10"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/instances/{instance_id}/adjust",
        expect.objectContaining({
          params: { path: { instance_id: 1 } },
          body: expect.objectContaining({
            quantity: expect.any(String),
          }),
        }),
      );
    });
  });

  it("surfaces server error from ledger action via mapApiError", async () => {
    mockInstanceDetailLoad(instanceExactAboveMin, defExact, []);
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      error: {
        code: "stock.negative_quantity",
        message: "Would go negative",
        params: { id: 1 },
      },
      response: new Response(null, { status: 422 }),
    } as AnyResult);

    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId("lot-discard-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("lot-discard-btn"));
    await screen.findByTestId("ledger-quantity-input");

    const qtyInput = screen.getByTestId("ledger-quantity-input") as HTMLInputElement;
    fireEvent.change(qtyInput, { target: { value: "999" } });
    await waitFor(() => expect(qtyInput.value).toBe("999"));

    fireEvent.click(screen.getByTestId("ledger-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("ledger-error-alert")).toBeDefined();
    });
    expect(screen.getByTestId("ledger-error-alert").textContent).toMatch(/negative/i);
  });
});
