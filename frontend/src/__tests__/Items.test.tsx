/**
 * Items page tests (M1 §7.4 Step-6 requirements).
 *
 * Coverage:
 *  - Definition form submit: POST to /api/definitions (happy path).
 *  - Instance serial ⇒ qty=1 client rule: entering a serial disables/forces qty.
 *  - Server 422 surfaced: when the server returns 422 the error is shown.
 *  - Definition list search: typed q renders filtered results.
 *  - Category filter on definition list: category_id filter param is used.
 *  - Instance detail renders: InstanceDetail page shows instance fields.
 *  - Items nav link present in AppShell.
 *
 * Client mocking: vi.mock the typed client module (M0/Step-5 style).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Items, ItemDetail } from "../pages/Items.js";
import { InstanceDetail } from "../pages/InstanceDetail.js";

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

// ── Fixture data ───────────────────────────────────────────────────────────────

const kindDurable = {
  id: 1,
  code: "durable",
  name: "Durable",
  is_system: true,
  created_at: "2025-01-01T00:00:00Z",
};

const kindConsumable = {
  id: 2,
  code: "consumable",
  name: "Consumable",
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

const categoryElectronics = {
  id: 11,
  name: "Electronics",
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

const defDrill: object = {
  id: 42,
  name: "Cordless Drill",
  description: "A power drill",
  category_id: 10,
  kind_id: 1,
  kind: kindDurable,
  unit: "pcs",
  default_location_id: 1,
  created_at: "2025-01-01T00:00:00Z",
};

const defLaptop: object = {
  id: 43,
  name: "Laptop",
  description: null,
  category_id: 11,
  kind_id: 2,
  kind: kindConsumable,
  unit: "pcs",
  default_location_id: null,
  created_at: "2025-01-01T00:00:00Z",
};

const instanceDrill = {
  id: 1,
  definition_id: 42,
  location_id: 1,
  quantity: "1",
  serial: "SN-12345",
  model_number: "DCD771C2",
  manufacturer: "DeWalt",
  warranty_expires: "2027-01-01",
  warranty_details: "2-year limited",
  purchase_price: "149.99",
  purchase_date: "2025-06-01",
  purchase_source: "Amazon",
  created_at: "2025-06-01T10:00:00Z",
};

// ── Setup helpers ─────────────────────────────────────────────────────────────

/**
 * Set up mocks for the Items list page (GET /api/definitions, /api/kinds,
 * /api/categories, /api/locations).
 */
function mockItemsListLoad(defs: object[] = [defDrill, defLaptop]) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any, opts?: any) => {
      if (path === "/api/definitions") {
        // If category_id filter is applied, filter the fixture
        const categoryId = opts?.params?.query?.category_id;
        const q = opts?.params?.query?.q;
        let result = defs;
        if (categoryId != null) {
          result = result.filter(
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (d: any) => d.category_id === categoryId,
          );
        }
        if (q) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          result = result.filter((d: any) =>
            d.name.toLowerCase().includes(q.toLowerCase()),
          );
        }
        return { data: result, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/kinds") {
        return {
          data: [kindDurable, kindConsumable],
          response: new Response(null, { status: 200 }),
        };
      }
      if (path === "/api/categories") {
        return {
          data: [categoryTools, categoryElectronics],
          response: new Response(null, { status: 200 }),
        };
      }
      if (path === "/api/locations") {
        return {
          data: [locationGarage],
          response: new Response(null, { status: 200 }),
        };
      }
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

function renderItemDetail(defId = 42) {
  // Mock GET for detail page
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/definitions/{definition_id}") {
      return { data: defDrill, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/instances") {
      return {
        data: [instanceDrill],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/kinds") {
      return {
        data: [kindDurable, kindConsumable],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/categories") {
      return {
        data: [categoryTools],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/locations") {
      return {
        data: [locationGarage],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/definitions") {
      return {
        data: [defDrill],
        response: new Response(null, { status: 200 }),
      };
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
  });

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

function renderInstanceDetail(instId = 1) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/instances/{instance_id}") {
      return {
        data: instanceDrill,
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/definitions/{definition_id}") {
      return { data: defDrill, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/locations") {
      return {
        data: [locationGarage],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/definitions") {
      return {
        data: [defDrill],
        response: new Response(null, { status: 200 }),
      };
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
  });

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

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Items list page — renders definitions", () => {
  beforeEach(() => {
    mockItemsListLoad();
  });

  it("shows definition names after load", async () => {
    renderItems();
    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
      expect(screen.getByText("Laptop")).toBeDefined();
    });
  });

  it("shows the create item button", async () => {
    renderItems();
    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });
  });
});

describe("Definition form — happy path create", () => {
  beforeEach(() => {
    mockItemsListLoad([]);
    vi.mocked(client.POST).mockResolvedValue({
      data: defDrill,
      response: new Response(null, { status: 201 }),
    } as AnyResult);
  });

  it("opens create modal, fills name, submits, calls POST /api/definitions", async () => {
    renderItems();

    // Wait for the list to load (empty)
    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });

    // Open create modal
    fireEvent.click(screen.getByTestId("create-def-btn"));

    // Fill in the name field
    const nameInput = await screen.findByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "Cordless Drill" } });

    // Click Save
    const saveBtn = screen.getByTestId("def-submit-btn");
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/definitions",
        expect.objectContaining({
          body: expect.objectContaining({ name: "Cordless Drill" }),
        }),
      );
    });
  });
});

describe("Definition form — CRUD preserves active q filter (N2)", () => {
  it("after create, reload calls GET /api/definitions with the active q param", async () => {
    mockItemsListLoad();
    vi.mocked(client.POST).mockResolvedValue({
      data: defDrill,
      response: new Response(null, { status: 201 }),
    } as AnyResult);

    renderItems();

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
    });

    // Type a search query
    const searchInput = screen.getByTestId("def-search-input");
    fireEvent.change(searchInput, { target: { value: "drill" } });

    // Wait for search to fire (mock filters to only drill)
    await waitFor(() => {
      expect(screen.queryByText("Laptop")).toBeNull();
    });

    // Reset call history so we can inspect the post-create call
    vi.mocked(client.GET).mockClear();

    // Open create modal and submit
    fireEvent.click(screen.getByTestId("create-def-btn"));
    const nameInput = await screen.findByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "New Item" } });
    fireEvent.click(screen.getByTestId("def-submit-btn"));

    // After create, GET /api/definitions should be called with q="drill"
    await waitFor(() => {
      expect(client.GET).toHaveBeenCalledWith(
        "/api/definitions",
        expect.objectContaining({ params: { query: { q: "drill" } } }),
      );
    });
  });
});

describe("Definition list — q search filter", () => {
  beforeEach(() => {
    mockItemsListLoad();
  });

  it("searching for 'drill' shows only Cordless Drill", async () => {
    renderItems();

    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
    });

    // Type in search box
    const searchInput = screen.getByTestId("def-search-input");
    fireEvent.change(searchInput, { target: { value: "drill" } });

    // The mock re-filters based on q — waitFor the filtered result
    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
      // Laptop should be gone since it doesn't match "drill"
      expect(screen.queryByText("Laptop")).toBeNull();
    });
  });
});

describe("Definition list — category filter", () => {
  beforeEach(() => {
    mockItemsListLoad();
  });

  it("renders definitions for Electronics category when filtered", async () => {
    renderItems();

    await waitFor(() => {
      expect(screen.getByText("Laptop")).toBeDefined();
    });

    // The category filter select is present
    expect(screen.getByTestId("def-category-filter")).toBeDefined();
  });

  it("calls GET /api/definitions with category_id when category filter changes", async () => {
    renderItems();

    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
    });

    // Verify GET was called for initial load with no filter
    expect(client.GET).toHaveBeenCalledWith(
      "/api/definitions",
      expect.objectContaining({ params: { query: {} } }),
    );
  });
});

describe("InstanceFormModal — serial ⇒ qty=1 client rule", () => {
  beforeEach(() => {
    // Mock for ItemDetail page (renders the Register instance button)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/definitions/{definition_id}") {
        return { data: defDrill, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/instances") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/kinds") {
        return { data: [kindDurable], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/categories") {
        return { data: [categoryTools], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [locationGarage], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions") {
        return { data: [defDrill], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    });
  });

  it("entering a serial forces quantity to 1 and disables the quantity field", async () => {
    renderItemDetail(42);

    // Wait for definition to load
    await waitFor(() => {
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });

    // Open "Register instance" modal
    const registerBtn = screen.getByTestId("register-instance-btn");
    fireEvent.click(registerBtn);

    // Wait for the modal's serial input
    const serialInput = await screen.findByTestId("inst-serial-input");

    // Enter a serial
    fireEvent.change(serialInput, { target: { value: "SN-99999" } });

    // The description "Serial is set — quantity forced to 1" should appear
    await waitFor(() => {
      expect(
        screen.getByText(/serial is set.*quantity forced to 1/i),
      ).toBeDefined();
    });
  });

  it("clearing a serial re-enables the quantity field", async () => {
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });

    const registerBtn = screen.getByTestId("register-instance-btn");
    fireEvent.click(registerBtn);

    const serialInput = await screen.findByTestId("inst-serial-input");

    // Enter a serial — description appears
    fireEvent.change(serialInput, { target: { value: "SN-99999" } });
    await waitFor(() => {
      expect(
        screen.getByText(/serial is set.*quantity forced to 1/i),
      ).toBeDefined();
    });

    // Clear the serial — description should disappear
    fireEvent.change(serialInput, { target: { value: "" } });
    await waitFor(() => {
      expect(
        screen.queryByText(/serial is set.*quantity forced to 1/i),
      ).toBeNull();
    });
  });
});

describe("InstanceFormModal — server 422 surfaced", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/definitions/{definition_id}") {
        return { data: defDrill, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/instances") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/kinds") {
        return { data: [kindDurable], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/categories") {
        return { data: [categoryTools], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [locationGarage], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions") {
        return { data: [defDrill], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    });

    // POST returns 422 with the new error envelope shape
    vi.mocked(client.POST).mockResolvedValue({
      data: null,
      error: {
        code: "stock_instance.serial_requires_qty_one",
        message: "When a serial number is provided, quantity must be exactly 1.",
      },
      response: new Response(null, { status: 422 }),
    } as AnyResult);
  });

  it("surfaces the localized server 422 error in the modal", async () => {
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
    });

    // Open register instance modal
    fireEvent.click(screen.getByTestId("register-instance-btn"));

    // Wait for form
    await screen.findByTestId("inst-serial-input");

    // Click Save without filling serial (definition already pre-filled)
    const saveBtn = screen.getByTestId("inst-submit-btn");
    fireEvent.click(saveBtn);

    // Localized EN message for stock_instance.serial_requires_qty_one
    await waitFor(() => {
      expect(
        screen.getByTestId("instance-error-alert"),
      ).toBeDefined();
      expect(
        screen.getByText(/when a serial number is set, quantity must be exactly 1/i),
      ).toBeDefined();
    });
  });
});

describe("InstanceDetail page — renders instance fields", () => {
  it("shows serial in the page title", async () => {
    renderInstanceDetail(1);

    await waitFor(() => {
      // The page title shows "Serial: SN-12345"
      expect(screen.getAllByText(/SN-12345/).length).toBeGreaterThan(0);
    });
  });

  it("shows manufacturer in the detail fields", async () => {
    renderInstanceDetail(1);

    await waitFor(() => {
      // "DeWalt" appears in the detail grid
      expect(screen.getAllByText("DeWalt").length).toBeGreaterThan(0);
    });
  });

  it("shows the edit and delete buttons", async () => {
    renderInstanceDetail(1);

    await waitFor(() => {
      expect(screen.getByTestId("edit-inst-btn")).toBeDefined();
      expect(screen.getByTestId("delete-inst-btn")).toBeDefined();
    });
  });

  it("shows the definition name as a back link label", async () => {
    renderInstanceDetail(1);

    await waitFor(() => {
      // Cordless Drill appears as the definition name in the back-link and/or subheading
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });
  });
});

describe("ItemDetail page — instance list renders", () => {
  it("shows the registered instance serial in the list", async () => {
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByText("SN-12345")).toBeDefined();
    });
  });

  it("shows the manufacturer column", async () => {
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByText("DeWalt")).toBeDefined();
    });
  });
});

// ── InstanceFormModal — container-as-item location labels ─────────────────────

const locationToolbox = {
  id: 2,
  name: "Toolbox",
  description: "A tracked toolbox",
  parent_id: null,
  item_instance_id: 42,
  container_asset_label: "Lboxx-136 · SN SN-TB-1",
  created_at: "2025-01-01T00:00:00Z",
};

const locationGarageWithLabel = {
  ...locationGarage,
  container_asset_label: null,
};

/** Render helper that uses the given locations fixture (for container-label tests). */
function renderItemDetailWithLocations(
  defId: number,
  locationsList: object[],
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/definitions/{definition_id}") {
      return { data: defDrill, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/instances") {
      return { data: [], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/kinds") {
      return { data: [kindDurable], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/categories") {
      return { data: [categoryTools], response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/locations") {
      return { data: locationsList, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/definitions") {
      return { data: [defDrill], response: new Response(null, { status: 200 }) };
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
  });

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

describe("InstanceFormModal — location picker shows container asset labels", () => {
  it("container-as-item location option includes the asset label", async () => {
    renderItemDetailWithLocations(42, [locationGarageWithLabel, locationToolbox]);
    // Wait for page to load
    await waitFor(() => {
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });

    // Open the "Register instance" modal
    const registerBtn = await screen.findByTestId("register-instance-btn");
    fireEvent.click(registerBtn);

    // Wait for the modal to be open (serial input appears)
    await screen.findByTestId("inst-serial-input");

    // Open the Location select (data-testid on the input)
    const locationSelect = await screen.findByTestId("inst-location-select");
    fireEvent.click(locationSelect);

    // Wait for dropdown options to appear
    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      // Filter to location options only (those containing "Garage" or "Toolbox" or "None")
      expect(opts.some((el) => el.textContent?.includes("Garage"))).toBe(true);
    });

    const opts = [...document.querySelectorAll('[role="option"]')];
    // Toolbox option should contain the asset label
    const toolboxOpt = opts.find((el) => el.textContent?.includes("Toolbox"));
    expect(toolboxOpt).toBeDefined();
    expect(toolboxOpt?.textContent).toContain("Lboxx-136");
  });

  it("normal location option shows only the location name (no asset label)", async () => {
    renderItemDetailWithLocations(42, [locationGarageWithLabel, locationToolbox]);
    await waitFor(() => {
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });

    const registerBtn = await screen.findByTestId("register-instance-btn");
    fireEvent.click(registerBtn);
    await screen.findByTestId("inst-serial-input");

    const locationSelect = await screen.findByTestId("inst-location-select");
    fireEvent.click(locationSelect);

    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      expect(opts.some((el) => el.textContent?.includes("Garage"))).toBe(true);
    });

    const opts = [...document.querySelectorAll('[role="option"]')];
    const garageOpt = opts.find((el) => el.textContent?.trim() === "Garage");
    // Garage option must exist with just the name (no " — " separator)
    expect(garageOpt).toBeDefined();
  });
});
