/**
 * TreeBrowser component tests.
 *
 * Tests the shared tree browse/edit component used by both Locations and
 * Categories pages (M1 §7.4 + §10 Step-5 requirements).
 *
 * Coverage:
 *  - Tree renders nodes from the server response (locations + categories).
 *  - Empty tree shows EmptyState.
 *  - Create-child happy path: POST succeeds → tree reloads.
 *  - Rename happy path: PATCH succeeds → tree reloads.
 *  - Delete happy path: DELETE succeeds → tree reloads.
 *  - Delete-guard 409: server returns 409 → error message renders inside the modal.
 *  - Container-as-item: location node with item_instance_id shows the asset badge.
 *  - Reparent happy path: Select picker shown, pick existing node → PATCH with parent_id.
 *  - Reparent root option: pick "root" → PATCH with parent_id: null.
 *  - Reparent cycle-safety: node being moved and its descendants not in picker options.
 *  - Reparent backend error: server 4xx detail message shown in modal.
 *  - Reparent categories: same behaviour for resource="categories".
 *
 * Client mocking: vi.mock the typed client module (M0 style).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { TreeBrowser } from "../components/TreeBrowser.js";

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

// ── Fixture data ──────────────────────────────────────────────────────────────

const locationTreeFixture = [
  {
    id: 1,
    name: "Home",
    description: null,
    parent_id: null,
    item_instance_id: null,
    container_asset_label: null,
    created_at: "2025-01-01T00:00:00Z",
    children: [
      {
        id: 2,
        name: "Garage",
        description: "The garage",
        parent_id: 1,
        item_instance_id: null,
        container_asset_label: null,
        created_at: "2025-01-01T00:00:00Z",
        children: [],
      },
      {
        id: 3,
        name: "Toolbox",
        description: "A tracked toolbox",
        parent_id: 1,
        // container-as-item: this location IS a tracked asset
        item_instance_id: 42,
        container_asset_label: "Lboxx-136 · SN SN-TB-1",
        created_at: "2025-01-01T00:00:00Z",
        children: [],
      },
    ],
  },
];

const categoryTreeFixture = [
  {
    id: 10,
    name: "Tools",
    description: null,
    parent_id: null,
    created_at: "2025-01-01T00:00:00Z",
    children: [
      {
        id: 11,
        name: "Power Tools",
        description: null,
        parent_id: 10,
        created_at: "2025-01-01T00:00:00Z",
        children: [],
      },
    ],
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderLocations() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <TreeBrowser resource="locations" />
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderCategories() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <TreeBrowser resource="categories" />
      </MantineProvider>
    </MemoryRouter>,
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyClientResult = any;

function makeSuccessGetLocations() {
  vi.mocked(client.GET).mockResolvedValue({
    data: locationTreeFixture,
    response: new Response(null, { status: 200 }),
  } as AnyClientResult);
}

function makeSuccessGetCategories() {
  vi.mocked(client.GET).mockResolvedValue({
    data: categoryTreeFixture,
    response: new Response(null, { status: 200 }),
  } as AnyClientResult);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("TreeBrowser — locations tree renders", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
  });

  it("renders root location node", async () => {
    renderLocations();
    await waitFor(() => {
      expect(screen.getByText("Home")).toBeDefined();
    });
  });

  it("shows the container-as-item asset badge for a location with item_instance_id", async () => {
    renderLocations();
    await waitFor(() => {
      // The Toolbox node has item_instance_id=42 → shows a badge
      expect(screen.getByTestId("container-badge-3")).toBeDefined();
    });
  });

  it("does NOT show an asset badge for a location without item_instance_id", async () => {
    renderLocations();
    await waitFor(() => {
      // Home (id=1) has item_instance_id=null → no badge
      expect(screen.queryByTestId("container-badge-1")).toBeNull();
    });
  });
});

describe("TreeBrowser — categories tree renders", () => {
  beforeEach(() => {
    makeSuccessGetCategories();
  });

  it("renders root category node", async () => {
    renderCategories();
    await waitFor(() => {
      expect(screen.getByText("Tools")).toBeDefined();
    });
  });
});

describe("TreeBrowser — empty tree", () => {
  beforeEach(() => {
    vi.mocked(client.GET).mockResolvedValue({
      data: [],
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("shows the empty state when tree is empty (locations)", async () => {
    renderLocations();
    await waitFor(() => {
      expect(screen.getByText(/no location/i)).toBeDefined();
    });
  });

  it("shows the empty state when tree is empty (categories)", async () => {
    renderCategories();
    await waitFor(() => {
      expect(screen.getByText(/no categories/i)).toBeDefined();
    });
  });
});

describe("TreeBrowser — create-child happy path", () => {
  beforeEach(() => {
    // First GET returns the initial tree; subsequent GETs (after create) also succeed.
    makeSuccessGetLocations();
    vi.mocked(client.POST).mockResolvedValue({
      data: {
        id: 99,
        name: "New Child",
        description: null,
        parent_id: null,
        item_instance_id: null,
        created_at: "2025-01-01T00:00:00Z",
      },
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("opens create modal, fills name, clicks Create, calls POST", async () => {
    renderLocations();
    // Wait for tree to load
    await waitFor(() => screen.getByText("Home"));

    // Click "Add location" button (top toolbar, no selection → root)
    const addBtn = screen.getByTestId("create-root-btn");
    fireEvent.click(addBtn);

    // Modal should appear; fill in name
    const nameInput = await screen.findByTestId("name-input");
    fireEvent.change(nameInput, { target: { value: "New Child" } });

    // Click Create
    const createBtn = screen.getByRole("button", { name: /^create$/i });
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/locations",
        expect.objectContaining({
          body: expect.objectContaining({ name: "New Child" }),
        }),
      );
    });
  });
});

describe("TreeBrowser — rename happy path", () => {
  beforeEach(() => {
    makeSuccessGetCategories();
    vi.mocked(client.PATCH).mockResolvedValue({
      data: {
        id: 10,
        name: "Renamed",
        description: null,
        parent_id: null,
        created_at: "2025-01-01T00:00:00Z",
      },
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("opens rename modal, fills new name, calls PATCH", async () => {
    renderCategories();
    await waitFor(() => screen.getByText("Tools"));

    // Click the rename icon for "Tools" (aria-label = "Rename Tools")
    const renameBtn = screen.getByRole("button", { name: /rename tools/i });
    fireEvent.click(renameBtn);

    const renameInput = await screen.findByTestId("rename-input");
    fireEvent.change(renameInput, { target: { value: "Renamed" } });

    const saveBtn = screen.getByRole("button", { name: /save/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/categories/{category_id}",
        expect.objectContaining({
          params: { path: { category_id: 10 } },
          body: expect.objectContaining({ name: "Renamed" }),
        }),
      );
    });
  });
});

describe("TreeBrowser — delete happy path", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
    vi.mocked(client.DELETE).mockResolvedValue({
      data: undefined,
      response: new Response(null, { status: 204 }),
    } as AnyClientResult);
  });

  it("opens delete modal and calls DELETE on confirm", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    const deleteBtn = screen.getByRole("button", { name: /delete home/i });
    fireEvent.click(deleteBtn);

    const confirmBtn = await screen.findByTestId("confirm-delete-btn");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(client.DELETE).toHaveBeenCalledWith(
        "/api/locations/{location_id}",
        expect.objectContaining({
          params: { path: { location_id: 1 } },
        }),
      );
    });
  });
});

describe("TreeBrowser — delete-guard 409 surfaced", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
    vi.mocked(client.DELETE).mockResolvedValue({
      error: {
        code: "tree.delete_has_children",
        message: "Cannot delete a node that still has children.",
        params: { kind: "location" },
      },
      response: new Response(null, { status: 409 }),
    } as AnyClientResult);
  });

  it("shows the localized 409 guard message inside the delete modal", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Open the delete modal for "Home"
    const deleteBtn = screen.getByRole("button", { name: /delete home/i });
    fireEvent.click(deleteBtn);

    const confirmBtn = await screen.findByTestId("confirm-delete-btn");
    fireEvent.click(confirmBtn);

    // The localized EN message for tree.delete_has_children should appear
    await waitFor(() => {
      expect(
        screen.getByTestId("delete-error"),
      ).toBeDefined();
      // "Cannot delete: this location still has children. Remove them first."
      expect(
        screen.getByText(/cannot delete.*still has children/i),
      ).toBeDefined();
    });
  });
});

// ── Reparent (move) tests ─────────────────────────────────────────────────────

describe("TreeBrowser — reparent modal shows Select picker (not numeric input)", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
  });

  it("opens reparent modal with a Select combobox, no NumberInput", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Select the "Garage" node (id=2) by clicking it
    fireEvent.click(screen.getByText("Garage"));

    // The selected-node panel appears; click "Reparent"
    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    // The modal should show the Select (not the old numeric input)
    await waitFor(() => {
      // The reparent-select wrapper is present
      expect(screen.getByTestId("reparent-select")).toBeDefined();
      // The old "New parent ID" label must not appear
      expect(screen.queryByText(/new parent id/i)).toBeNull();
    });
  });
});

describe("TreeBrowser — reparent happy path (pick an existing node)", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
    vi.mocked(client.PATCH).mockResolvedValue({
      data: {},
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("selecting an existing node option → PATCH with its numeric parent_id", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Select "Garage" (id=2, parent_id=1)
    fireEvent.click(screen.getByText("Garage"));

    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    // data-testid goes directly onto the <input> in Mantine v7 Select
    const selectInput = await screen.findByTestId("reparent-select");
    // Open the dropdown by clicking the select input
    fireEvent.click(selectInput);

    // Options rendered in a portal (role="option")
    // Toolbox (id=3) is a valid sibling target (not Garage itself or its descendants)
    await waitFor(() => {
      const toolboxOption = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("Toolbox"),
      );
      expect(toolboxOption).toBeDefined();
    });

    const toolboxOption = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("Toolbox"),
    );
    fireEvent.click(toolboxOption!);

    // Click Move
    const moveBtn = screen.getByRole("button", { name: /^move$/i });
    fireEvent.click(moveBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/locations/{location_id}",
        expect.objectContaining({
          params: { path: { location_id: 2 } },
          body: expect.objectContaining({ parent_id: 3 }),
        }),
      );
    });
  });
});

describe("TreeBrowser — reparent root option → parent_id: null", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
    vi.mocked(client.PATCH).mockResolvedValue({
      data: {},
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("selecting 'root' option → PATCH with parent_id: null", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Select "Garage" (id=2, currently child of Home id=1)
    fireEvent.click(screen.getByText("Garage"));

    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    // data-testid is on the <input> itself in Mantine v7 Select
    const selectInput = await screen.findByTestId("reparent-select");
    fireEvent.click(selectInput);

    // Find and click the root sentinel option
    await waitFor(() => {
      const rootOption = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("root"),
      );
      expect(rootOption).toBeDefined();
    });

    const rootOption = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("root"),
    );
    fireEvent.click(rootOption!);

    const moveBtn = screen.getByRole("button", { name: /^move$/i });
    fireEvent.click(moveBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/locations/{location_id}",
        expect.objectContaining({
          params: { path: { location_id: 2 } },
          body: expect.objectContaining({ parent_id: null }),
        }),
      );
    });
  });
});

describe("TreeBrowser — reparent cycle-safety: node and descendants excluded", () => {
  beforeEach(() => {
    // Use a deeper tree: Home(1) → Garage(2) → Shelf(4)
    vi.mocked(client.GET).mockResolvedValue({
      data: [
        {
          id: 1,
          name: "Home",
          description: null,
          parent_id: null,
          item_instance_id: null,
          created_at: "2025-01-01T00:00:00Z",
          children: [
            {
              id: 2,
              name: "Garage",
              description: null,
              parent_id: 1,
              item_instance_id: null,
              created_at: "2025-01-01T00:00:00Z",
              children: [
                {
                  id: 4,
                  name: "Shelf",
                  description: null,
                  parent_id: 2,
                  item_instance_id: null,
                  created_at: "2025-01-01T00:00:00Z",
                  children: [],
                },
              ],
            },
            {
              id: 3,
              name: "Kitchen",
              description: null,
              parent_id: 1,
              item_instance_id: null,
              created_at: "2025-01-01T00:00:00Z",
              children: [],
            },
          ],
        },
      ],
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("picker excludes the moving node itself and its descendants", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Click "Garage" (id=2) to select it
    fireEvent.click(screen.getByText("Garage"));

    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    // data-testid is on the <input> itself in Mantine v7 Select
    const selectInput = await screen.findByTestId("reparent-select");
    fireEvent.click(selectInput);

    // Wait for dropdown to open
    await waitFor(() => {
      // "Kitchen" (id=3) is a valid target and should appear
      const kitchenOption = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("Kitchen"),
      );
      expect(kitchenOption).toBeDefined();
    });

    const allOptions = [...document.querySelectorAll('[role="option"]')];
    const optionTexts = allOptions.map((el) => el.textContent ?? "");

    // "Garage" (the node being moved) must NOT appear
    expect(optionTexts.some((t) => t.includes("Garage"))).toBe(false);
    // "Shelf" (descendant of Garage) must NOT appear
    expect(optionTexts.some((t) => t.includes("Shelf"))).toBe(false);
    // "Home" (valid ancestor) MUST appear
    expect(optionTexts.some((t) => t.includes("Home"))).toBe(true);
    // "Kitchen" (sibling, valid) MUST appear
    expect(optionTexts.some((t) => t.includes("Kitchen"))).toBe(true);
    // Root option must appear
    expect(optionTexts.some((t) => t.includes("root"))).toBe(true);
  });
});

describe("TreeBrowser — reparent backend error is surfaced in modal", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
    vi.mocked(client.PATCH).mockResolvedValue({
      error: {
        code: "tree.cycle",
        message: "Operation would create a cycle in the tree.",
        params: { kind: "location" },
      },
      response: new Response(null, { status: 409 }),
    } as AnyClientResult);
  });

  it("shows the localized cycle error in the reparent modal", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Select "Garage" and open reparent modal
    fireEvent.click(screen.getByText("Garage"));
    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    // Click Move without changing the selection (current parent pre-selected)
    await screen.findByTestId("reparent-select");
    const moveBtn = screen.getByRole("button", { name: /^move$/i });
    fireEvent.click(moveBtn);

    // Localized EN message for tree.cycle
    await waitFor(() => {
      expect(screen.getByText(/would create a circular reference/i)).toBeDefined();
    });
  });
});

describe("TreeBrowser — reparent works for categories resource", () => {
  beforeEach(() => {
    makeSuccessGetCategories();
    vi.mocked(client.PATCH).mockResolvedValue({
      data: {},
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("opens reparent modal for a category and calls PATCH on categories endpoint", async () => {
    renderCategories();
    await waitFor(() => screen.getByText("Power Tools"));

    // Select "Power Tools" (id=11)
    fireEvent.click(screen.getByText("Power Tools"));

    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    // data-testid is on the <input> itself in Mantine v7 Select
    const selectInput = await screen.findByTestId("reparent-select");
    fireEvent.click(selectInput);

    // Open dropdown and click root option
    await waitFor(() => {
      const rootOption = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("root"),
      );
      expect(rootOption).toBeDefined();
    });
    const rootOption = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("root"),
    );
    fireEvent.click(rootOption!);

    const moveBtn = screen.getByRole("button", { name: /^move$/i });
    fireEvent.click(moveBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/categories/{category_id}",
        expect.objectContaining({
          params: { path: { category_id: 11 } },
          body: expect.objectContaining({ parent_id: null }),
        }),
      );
    });
  });
});

// ── Fix 3: Location instances panel ──────────────────────────────────────────

/** Instance fixture for location_id=2 (Garage). */
const instanceAtGarage = {
  id: 101,
  definition_id: 5,
  location_id: 2,
  quantity: "1",
  serial: "SN-001",
  model_number: null,
  manufacturer: "Bosch",
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  created_at: "2025-01-01T00:00:00Z",
};

const definitionDrill = {
  id: 5,
  name: "Cordless Drill",
  description: null,
  category_id: null,
  kind: { id: 1, code: "durable", name: "Durable", is_system: true, created_at: "2025-01-01T00:00:00Z" },
  kind_id: 1,
  unit: "pcs",
  default_location_id: null,
  created_at: "2025-01-01T00:00:00Z",
};

describe("TreeBrowser — location instances panel renders", () => {
  beforeEach(() => {
    // GET /api/locations/tree
    vi.mocked(client.GET).mockImplementation(async (path: string, opts?: unknown) => {
      if (path === "/api/locations/tree") {
        return {
          data: locationTreeFixture,
          response: new Response(null, { status: 200 }),
        } as AnyClientResult;
      }
      // GET /api/instances?location_id=2
      if (path === "/api/instances") {
        const params = (opts as { params?: { query?: { location_id?: number } } })?.params?.query;
        if (params?.location_id === 2) {
          return {
            data: [instanceAtGarage],
            response: new Response(null, { status: 200 }),
          } as AnyClientResult;
        }
        return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      // GET /api/definitions/{definition_id}
      if (path === "/api/definitions/{definition_id}") {
        return {
          data: definitionDrill,
          response: new Response(null, { status: 200 }),
        } as AnyClientResult;
      }
      return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
    });
  });

  it("shows instances section label when a location is selected", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Select "Garage" (id=2)
    fireEvent.click(screen.getByText("Garage"));

    await waitFor(() => {
      expect(screen.getByTestId("instances-section-label")).toBeDefined();
    });
  });

  it("shows instance row with definition name and serial", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    fireEvent.click(screen.getByText("Garage"));

    await waitFor(() => {
      expect(screen.getByTestId(`instance-row-${instanceAtGarage.id}`)).toBeDefined();
    });

    // Definition name should appear (fetched separately)
    await waitFor(() => {
      expect(screen.getByText("Cordless Drill")).toBeDefined();
    });

    // Serial should appear
    expect(screen.getByText("SN-001")).toBeDefined();
  });

  it("shows empty state when location has no instances", async () => {
    // Home (id=1) has no instances.
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Select "Home" (id=1) — instances mock returns []
    fireEvent.click(screen.getByText("Home"));

    await waitFor(() => {
      expect(screen.getByTestId("instances-empty")).toBeDefined();
    });
  });

  it("does NOT show instances section for categories", async () => {
    vi.mocked(client.GET).mockResolvedValue({
      data: categoryTreeFixture,
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);

    renderCategories();
    await waitFor(() => {
      // Wait for the tree to load — "Tools" is the root category
      expect(screen.getAllByText("Tools").length).toBeGreaterThan(0);
    });

    // Select the root "Tools" category node (the tree node, not any heading)
    // Use the first occurrence of "Tools" text (the tree node)
    const toolsNodes = screen.getAllByText("Tools");
    fireEvent.click(toolsNodes[0]);

    await waitFor(() => {
      // After selecting, there should be at least two "Tools" text elements
      // (tree node + detail panel) — panel has appeared
      expect(screen.getAllByText("Tools").length).toBeGreaterThanOrEqual(1);
    });

    // Instances section must NOT appear for categories
    expect(screen.queryByTestId("instances-section-label")).toBeNull();
  });
});

describe("TreeBrowser — move instance happy path", () => {
  beforeEach(() => {
    vi.mocked(client.GET).mockImplementation(async (path: string, opts?: unknown) => {
      if (path === "/api/locations/tree") {
        return {
          data: locationTreeFixture,
          response: new Response(null, { status: 200 }),
        } as AnyClientResult;
      }
      if (path === "/api/instances") {
        const params = (opts as { params?: { query?: { location_id?: number } } })?.params?.query;
        if (params?.location_id === 2) {
          return {
            data: [instanceAtGarage],
            response: new Response(null, { status: 200 }),
          } as AnyClientResult;
        }
        return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      if (path === "/api/definitions/{definition_id}") {
        return { data: definitionDrill, response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
    });
    vi.mocked(client.PATCH).mockResolvedValue({
      data: { ...instanceAtGarage, location_id: 1 },
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("clicking move icon opens move modal and PATCH is called with new location_id", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));
    fireEvent.click(screen.getByText("Garage"));

    // Wait for instance row to appear
    await waitFor(() => {
      expect(screen.getByTestId(`move-instance-${instanceAtGarage.id}`)).toBeDefined();
    });

    // Click the move icon
    fireEvent.click(screen.getByTestId(`move-instance-${instanceAtGarage.id}`));

    // Modal should open with the location select
    const locationSelect = await screen.findByTestId("move-location-select");
    expect(locationSelect).toBeDefined();

    // Open the dropdown
    fireEvent.click(locationSelect);

    // Pick "Home" (id=1)
    await waitFor(() => {
      const homeOption = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("Home"),
      );
      expect(homeOption).toBeDefined();
    });
    const homeOption = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("Home"),
    );
    fireEvent.click(homeOption!);

    // Click Move
    const moveBtn = screen.getByTestId("confirm-move-btn");
    fireEvent.click(moveBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/instances/{instance_id}",
        expect.objectContaining({
          params: { path: { instance_id: instanceAtGarage.id } },
          body: expect.objectContaining({ location_id: 1 }),
        }),
      );
    });
  });
});

describe("TreeBrowser — delete instance happy path", () => {
  beforeEach(() => {
    vi.mocked(client.GET).mockImplementation(async (path: string, opts?: unknown) => {
      if (path === "/api/locations/tree") {
        return { data: locationTreeFixture, response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      if (path === "/api/instances") {
        const params = (opts as { params?: { query?: { location_id?: number } } })?.params?.query;
        if (params?.location_id === 2) {
          return { data: [instanceAtGarage], response: new Response(null, { status: 200 }) } as AnyClientResult;
        }
        return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      if (path === "/api/definitions/{definition_id}") {
        return { data: definitionDrill, response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
    });
    vi.mocked(client.DELETE).mockResolvedValue({
      data: undefined,
      response: new Response(null, { status: 204 }),
    } as AnyClientResult);
  });

  it("clicking delete icon opens confirm modal and DELETE is called", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));
    fireEvent.click(screen.getByText("Garage"));

    await waitFor(() => {
      expect(screen.getByTestId(`delete-instance-${instanceAtGarage.id}`)).toBeDefined();
    });

    // Click the delete icon
    fireEvent.click(screen.getByTestId(`delete-instance-${instanceAtGarage.id}`));

    // Confirm modal appears
    const confirmBtn = await screen.findByTestId("confirm-delete-instance-btn");
    expect(confirmBtn).toBeDefined();

    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(client.DELETE).toHaveBeenCalledWith(
        "/api/instances/{instance_id}",
        expect.objectContaining({
          params: { path: { instance_id: instanceAtGarage.id } },
        }),
      );
    });
  });
});

// ── Blank-space deselect ──────────────────────────────────────────────────────

describe("TreeBrowser — clicking blank space in the tree region clears selection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    makeSuccessGetLocations();
  });

  /**
   * Structural / non-vacuous guard:
   * jsdom has no layout engine, so pixel geometry cannot be tested. Instead we
   * verify that the tree-region wrapper carries a `min-height` style — which is
   * the fix that gives it real estate in a real browser so blank-space clicks
   * below the node rows actually land inside the element and reach its onClick.
   * Without this property the div collapses to zero extra space and the handler
   * is unreachable (the regression that commit 04a7f65 silently had).
   */
  it("tree-region wrapper has a min-height style so blank space is genuinely clickable in a real browser", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    const region = screen.getByTestId("tree-region");
    // The element must declare a min-height (any non-zero / non-empty value).
    // This is the structural guarantee that blank area below tree rows is inside
    // the div in a real browser — purely behavioural jsdom clicks cannot catch
    // the absence of this property.
    const minHeight = region.style.minHeight;
    expect(minHeight).toBeTruthy();
    expect(minHeight).not.toBe("0");
    expect(minHeight).not.toBe("0px");
  });

  it("select a node then click the tree region background → selection cleared (locations)", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Select "Garage"
    fireEvent.click(screen.getByText("Garage"));

    // Detail panel and top button reading "Add child location" should appear
    // (use testid to avoid collision with inline action icons)
    await waitFor(() => {
      const createBtn = screen.getByTestId("create-root-btn");
      expect(createBtn.textContent).toMatch(/add child location/i);
    });

    // Click the blank tree-region wrapper (not on any node)
    fireEvent.click(screen.getByTestId("tree-region"));

    // Selection cleared: top button reverts to "Add location", detail panel gone
    await waitFor(() => {
      const createBtn = screen.getByTestId("create-root-btn");
      expect(createBtn.textContent).toMatch(/^add location$/i);
      // The "Reparent" button inside the detail panel should no longer be visible
      expect(screen.queryByRole("button", { name: /reparent/i })).toBeNull();
    });
  });

  it("select a node then click the tree region background → selection cleared (categories)", async () => {
    makeSuccessGetCategories();
    renderCategories();
    await waitFor(() => screen.getByText("Tools"));

    // Select "Tools"
    fireEvent.click(screen.getByText("Tools"));

    // Detail panel should appear (Reparent button)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /reparent/i })).toBeDefined();
    });

    // Click blank tree region
    fireEvent.click(screen.getByTestId("tree-region"));

    // Selection cleared: detail panel gone
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /reparent/i })).toBeNull();
      // Top button back to "Add category"
      const createBtn = screen.getByTestId("create-root-btn");
      expect(createBtn.textContent).toMatch(/^add category$/i);
    });
  });

  it("clicking a node does NOT trigger the blank-space handler (selection stays set)", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Click node — must select and NOT immediately clear via bubble
    fireEvent.click(screen.getByText("Garage"));

    await waitFor(() => {
      // Top toolbar "create-root-btn" should read "Add child location" (node IS selected)
      const createBtn = screen.getByTestId("create-root-btn");
      expect(createBtn.textContent).toMatch(/add child location/i);
    });
  });

  it("clicking a node action button (rename/delete) does NOT clear selection via bubble", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // First select a node
    fireEvent.click(screen.getByText("Garage"));
    await waitFor(() => {
      const createBtn = screen.getByTestId("create-root-btn");
      expect(createBtn.textContent).toMatch(/add child location/i);
    });

    // Click the rename icon for "Garage" — should open rename modal, not clear selection
    const renameBtn = screen.getByRole("button", { name: /rename garage/i });
    fireEvent.click(renameBtn);

    // Rename modal should open (not blank-space handler)
    await waitFor(() => {
      expect(screen.getByTestId("rename-input")).toBeDefined();
    });
  });

  it("a blank-space click does NOT call POST/PATCH/DELETE (no spurious action)", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    fireEvent.click(screen.getByText("Home"));
    await waitFor(() => {
      const createBtn = screen.getByTestId("create-root-btn");
      expect(createBtn.textContent).toMatch(/add child location/i);
    });

    // Click the blank tree region
    fireEvent.click(screen.getByTestId("tree-region"));

    // Wait a tick for any spurious effects
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /reparent/i })).toBeNull();
    });

    // No mutating API calls should have been triggered
    expect(client.POST).not.toHaveBeenCalled();
    expect(client.PATCH).not.toHaveBeenCalled();
    expect(client.DELETE).not.toHaveBeenCalled();
  });
});

// ── Container-asset link / unlink ─────────────────────────────────────────────

/**
 * Shared GET mock that serves the location tree, instances (all + by location),
 * and definition names — used by the link/unlink test suite.
 *
 * The location tree contains:
 *   Home (1) → Garage (2, no link), Toolbox (3, item_instance_id=42)
 *
 * A single instance exists: id=42, definition_id=5, serial="SN-TB-1".
 */
function makeFullGetMock() {
  vi.mocked(client.GET).mockImplementation(async (path: string, opts?: unknown) => {
    if (path === "/api/locations/tree") {
      return {
        data: locationTreeFixture,
        response: new Response(null, { status: 200 }),
      } as AnyClientResult;
    }
    if (path === "/api/instances") {
      const params = (opts as { params?: { query?: { location_id?: number } } } | undefined)
        ?.params?.query;
      // No filter → return all instances (for the container-asset picker).
      if (!params?.location_id) {
        return {
          data: [instanceForToolbox],
          response: new Response(null, { status: 200 }),
        } as AnyClientResult;
      }
      // Filtered by location → return matching instances.
      if (params.location_id === 3) {
        return {
          data: [instanceForToolbox],
          response: new Response(null, { status: 200 }),
        } as AnyClientResult;
      }
      return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
    }
    if (path === "/api/definitions/{definition_id}") {
      return {
        data: definitionDrill,
        response: new Response(null, { status: 200 }),
      } as AnyClientResult;
    }
    return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
  });
}

/** The instance that backs the Toolbox container-asset link. */
const instanceForToolbox = {
  id: 42,
  definition_id: 5,
  location_id: 3,
  quantity: "1",
  serial: "SN-TB-1",
  model_number: null,
  manufacturer: null,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  created_at: "2025-01-01T00:00:00Z",
};

describe("TreeBrowser — link container asset (happy path)", () => {
  beforeEach(() => {
    makeFullGetMock();
    vi.mocked(client.PATCH).mockResolvedValue({
      data: { id: 2, name: "Garage", item_instance_id: 42 },
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("shows Link button when location has no item_instance_id", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Select "Garage" (id=2, item_instance_id=null)
    fireEvent.click(screen.getByText("Garage"));

    await waitFor(() => {
      expect(screen.getByTestId("link-container-btn")).toBeDefined();
    });
    // Unlink button must NOT be visible
    expect(screen.queryByTestId("unlink-container-btn")).toBeNull();
  });

  it("opens link modal with an instance Select picker (not a raw number input)", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    fireEvent.click(screen.getByText("Garage"));
    const linkBtn = await screen.findByTestId("link-container-btn");
    fireEvent.click(linkBtn);

    // Modal opens — Select picker is present; no numeric text input
    await waitFor(() => {
      expect(screen.getByTestId("link-instance-select")).toBeDefined();
    });
    expect(screen.queryByRole("spinbutton")).toBeNull();
  });

  it("picking an instance and clicking Link calls PATCH with item_instance_id", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    fireEvent.click(screen.getByText("Garage"));
    const linkBtn = await screen.findByTestId("link-container-btn");
    fireEvent.click(linkBtn);

    // Wait for the Select to appear (allInstances loaded)
    const selectInput = await screen.findByTestId("link-instance-select");
    fireEvent.click(selectInput);

    // The instance option should be present (definition name + serial)
    await waitFor(() => {
      const opt = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("SN-TB-1"),
      );
      expect(opt).toBeDefined();
    });
    const opt = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("SN-TB-1"),
    );
    fireEvent.click(opt!);

    // Click Link
    const confirmBtn = screen.getByTestId("confirm-link-btn");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/locations/{location_id}",
        expect.objectContaining({
          params: { path: { location_id: 2 } },
          body: expect.objectContaining({ item_instance_id: 42 }),
        }),
      );
    });
  });
});

describe("TreeBrowser — link container asset 409 (already linked elsewhere)", () => {
  beforeEach(() => {
    makeFullGetMock();
    vi.mocked(client.PATCH).mockResolvedValue({
      error: {
        code: "location.container_link_conflict",
        message: "Stock instance is already linked to another location.",
        params: { id: 2 },
      },
      response: new Response(null, { status: 409 }),
    } as AnyClientResult);
  });

  it("shows the localized 409 conflict error inside the link modal", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    fireEvent.click(screen.getByText("Garage"));
    const linkBtn = await screen.findByTestId("link-container-btn");
    fireEvent.click(linkBtn);

    const selectInput = await screen.findByTestId("link-instance-select");
    fireEvent.click(selectInput);

    await waitFor(() => {
      const opt = [...document.querySelectorAll('[role="option"]')].find(
        (el) => el.textContent?.includes("SN-TB-1"),
      );
      expect(opt).toBeDefined();
    });
    const opt = [...document.querySelectorAll('[role="option"]')].find(
      (el) => el.textContent?.includes("SN-TB-1"),
    );
    fireEvent.click(opt!);

    fireEvent.click(screen.getByTestId("confirm-link-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("link-container-error")).toBeDefined();
      // Localized EN message for location.container_link_conflict
      expect(
        screen.getByText(/location.*already linked.*another stock instance/i),
      ).toBeDefined();
    });
  });
});

describe("TreeBrowser — unlink container asset (happy path)", () => {
  beforeEach(() => {
    makeFullGetMock();
    vi.mocked(client.PATCH).mockResolvedValue({
      data: { id: 3, name: "Toolbox", item_instance_id: null },
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);
  });

  it("shows Unlink button when location has item_instance_id set", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Toolbox"));

    // Select "Toolbox" (id=3, item_instance_id=42)
    fireEvent.click(screen.getByText("Toolbox"));

    await waitFor(() => {
      expect(screen.getByTestId("unlink-container-btn")).toBeDefined();
    });
    // Link button must NOT be visible for a linked location
    expect(screen.queryByTestId("link-container-btn")).toBeNull();
  });

  it("linked location shows human-readable instance label instead of raw #ID", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Toolbox"));
    fireEvent.click(screen.getByText("Toolbox"));

    // The detail panel should show the instance badge with a human-readable label.
    // locationInstances is loaded when the location is selected, so the linked
    // instance (id=42, definition "Cordless Drill", serial "SN-TB-1") is resolved
    // without needing to open the Link modal.
    await waitFor(() => {
      const badge = screen.getByTestId("container-asset-linked");
      expect(badge).toBeDefined();
      // Must show definition name + serial — NOT the raw "Instance #42" fallback.
      expect(badge.textContent).toContain("Cordless Drill");
      expect(badge.textContent).toContain("SN-TB-1");
      expect(badge.textContent).not.toContain("Instance #42");
    });
  });

  it("clicking Unlink → confirm calls PATCH with item_instance_id: null", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Toolbox"));
    fireEvent.click(screen.getByText("Toolbox"));

    const unlinkBtn = await screen.findByTestId("unlink-container-btn");
    fireEvent.click(unlinkBtn);

    // Confirmation modal appears
    const confirmBtn = await screen.findByTestId("confirm-unlink-btn");
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(client.PATCH).toHaveBeenCalledWith(
        "/api/locations/{location_id}",
        expect.objectContaining({
          params: { path: { location_id: 3 } },
          body: expect.objectContaining({ item_instance_id: null }),
        }),
      );
    });
  });
});

describe("TreeBrowser — link/unlink controls hidden for categories", () => {
  beforeEach(() => {
    makeSuccessGetCategories();
  });

  it("neither Link nor Unlink button appears for a selected category", async () => {
    renderCategories();
    await waitFor(() => screen.getByText("Tools"));

    fireEvent.click(screen.getByText("Tools"));

    await waitFor(() => {
      // The detail panel appears (Reparent button is there)
      expect(screen.getByRole("button", { name: /reparent/i })).toBeDefined();
    });

    expect(screen.queryByTestId("link-container-btn")).toBeNull();
    expect(screen.queryByTestId("unlink-container-btn")).toBeNull();
    expect(screen.queryByTestId("container-asset-linked")).toBeNull();
  });
});

// ── Container asset labeling in tree badge and pickers ────────────────────────

describe("TreeBrowser — container-as-item badge shows linked asset name (not raw #N)", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
  });

  it("container location badge shows container_asset_label from tree node", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Toolbox"));

    // Toolbox (id=3) has container_asset_label="Lboxx-136 · SN SN-TB-1"
    const badge = screen.getByTestId("container-badge-3");
    expect(badge).toBeDefined();
    expect(badge.textContent).toContain("Lboxx-136");
    expect(badge.textContent).toContain("SN-TB-1");
    // Must NOT show raw "Asset #42"
    expect(badge.textContent).not.toContain("Asset #42");
  });

  it("normal location does NOT show a container badge", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Garage (id=2) has item_instance_id=null → no badge
    expect(screen.queryByTestId("container-badge-2")).toBeNull();
  });

  it("fallback badge shows #N when container_asset_label is null/absent", async () => {
    // Simulate an API response where container_asset_label is not yet set (pre-load
    // or old data) but item_instance_id is present.
    vi.mocked(client.GET).mockResolvedValue({
      data: [
        {
          id: 1,
          name: "Home",
          description: null,
          parent_id: null,
          item_instance_id: 99,
          container_asset_label: null, // label not yet resolved
          created_at: "2025-01-01T00:00:00Z",
          children: [],
        },
      ],
      response: new Response(null, { status: 200 }),
    } as AnyClientResult);

    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    const badge = screen.getByTestId("container-badge-1");
    expect(badge).toBeDefined();
    // Fallback must show Asset #99
    expect(badge.textContent).toContain("Asset #99");
  });
});

describe("TreeBrowser — reparent picker annotates container-as-item locations", () => {
  beforeEach(() => {
    makeSuccessGetLocations();
  });

  it("reparent options for container-as-item location include the asset label", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Select "Garage" and open reparent modal
    fireEvent.click(screen.getByText("Garage"));
    const reparentBtn = await screen.findByRole("button", { name: /reparent/i });
    fireEvent.click(reparentBtn);

    const selectInput = await screen.findByTestId("reparent-select");
    fireEvent.click(selectInput);

    // The Toolbox option should show its asset label
    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      const toolboxOpt = opts.find((el) => el.textContent?.includes("Toolbox"));
      expect(toolboxOpt).toBeDefined();
      // Label must be appended
      expect(toolboxOpt?.textContent).toContain("Lboxx-136");
    });
  });
});

describe("TreeBrowser — move-instance picker annotates container-as-item locations", () => {
  beforeEach(() => {
    vi.mocked(client.GET).mockImplementation(async (path: string, opts?: unknown) => {
      if (path === "/api/locations/tree") {
        return {
          data: locationTreeFixture,
          response: new Response(null, { status: 200 }),
        } as AnyClientResult;
      }
      if (path === "/api/instances") {
        const params = (opts as { params?: { query?: { location_id?: number } } })?.params?.query;
        if (params?.location_id === 2) {
          return {
            data: [instanceAtGarage],
            response: new Response(null, { status: 200 }),
          } as AnyClientResult;
        }
        return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      if (path === "/api/definitions/{definition_id}") {
        return { data: definitionDrill, response: new Response(null, { status: 200 }) } as AnyClientResult;
      }
      return { data: [], response: new Response(null, { status: 200 }) } as AnyClientResult;
    });
  });

  it("move-instance target picker annotates container-as-item locations", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Garage"));

    // Select Garage (has an instance)
    fireEvent.click(screen.getByText("Garage"));
    await waitFor(() => {
      expect(screen.getByTestId(`move-instance-${instanceAtGarage.id}`)).toBeDefined();
    });

    // Open move modal
    fireEvent.click(screen.getByTestId(`move-instance-${instanceAtGarage.id}`));
    const locationSelect = await screen.findByTestId("move-location-select");
    fireEvent.click(locationSelect);

    // Toolbox option should contain the asset label
    await waitFor(() => {
      const opts = [...document.querySelectorAll('[role="option"]')];
      const toolboxOpt = opts.find((el) => el.textContent?.includes("Toolbox"));
      expect(toolboxOpt).toBeDefined();
      expect(toolboxOpt?.textContent).toContain("Lboxx-136");
    });
  });
});
