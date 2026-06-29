/**
 * M7 Step 6 — Shopping list page tests.
 *
 * Coverage:
 *  1. List renders auto row with "Auto" source badge.
 *  2. List renders manual row with "Manual" source badge.
 *  3. Add a manual item (free-text) via the add modal.
 *  4. Edit quantity via the edit modal.
 *  5. Check off a free-text item (no intake modal, direct POST).
 *  6. Check off a definition-linked item — check-off modal opens, "Just check off".
 *  7. Check off with intake — modal opens, fill location + qty, "Add to stock".
 *  8. Clear purchased calls POST /shopping-list/clear-purchased.
 *  9. Refresh calls POST /shopping-list/refresh and auto row appears after refresh.
 * 10. Viewer sees the list read-only (no add/edit/check/delete controls).
 * 11. shoppingList namespace en + zh catalog parity (key sets identical).
 * 12. shoppingList namespace: en values are non-empty strings.
 * 13. errors namespace includes shopping_list.not_found in both en and zh.
 * 14. nav namespace includes shoppingList in both en and zh.
 *
 * All component tests pin to 'en' (vitest setup.ts resets before each test).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

// i18n singleton must be initialized before any component renders.
import "../i18n/index.js";

import { ShoppingList } from "../pages/ShoppingList";
import { AuthProvider } from "../auth/AuthContext";
import type { components } from "../api/schema";

// Catalog imports for parity tests
import enShoppingList from "../i18n/locales/en/shoppingList.json";
import zhShoppingList from "../i18n/locales/zh/shoppingList.json";
import enErrors from "../i18n/locales/en/errors.json";
import zhErrors from "../i18n/locales/zh/errors.json";
import enNav from "../i18n/locales/en/nav.json";
import zhNav from "../i18n/locales/zh/nav.json";

/** Mock the typed API client — all tests control its responses per-test. */
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

type ShoppingListItem = components["schemas"]["ShoppingListItemResponse"];
type UserResponse = components["schemas"]["UserResponse"];

function makeUser(role: "admin" | "member" | "viewer"): UserResponse {
  return {
    id: 1,
    email: `${role}@test.com`,
    role,
    is_active: true,
    notify_in_app: true,
    notify_email_digest: true,
    created_at: "2026-01-01T00:00:00Z",
    preferred_language: "en",
  };
}

const autoItem: ShoppingListItem = {
  id: 1,
  source: "auto",
  definition_id: 10,
  name: "Oat Milk",
  desired_quantity: "2.000000",
  unit: "cartons",
  note: "UHT",
  purchased_at: null,
  created_by: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const manualItem: ShoppingListItem = {
  id: 2,
  source: "manual",
  definition_id: null,
  name: "Paper Towels",
  desired_quantity: "3.000000",
  unit: "rolls",
  note: null,
  purchased_at: null,
  created_by: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const purchasedItem: ShoppingListItem = {
  id: 3,
  source: "manual",
  definition_id: null,
  name: "Coffee Beans",
  desired_quantity: null,
  unit: null,
  note: null,
  purchased_at: "2026-01-02T10:00:00Z",
  created_by: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-02T10:00:00Z",
};

const mockDef = {
  id: 10,
  name: "Oat Milk",
  unit: "cartons",
  stock_tracking_mode: "exact",
  description: null,
  category_id: null,
  kind_id: 1,
  kind: { id: 1, code: "consumable", name: "Consumable", is_system: true, created_at: "2026-01-01T00:00:00Z" },
  default_location_id: null,
  min_stock: "1.000000",
  default_best_before_days: null,
  reminder_lead_days: null,
  custom_fields: null,
  responsible_user_id: null,
  created_at: "2026-01-01T00:00:00Z",
};

const mockLocation = {
  id: 5,
  name: "Kitchen",
  description: null,
  parent_id: null,
  item_instance_id: null,
  container_asset_label: null,
  created_at: "2026-01-01T00:00:00Z",
};

/** Default GET mock: returns [autoItem, manualItem], plus defs/locations. */
function mockDefaultLoad(extraItems: ShoppingListItem[] = []) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/shopping-list") {
      return {
        data: [autoItem, manualItem, ...extraItems],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/definitions") {
      return {
        data: [mockDef],
        response: new Response(null, { status: 200 }),
      };
    }
    if (path === "/api/locations") {
      return {
        data: [mockLocation],
        response: new Response(null, { status: 200 }),
      };
    }
    return {
      data: null,
      error: { code: "http.404", message: "Not found" },
      response: new Response(null, { status: 404 }),
    };
  });
}

/** Wrap the ShoppingList page with Router + Mantine + Auth (member by default). */
function renderPage(role: "admin" | "member" | "viewer" = "member") {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <AuthProvider
          user={makeUser(role)}
          onRefresh={vi.fn()}
          onLogout={vi.fn()}
        >
          <ShoppingList />
        </AuthProvider>
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── Deep key extraction (reused from i18n-catalog.test.ts pattern) ────────────

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

// ── 1. List renders auto row with "Auto" source badge ─────────────────────────

describe("ShoppingList — list rendering", () => {
  beforeEach(() => {
    mockDefaultLoad();
  });

  it("renders the auto row with the 'Auto' source badge", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("shopping-row-1")).toBeDefined();
    });
    // Source badge for auto item
    expect(screen.getByTestId("source-badge-auto")).toBeDefined();
    expect(screen.getByTestId("source-badge-auto").textContent).toContain("Auto");
    // Name is rendered
    expect(screen.getByTestId("name-1").textContent).toContain("Oat Milk");
  });

  it("renders the manual row with the 'Manual' source badge", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("shopping-row-2")).toBeDefined();
    });
    expect(screen.getByTestId("source-badge-manual")).toBeDefined();
    expect(screen.getByTestId("source-badge-manual").textContent).toContain("Manual");
    expect(screen.getByTestId("name-2").textContent).toContain("Paper Towels");
  });

  it("renders desired quantity for an auto item", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("qty-1")).toBeDefined();
    });
    // formatQuantity("2.000000") → "2"
    expect(screen.getByTestId("qty-1").textContent).toContain("2");
  });

  it("renders the open-section label", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("open-section-label")).toBeDefined();
    });
    expect(screen.getByTestId("open-section-label").textContent).toBe("To buy");
  });
});

// ── 2. Add manual item ────────────────────────────────────────────────────────

describe("ShoppingList — add manual item", () => {
  beforeEach(() => {
    mockDefaultLoad();
    // Mock POST /shopping-list to succeed
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: manualItem, response: new Response(null, { status: 201 }) } as any,
    );
  });

  it("opens the add modal when 'Add item' is clicked", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("add-item-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("add-item-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("add-name-input")).toBeDefined();
    });
  });

  it("calls POST /shopping-list with the entered free-text name", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("add-item-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("add-item-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("add-name-input")).toBeDefined();
    });

    fireEvent.change(screen.getByTestId("add-name-input"), {
      target: { value: "Dish Soap" },
    });

    fireEvent.click(screen.getByTestId("add-submit-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list",
        expect.objectContaining({
          body: expect.objectContaining({ name: "Dish Soap" }),
        }),
      );
    });
  });
});

// ── 3. Edit quantity ──────────────────────────────────────────────────────────

describe("ShoppingList — edit quantity", () => {
  beforeEach(() => {
    mockDefaultLoad();
    vi.mocked(client.PATCH).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: { ...autoItem, desired_quantity: "5.000000" }, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("opens the edit modal when the edit button is clicked", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("edit-1")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("edit-1"));
    await waitFor(() => {
      expect(screen.getByTestId("edit-submit-btn")).toBeDefined();
    });
  });

  it("calls PATCH /shopping-list/{item_id} on save", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("edit-1")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("edit-1"));

    await waitFor(() => {
      expect(screen.getByTestId("edit-submit-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("edit-submit-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.PATCH)).toHaveBeenCalledWith(
        "/api/shopping-list/{item_id}",
        expect.objectContaining({
          params: { path: { item_id: 1 } },
        }),
      );
    });
  });
});

// ── 4. Check off free-text item (no intake modal) ─────────────────────────────

describe("ShoppingList — check off free-text item", () => {
  beforeEach(() => {
    mockDefaultLoad();
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: { item: { ...manualItem, purchased_at: "2026-01-03T00:00:00Z" } }, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("directly POSTs check for a free-text item (no modal shown)", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("check-2")).toBeDefined();
    });
    // manualItem has definition_id = null → direct check-off, no modal
    fireEvent.click(screen.getByTestId("check-2"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list/{item_id}/check",
        expect.objectContaining({
          params: { path: { item_id: 2 } },
        }),
      );
    });

    // Confirm the modal did NOT open (no just-check-btn visible)
    expect(screen.queryByTestId("just-check-btn")).toBeNull();
  });
});

// ── 5. Check off definition-linked item — "Just check off" ────────────────────

describe("ShoppingList — check off definition-linked item (just check off)", () => {
  beforeEach(() => {
    mockDefaultLoad();
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: { item: { ...autoItem, purchased_at: "2026-01-03T00:00:00Z" } }, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("opens the check-off modal for a definition-linked item", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("check-1")).toBeDefined();
    });
    // autoItem has definition_id = 10 → modal should open
    fireEvent.click(screen.getByTestId("check-1"));

    await waitFor(() => {
      expect(screen.getByTestId("just-check-btn")).toBeDefined();
    });
    expect(screen.getByTestId("check-intake-btn")).toBeDefined();
  });

  it("calls POST /check with no intake when 'Just check off' is clicked", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("check-1")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("check-1"));

    await waitFor(() => {
      expect(screen.getByTestId("just-check-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("just-check-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list/{item_id}/check",
        expect.objectContaining({
          params: { path: { item_id: 1 } },
        }),
      );
    });
  });
});

// ── 6. Check off with intake ──────────────────────────────────────────────────

describe("ShoppingList — check off with intake", () => {
  beforeEach(() => {
    mockDefaultLoad();
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: { item: { ...autoItem, purchased_at: "2026-01-03T00:00:00Z" }, created_instance_id: 99 }, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("calls POST /check with intake when 'Check off & add to stock' is clicked", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("check-1")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("check-1"));

    await waitFor(() => {
      expect(screen.getByTestId("check-intake-btn")).toBeDefined();
    });

    // Click "Add to Stock" (with default prefilled quantity from item)
    fireEvent.click(screen.getByTestId("check-intake-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list/{item_id}/check",
        expect.objectContaining({
          params: { path: { item_id: 1 } },
          body: expect.objectContaining({
            // Quantity pre-filled from autoItem.desired_quantity ("2.000000");
            // no location selected so location_id is null.
            intake: expect.objectContaining({
              quantity: "2.000000",
              location_id: null,
            }),
          }),
        }),
      );
    });
  });
});

// ── 7. Clear purchased ────────────────────────────────────────────────────────

describe("ShoppingList — clear purchased", () => {
  beforeEach(() => {
    // Include a purchased item so the "Clear purchased" button appears
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/shopping-list") {
        return {
          data: [autoItem, manualItem, purchasedItem],
          response: new Response(null, { status: 200 }),
        };
      }
      if (path === "/api/definitions") {
        return { data: [mockDef], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [mockLocation], response: new Response(null, { status: 200 }) };
      }
      return {
        data: null,
        error: { code: "http.404", message: "Not found" },
        response: new Response(null, { status: 404 }),
      };
    });
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: { deleted_count: 1 }, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("'Clear purchased' button is present when there are purchased items", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("clear-purchased-btn")).toBeDefined();
    });
  });

  it("calls POST /shopping-list/clear-purchased when clicked", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("clear-purchased-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("clear-purchased-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list/clear-purchased",
      );
    });
  });
});

// ── 7b. Uncheck purchased item ────────────────────────────────────────────────

describe("ShoppingList — uncheck purchased item", () => {
  beforeEach(() => {
    // Include a purchased item so there is something to uncheck.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/shopping-list") {
        return {
          data: [autoItem, manualItem, purchasedItem],
          response: new Response(null, { status: 200 }),
        };
      }
      if (path === "/api/definitions") {
        return { data: [mockDef], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [mockLocation], response: new Response(null, { status: 200 }) };
      }
      return {
        data: null,
        error: { code: "http.404", message: "Not found" },
        response: new Response(null, { status: 404 }),
      };
    });
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: { ...purchasedItem, purchased_at: null }, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("clicking a purchased item's checkbox calls POST /{id}/uncheck", async () => {
    renderPage();
    // Expand the purchased section so the row is accessible.
    await waitFor(() => {
      expect(screen.getByTestId("purchased-toggle")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("purchased-toggle"));

    // The purchased item checkbox is rendered with data-testid="check-3".
    await waitFor(() => {
      expect(screen.getByTestId("check-3")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("check-3"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list/{item_id}/uncheck",
        expect.objectContaining({
          params: { path: { item_id: 3 } },
        }),
      );
    });
  });
});

// ── 7c. Delete item ────────────────────────────────────────────────────────────

describe("ShoppingList — delete item", () => {
  beforeEach(() => {
    // First GET returns both open items; subsequent GETs (after delete) return only manualItem.
    let getCallCount = 0;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/shopping-list") {
        getCallCount++;
        return {
          data: getCallCount === 1 ? [autoItem, manualItem] : [manualItem],
          response: new Response(null, { status: 200 }),
        };
      }
      if (path === "/api/definitions") {
        return { data: [mockDef], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [mockLocation], response: new Response(null, { status: 200 }) };
      }
      return {
        data: null,
        error: { code: "http.404", message: "Not found" },
        response: new Response(null, { status: 404 }),
      };
    });
    vi.mocked(client.DELETE).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: null, response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("clicking delete calls DELETE /{id} and the row disappears", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("delete-1")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("delete-1"));

    await waitFor(() => {
      expect(vi.mocked(client.DELETE)).toHaveBeenCalledWith(
        "/api/shopping-list/{item_id}",
        expect.objectContaining({
          params: { path: { item_id: 1 } },
        }),
      );
    });

    // After reload the row is gone (autoItem was deleted; only manualItem remains).
    await waitFor(() => {
      expect(screen.queryByTestId("shopping-row-1")).toBeNull();
    });
    // manualItem (id 2) is still present.
    expect(screen.getByTestId("shopping-row-2")).toBeDefined();
  });
});

// ── 8. Refresh (and auto row appears after refresh) ───────────────────────────

describe("ShoppingList — refresh", () => {
  const newAutoItem: ShoppingListItem = {
    id: 99,
    source: "auto",
    definition_id: 10,
    name: "Oat Milk",
    desired_quantity: null,
    unit: "cartons",
    note: null,
    purchased_at: null,
    created_by: null,
    created_at: "2026-01-03T00:00:00Z",
    updated_at: "2026-01-03T00:00:00Z",
  };

  beforeEach(() => {
    // Before refresh: no items; after refresh: one auto item appears
    let callCount = 0;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/shopping-list") {
        callCount++;
        // First call (initial load): empty list
        // Subsequent calls (after refresh): list contains the new auto item
        const data = callCount === 1 ? [] : [newAutoItem];
        return { data, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions") {
        return { data: [mockDef], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [mockLocation], response: new Response(null, { status: 200 }) };
      }
      return {
        data: null,
        error: { code: "http.404", message: "Not found" },
        response: new Response(null, { status: 404 }),
      };
    });

    // Mock POST /refresh to succeed (returns the reconciled list, but we reload via GET)
    vi.mocked(client.POST).mockResolvedValue(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { data: [newAutoItem], response: new Response(null, { status: 200 }) } as any,
    );
  });

  it("shows empty state before refresh", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("shopping-empty")).toBeDefined();
    });
  });

  it("auto row appears after Refresh is clicked", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("refresh-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("refresh-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/shopping-list/refresh",
      );
    });

    // After reload, the new auto item (id 99) should appear
    await waitFor(() => {
      expect(screen.getByTestId("shopping-row-99")).toBeDefined();
    });
    expect(screen.getByTestId("source-badge-auto")).toBeDefined();
  });
});

// ── 9. Viewer read-only gating ────────────────────────────────────────────────

describe("ShoppingList — viewer read-only", () => {
  beforeEach(() => {
    mockDefaultLoad();
  });

  it("viewer does NOT see the add-item button", async () => {
    renderPage("viewer");
    await waitFor(() => {
      // List loaded — auto item visible
      expect(screen.getByTestId("shopping-row-1")).toBeDefined();
    });
    expect(screen.queryByTestId("add-item-btn")).toBeNull();
  });

  it("viewer does NOT see per-row edit buttons", async () => {
    renderPage("viewer");
    await waitFor(() => {
      expect(screen.getByTestId("shopping-row-1")).toBeDefined();
    });
    expect(screen.queryByTestId("edit-1")).toBeNull();
    expect(screen.queryByTestId("edit-2")).toBeNull();
  });

  it("viewer does NOT see per-row delete buttons", async () => {
    renderPage("viewer");
    await waitFor(() => {
      expect(screen.getByTestId("shopping-row-1")).toBeDefined();
    });
    expect(screen.queryByTestId("delete-1")).toBeNull();
    expect(screen.queryByTestId("delete-2")).toBeNull();
  });

  it("viewer STILL sees the list (read-only display)", async () => {
    renderPage("viewer");
    await waitFor(() => {
      expect(screen.getByTestId("name-1")).toBeDefined();
    });
    // Items are readable
    expect(screen.getByTestId("name-1").textContent).toContain("Oat Milk");
    expect(screen.getByTestId("name-2").textContent).toContain("Paper Towels");
  });

  it("viewer checkboxes are disabled (read-only)", async () => {
    renderPage("viewer");
    await waitFor(() => {
      expect(screen.getByTestId("shopping-row-1")).toBeDefined();
    });
    // The check-1 testid is only on the interactive checkbox (canEdit).
    // For viewer, we render a plain disabled Checkbox with no testid.
    expect(screen.queryByTestId("check-1")).toBeNull();
  });

  it("viewer does NOT see the refresh button", async () => {
    renderPage("viewer");
    await waitFor(() => {
      // List loaded — auto item visible
      expect(screen.getByTestId("shopping-row-1")).toBeDefined();
    });
    expect(screen.queryByTestId("refresh-btn")).toBeNull();
  });
});

// ── 10. i18n: shoppingList namespace en + zh key parity ──────────────────────

describe("i18n — shoppingList namespace key parity", () => {
  it("en and zh shoppingList have identical key sets", () => {
    const enKeys = collectKeys(enShoppingList).sort();
    const zhKeys = collectKeys(zhShoppingList).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(missingInZh, "Keys in en/shoppingList missing from zh/shoppingList").toEqual([]);
    expect(extraInZh, "Extra keys in zh/shoppingList not present in en/shoppingList").toEqual([]);
  });

  it("all en shoppingList values are non-empty strings", () => {
    const enKeys = collectKeys(enShoppingList);
    for (const key of enKeys) {
      const parts = key.split(".");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let val: any = enShoppingList;
      for (const part of parts) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        val = (val as any)[part];
      }
      expect(typeof val, `en/shoppingList key '${key}' should be a string`).toBe("string");
      expect((val as string).trim().length, `en/shoppingList key '${key}' should be non-empty`).toBeGreaterThan(0);
    }
  });

  it("zh translations differ from en (are actually translated)", () => {
    // Spot-check a few key values
    expect(zhShoppingList.title).not.toBe(enShoppingList.title);
    expect(zhShoppingList.empty).not.toBe(enShoppingList.empty);
    expect(zhShoppingList.source.auto).not.toBe(enShoppingList.source.auto);
  });
});

// ── 11. errors namespace includes shopping_list.not_found ────────────────────

describe("i18n — errors.shopping_list.not_found in both en and zh", () => {
  it("en/errors has shopping_list.not_found", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const enErrTyped = enErrors as any;
    expect(enErrTyped["shopping_list"]).toBeDefined();
    expect(enErrTyped["shopping_list"]["not_found"]).toBeDefined();
    expect(typeof enErrTyped["shopping_list"]["not_found"]).toBe("string");
    expect((enErrTyped["shopping_list"]["not_found"] as string).trim().length).toBeGreaterThan(0);
  });

  it("zh/errors has shopping_list.not_found", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const zhErrTyped = zhErrors as any;
    expect(zhErrTyped["shopping_list"]).toBeDefined();
    expect(zhErrTyped["shopping_list"]["not_found"]).toBeDefined();
    expect(typeof zhErrTyped["shopping_list"]["not_found"]).toBe("string");
    expect((zhErrTyped["shopping_list"]["not_found"] as string).trim().length).toBeGreaterThan(0);
  });
});

// ── 12. nav namespace includes shoppingList key ───────────────────────────────

describe("i18n — nav.shoppingList in both en and zh", () => {
  it("en/nav has shoppingList key", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((enNav as any)["shoppingList"]).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(typeof (enNav as any)["shoppingList"]).toBe("string");
  });

  it("zh/nav has shoppingList key", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((zhNav as any)["shoppingList"]).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(typeof (zhNav as any)["shoppingList"]).toBe("string");
  });

  it("zh/nav shoppingList differs from en (is translated)", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((zhNav as any)["shoppingList"]).not.toBe((enNav as any)["shoppingList"]);
  });
});
