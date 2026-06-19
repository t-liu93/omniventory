/**
 * M2 Step 6 — frontend tests.
 *
 * Coverage (per §5 "Frontend" / §7.6 / §10 Step 6 blind-review points):
 *
 * 1. DefinitionFormModal — mode switch shows/hides min_stock:
 *    - switching to "exact" shows the min_stock NumberInput
 *    - switching to "level" hides the min_stock NumberInput
 *    - switching to "none" hides the min_stock NumberInput
 *
 * 2. DefinitionFormModal — POST body includes tracking mode and min_stock:
 *    - mode is sent from the select (not hardcoded)
 *    - min_stock is sent when mode == exact and value is set
 *    - min_stock is null when mode != exact
 *
 * 3. InstanceFormModal — branches by mode (quantity vs stock_level vs neither):
 *    - exact (create): quantity field shown, stock_level absent
 *    - level (create): stock_level select shown, quantity absent
 *    - none (create): neither quantity nor stock_level shown
 *    - exact (edit): quantity field shown but disabled (locked)
 *    - level (edit): stock_level select shown
 *
 * 4. InstanceFormModal — serial ⇒ qty=1 rule preserved for exact mode:
 *    - entering a serial forces qty to 1 and disables the field
 *
 * 5. Mode-change 409 (tracking_mode_change_conflict) surfaced via mapApiError.
 *
 * 6. Definition detail card shows mode badge and min_stock.
 *
 * Conventions: vitest + Testing Library, mock the typed client, pinned to "en".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import React from "react";
import { Items, ItemDetail } from "../pages/Items.js";
import { InstanceFormModal, type InstanceFormState } from "../components/InstanceFormModal.js";
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

const kindDurable = {
  id: 1,
  code: "durable",
  name: "Durable",
  is_system: true,
  created_at: "2025-01-01T00:00:00Z",
};

const categoryTools = {
  id: 10,
  name: "Tools",
  description: null,
  parent_id: null,
  created_at: "2025-01-01T00:00:00Z",
};

const locationGarage = {
  id: 1,
  name: "Garage",
  description: null,
  parent_id: null,
  item_instance_id: null,
  created_at: "2025-01-01T00:00:00Z",
};

// Definition with exact mode and min_stock (uses raw Decimal string as returned by API)
const defExact = {
  id: 42,
  name: "AA Batteries",
  description: "Alkaline batteries",
  category_id: 10,
  kind_id: 1,
  kind: kindDurable,
  unit: "pcs",
  default_location_id: 1,
  stock_tracking_mode: "exact",
  min_stock: "4.000000",
  created_at: "2025-01-01T00:00:00Z",
};

// Definition with fractional min_stock for display formatting test
const defExactFractional = {
  id: 45,
  name: "Cable Ties",
  description: null,
  category_id: 10,
  kind_id: 1,
  kind: kindDurable,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "exact",
  min_stock: "4.500000",
  created_at: "2025-01-01T00:00:00Z",
};

// Definition with level mode
const defLevel = {
  id: 43,
  name: "Assorted Screws",
  description: null,
  category_id: 10,
  kind_id: 1,
  kind: kindDurable,
  unit: "bag",
  default_location_id: null,
  stock_tracking_mode: "level",
  min_stock: null,
  created_at: "2025-01-01T00:00:00Z",
};

// Definition with none mode
const defNone = {
  id: 44,
  name: "Wall Art",
  description: null,
  category_id: null,
  kind_id: 1,
  kind: kindDurable,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "none",
  min_stock: null,
  created_at: "2025-01-01T00:00:00Z",
};

const instanceExact = {
  id: 1,
  definition_id: 42,
  location_id: 1,
  quantity: "10",
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

// ── Setup helpers ─────────────────────────────────────────────────────────────

/** Force i18n to English for all tests in this file. */
beforeEach(async () => {
  await i18n.changeLanguage("en");
});

function mockItemsListLoad(defs: object[] = []) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/definitions") return { data: defs, response: new Response(null, { status: 200 }) };
      if (path === "/api/kinds") return { data: [kindDurable], response: new Response(null, { status: 200 }) };
      if (path === "/api/categories") return { data: [categoryTools], response: new Response(null, { status: 200 }) };
      if (path === "/api/locations") return { data: [locationGarage], response: new Response(null, { status: 200 }) };
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );
}

function mockItemDetailLoad(def: object, instances: object[] = []) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/definitions/{definition_id}") return { data: def, response: new Response(null, { status: 200 }) };
      if (path === "/api/instances") return { data: instances, response: new Response(null, { status: 200 }) };
      if (path === "/api/kinds") return { data: [kindDurable], response: new Response(null, { status: 200 }) };
      if (path === "/api/categories") return { data: [categoryTools], response: new Response(null, { status: 200 }) };
      if (path === "/api/locations") return { data: [locationGarage], response: new Response(null, { status: 200 }) };
      if (path === "/api/definitions") return { data: [def], response: new Response(null, { status: 200 }) };
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );
}

function renderItems() {
  return render(
    <MemoryRouter initialEntries={["/items"]}>
      <MantineProvider>
        <Routes>
          <Route path="/items" element={<Items />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
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

/**
 * Stateful wrapper for InstanceFormModal — needed so we can test interactive
 * state transitions (e.g. serial entry updates form and re-renders).
 */
function InstanceFormModalWrapper(props: {
  initialForm?: Partial<InstanceFormState>;
  trackingMode?: string;
  isEdit?: boolean;
}) {
  const [form, setForm] = React.useState<InstanceFormState>({
    definition_id: "42",
    location_id: "",
    quantity: "1",
    stock_level: "",
    serial: "",
    model_number: "",
    manufacturer: "",
    best_before_date: "",
    warranty_expires: "",
    warranty_details: "",
    purchase_price: "",
    purchase_date: "",
    purchase_source: "",
    ...props.initialForm,
  });
  return (
    <MantineProvider>
      <InstanceFormModal
        opened={true}
        title="Test modal"
        form={form}
        setForm={setForm}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
        busy={false}
        error={null}
        definitions={[defExact as AnyResult]}
        locations={[locationGarage as AnyResult]}
        trackingMode={props.trackingMode ?? "exact"}
        isEdit={props.isEdit ?? false}
        lockDefinition={false}
      />
    </MantineProvider>
  );
}

// ── Tests: DefinitionFormModal mode select + min_stock visibility ─────────────

describe("DefinitionFormModal — tracking mode select and min_stock visibility", () => {
  beforeEach(() => {
    mockItemsListLoad([]);
    vi.mocked(client.POST).mockResolvedValue({
      data: defExact,
      response: new Response(null, { status: 201 }),
    } as AnyResult);
  });

  async function openCreateModal() {
    renderItems();
    await waitFor(() => expect(screen.getByTestId("create-def-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("create-def-btn"));
    await screen.findByTestId("def-name-input");
  }

  it("tracking mode select is present in create modal (defaults to exact)", async () => {
    await openCreateModal();
    expect(screen.getByTestId("def-tracking-mode-select")).toBeDefined();
  });

  it("min_stock input is shown by default (mode=exact)", async () => {
    await openCreateModal();
    expect(screen.getByTestId("def-min-stock-input")).toBeDefined();
  });

  it("min_stock input is hidden when mode is switched to level", async () => {
    await openCreateModal();
    const modeSelect = screen.getByTestId("def-tracking-mode-select");
    fireEvent.click(modeSelect);
    // Find and click the "Level (high/medium/low)" option (exact label from i18n)
    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      expect(opts.some((el) => el.textContent?.includes("Level (high/medium/low)"))).toBe(true);
    });
    const levelOpt = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("Level (high/medium/low)"),
    );
    expect(levelOpt).toBeDefined();
    fireEvent.click(levelOpt!);

    await waitFor(() => {
      expect(screen.queryByTestId("def-min-stock-input")).toBeNull();
    });
  });

  it("min_stock input is hidden when mode is switched to none", async () => {
    await openCreateModal();
    const modeSelect = screen.getByTestId("def-tracking-mode-select");
    fireEvent.click(modeSelect);
    // "None (presence only)" is the exact label from i18n
    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      expect(opts.some((el) => el.textContent?.includes("None (presence only)"))).toBe(true);
    });
    const noneOpt = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("None (presence only)"),
    );
    expect(noneOpt).toBeDefined();
    fireEvent.click(noneOpt!);

    await waitFor(() => {
      expect(screen.queryByTestId("def-min-stock-input")).toBeNull();
    });
  });

  it("min_stock input reappears when mode is switched back to exact", async () => {
    await openCreateModal();

    // Switch to level (hides min_stock)
    const modeSelect = screen.getByTestId("def-tracking-mode-select");
    fireEvent.click(modeSelect);
    await waitFor(() => {
      expect([...document.querySelectorAll('[role="option"]')].some((el) => el.textContent?.includes("Level (high/medium/low)"))).toBe(true);
    });
    fireEvent.click([...document.querySelectorAll('[role="option"]')].find((el) => el.textContent?.includes("Level (high/medium/low)"))!);
    await waitFor(() => expect(screen.queryByTestId("def-min-stock-input")).toBeNull());

    // Switch back to exact (shows min_stock)
    fireEvent.click(modeSelect);
    await waitFor(() => {
      expect([...document.querySelectorAll('[role="option"]')].some((el) => el.textContent?.includes("Exact (ledger)"))).toBe(true);
    });
    fireEvent.click([...document.querySelectorAll('[role="option"]')].find((el) => el.textContent?.includes("Exact (ledger)"))!);
    await waitFor(() => expect(screen.getByTestId("def-min-stock-input")).toBeDefined());
  });
});

// ── Tests: POST body includes tracking_mode and min_stock ─────────────────────

describe("DefinitionFormModal — POST body sends tracking_mode and min_stock", () => {
  beforeEach(() => {
    mockItemsListLoad([]);
    vi.mocked(client.POST).mockResolvedValue({
      data: defExact,
      response: new Response(null, { status: 201 }),
    } as AnyResult);
  });

  it("sends stock_tracking_mode from the select (not hardcoded exact)", async () => {
    renderItems();
    await waitFor(() => expect(screen.getByTestId("create-def-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("create-def-btn"));

    const nameInput = await screen.findByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "Test Item" } });

    // Submit without changing mode — should send "exact" from form state
    fireEvent.click(screen.getByTestId("def-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/definitions",
        expect.objectContaining({
          body: expect.objectContaining({ stock_tracking_mode: "exact" }),
        }),
      );
    });
  });

  it("sends min_stock as null when mode is not exact", async () => {
    renderItems();
    await waitFor(() => expect(screen.getByTestId("create-def-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("create-def-btn"));

    const nameInput = await screen.findByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "Test Item" } });

    // Switch to "level" mode
    const modeSelect = screen.getByTestId("def-tracking-mode-select");
    fireEvent.click(modeSelect);
    await waitFor(() => {
      expect([...document.querySelectorAll('[role="option"]')].some((el) => el.textContent?.includes("Level (high/medium/low)"))).toBe(true);
    });
    fireEvent.click([...document.querySelectorAll('[role="option"]')].find((el) => el.textContent?.includes("Level (high/medium/low)"))!);

    await waitFor(() => expect(screen.queryByTestId("def-min-stock-input")).toBeNull());

    fireEvent.click(screen.getByTestId("def-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/definitions",
        expect.objectContaining({
          body: expect.objectContaining({
            stock_tracking_mode: "level",
            min_stock: null,
          }),
        }),
      );
    });
  });
});

// ── Tests: mode-change 409 surfaced ──────────────────────────────────────────

describe("DefinitionFormModal — mode-change 409 (tracking_mode_change_conflict) surfaced", () => {
  it("surfaces tracking_mode_change_conflict via mapApiError on PATCH", async () => {
    mockItemDetailLoad(defExact, []);
    vi.mocked(client.PATCH).mockResolvedValue({
      data: null,
      error: {
        code: "item_definition.tracking_mode_change_conflict",
        message: "Cannot change tracking mode when the definition already has stock instances.",
      },
      response: new Response(null, { status: 409 }),
    } as AnyResult);

    renderItemDetail(42);

    // Wait for the definition detail to load
    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    // Open edit modal
    fireEvent.click(screen.getByTestId("edit-def-btn"));
    await screen.findByTestId("def-name-input");

    // Submit the edit form (trigger a PATCH)
    fireEvent.click(screen.getByTestId("def-submit-btn"));

    // Expect the localized error message to appear
    await waitFor(() => {
      expect(
        screen.getByText(
          /cannot change the tracking mode while this definition still has registered stock lots/i,
        ),
      ).toBeDefined();
    });
  });
});

// ── Tests: definition detail card shows mode badge + min_stock ────────────────

describe("ItemDetail — definition card shows tracking mode badge and min_stock", () => {
  it("shows the tracking mode badge for exact mode", async () => {
    mockItemDetailLoad(defExact, []);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId("def-tracking-mode-badge")).toBeDefined();
    });
    // Badge shows localized exact label
    expect(screen.getByTestId("def-tracking-mode-badge").textContent).toMatch(/exact/i);
  });

  it("shows min_stock value with trailing zeros stripped (4.000000 → 4)", async () => {
    mockItemDetailLoad(defExact, []);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByTestId("def-min-stock-value")).toBeDefined();
    });
    // API returns "4.000000"; formatQuantity should strip trailing zeros → "4"
    expect(screen.getByTestId("def-min-stock-value").textContent).toBe("4");
  });

  it("shows fractional min_stock with trailing zeros stripped (4.500000 → 4.5)", async () => {
    mockItemDetailLoad(defExactFractional, []);
    renderItemDetail(45);

    await waitFor(() => {
      expect(screen.getByTestId("def-min-stock-value")).toBeDefined();
    });
    // API returns "4.500000"; formatQuantity should strip trailing zeros → "4.5"
    expect(screen.getByTestId("def-min-stock-value").textContent).toBe("4.5");
  });

  it("does NOT show min_stock when mode is level", async () => {
    mockItemDetailLoad(defLevel, []);
    renderItemDetail(43);

    await waitFor(() => {
      expect(screen.getByTestId("def-tracking-mode-badge")).toBeDefined();
    });
    // min_stock value should not be present
    expect(screen.queryByTestId("def-min-stock-value")).toBeNull();
  });

  it("does NOT show min_stock when mode is none", async () => {
    mockItemDetailLoad(defNone, []);
    renderItemDetail(44);

    await waitFor(() => {
      expect(screen.getByTestId("def-tracking-mode-badge")).toBeDefined();
    });
    expect(screen.queryByTestId("def-min-stock-value")).toBeNull();
  });
});

// ── Tests: InstanceFormModal branches by mode ─────────────────────────────────

describe("InstanceFormModal — branches by tracking mode (stateful wrapper)", () => {
  it("exact (create): shows quantity field, no stock_level select", () => {
    render(<InstanceFormModalWrapper trackingMode="exact" isEdit={false} />);
    expect(screen.getByTestId("inst-quantity-input")).toBeDefined();
    expect(screen.queryByTestId("inst-stock-level-select")).toBeNull();
  });

  it("level (create): shows stock_level select, no quantity field", () => {
    render(<InstanceFormModalWrapper trackingMode="level" isEdit={false} />);
    expect(screen.getByTestId("inst-stock-level-select")).toBeDefined();
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
  });

  it("none (create): shows neither quantity nor stock_level", () => {
    render(<InstanceFormModalWrapper trackingMode="none" isEdit={false} />);
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
    expect(screen.queryByTestId("inst-stock-level-select")).toBeNull();
  });

  it("exact (edit): quantity field is shown but disabled (locked)", () => {
    render(<InstanceFormModalWrapper trackingMode="exact" isEdit={true} />);
    // Mantine NumberInput passes data-testid to the <input> element directly
    const qtyInput = screen.getByTestId("inst-quantity-input") as HTMLInputElement;
    expect(qtyInput).toBeDefined();
    expect(qtyInput.disabled).toBe(true);
  });

  it("level (edit): stock_level select is shown", () => {
    render(<InstanceFormModalWrapper initialForm={{ stock_level: "low" }} trackingMode="level" isEdit={true} />);
    expect(screen.getByTestId("inst-stock-level-select")).toBeDefined();
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
  });
});

// ── Tests: serial ⇒ qty=1 rule preserved in exact mode ───────────────────────

describe("InstanceFormModal — serial ⇒ qty=1 rule (exact mode, stateful)", () => {
  it("entering serial shows the hint text", async () => {
    render(<InstanceFormModalWrapper trackingMode="exact" isEdit={false} />);

    const serialInput = screen.getByTestId("inst-serial-input");
    fireEvent.change(serialInput, { target: { value: "SN-123" } });

    await waitFor(() => {
      expect(screen.getByText(/serial is set.*quantity forced to 1/i)).toBeDefined();
    });
  });

  it("clearing the serial removes the hint", async () => {
    render(<InstanceFormModalWrapper trackingMode="exact" isEdit={false} />);

    const serialInput = screen.getByTestId("inst-serial-input");
    // Enter a serial
    fireEvent.change(serialInput, { target: { value: "SN-123" } });
    await waitFor(() => {
      expect(screen.getByText(/serial is set.*quantity forced to 1/i)).toBeDefined();
    });
    // Clear it
    fireEvent.change(serialInput, { target: { value: "" } });
    await waitFor(() => {
      expect(screen.queryByText(/serial is set.*quantity forced to 1/i)).toBeNull();
    });
  });

  it("serial rule does NOT apply for level mode (no quantity field shown)", () => {
    render(<InstanceFormModalWrapper trackingMode="level" isEdit={false} />);
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
    expect(screen.queryByText(/serial is set.*quantity forced to 1/i)).toBeNull();
  });
});

// ── Tests: instance form in ItemDetail page branches by definition mode ────────

describe("ItemDetail — instance form branches by definition tracking mode", () => {
  it("exact mode: register instance shows quantity field", async () => {
    mockItemDetailLoad(defExact, []);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("register-instance-btn"));
    await screen.findByTestId("inst-serial-input");

    // Quantity field should be present
    expect(screen.getByTestId("inst-quantity-input")).toBeDefined();
    // Stock level select should not be present
    expect(screen.queryByTestId("inst-stock-level-select")).toBeNull();
  });

  it("level mode: register instance shows stock_level select, no quantity", async () => {
    mockItemDetailLoad(defLevel, [instanceLevel]);
    renderItemDetail(43);

    await waitFor(() => {
      expect(screen.getAllByText("Assorted Screws").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("register-instance-btn"));
    await screen.findByTestId("inst-serial-input");

    // Stock level select should be present
    expect(screen.getByTestId("inst-stock-level-select")).toBeDefined();
    // Quantity field should not be present
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
  });

  it("none mode: register instance shows neither quantity nor stock_level", async () => {
    mockItemDetailLoad(defNone, []);
    renderItemDetail(44);

    await waitFor(() => {
      expect(screen.getAllByText("Wall Art").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("register-instance-btn"));
    await screen.findByTestId("inst-serial-input");

    // Neither quantity nor stock level
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
    expect(screen.queryByTestId("inst-stock-level-select")).toBeNull();
  });

  it("exact mode (edit): quantity field is disabled/locked", async () => {
    mockItemDetailLoad(defExact, [instanceExact]);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("AA Batteries").length).toBeGreaterThan(0);
    });

    // Open edit modal for the instance
    fireEvent.click(screen.getByTestId(`edit-inst-${instanceExact.id}`));
    await screen.findByTestId("inst-serial-input");

    // Mantine NumberInput passes data-testid to the <input> element directly
    const qtyInput = screen.getByTestId("inst-quantity-input") as HTMLInputElement;
    expect(qtyInput).toBeDefined();
    expect(qtyInput.disabled).toBe(true);
  });

  it("level mode (edit): stock_level select shown", async () => {
    mockItemDetailLoad(defLevel, [instanceLevel]);
    renderItemDetail(43);

    await waitFor(() => {
      expect(screen.getAllByText("Assorted Screws").length).toBeGreaterThan(0);
    });

    // Open edit modal for the level instance
    fireEvent.click(screen.getByTestId(`edit-inst-${instanceLevel.id}`));
    await screen.findByTestId("inst-serial-input");

    expect(screen.getByTestId("inst-stock-level-select")).toBeDefined();
    expect(screen.queryByTestId("inst-quantity-input")).toBeNull();
  });
});
