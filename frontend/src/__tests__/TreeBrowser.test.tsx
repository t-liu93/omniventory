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
    created_at: "2025-01-01T00:00:00Z",
    children: [
      {
        id: 2,
        name: "Garage",
        description: "The garage",
        parent_id: 1,
        item_instance_id: null,
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
        <TreeBrowser resource="locations" label="Location" />
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderCategories() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <TreeBrowser resource="categories" label="Category" labelPlural="Categories" />
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
      error: { detail: "Cannot delete: location still has child locations." },
      response: new Response(null, { status: 409 }),
    } as AnyClientResult);
  });

  it("shows the 409 guard message inside the delete modal", async () => {
    renderLocations();
    await waitFor(() => screen.getByText("Home"));

    // Open the delete modal for "Home"
    const deleteBtn = screen.getByRole("button", { name: /delete home/i });
    fireEvent.click(deleteBtn);

    const confirmBtn = await screen.findByTestId("confirm-delete-btn");
    fireEvent.click(confirmBtn);

    // The 409 error detail should appear in the modal
    await waitFor(() => {
      expect(
        screen.getByText(/cannot delete.*location still has child/i),
      ).toBeDefined();
    });
  });
});
