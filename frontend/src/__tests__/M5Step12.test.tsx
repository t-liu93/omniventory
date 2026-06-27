/**
 * M5 Step 12 — Global search bar + results page.
 *
 * Coverage (per M5 §7.4, §9 Step 12, §10 Step 12):
 *
 * 1. Search page — query → grouped results:
 *    a. Renders item_definitions group with items and totals count.
 *    b. Renders stock_instances group with items and totals count.
 *    c. Renders locations / categories / tags groups.
 *    d. Shows "Showing N of M" cap badge when totals exceed the result list.
 *
 * 2. Search page — subject navigation links:
 *    a. item_definitions hit links to /items/:id.
 *    b. stock_instances hit links to /instances/:id.
 *    c. locations hit links to /locations.
 *    d. categories hit links to /categories.
 *    e. tags hit links to /items.
 *
 * 3. Search page — empty states:
 *    a. No ?q param → prompt-to-search state.
 *    b. Query with all-empty groups → no-results message.
 *
 * 4. HeaderSearchBar — navigation:
 *    a. Typing a query and pressing Enter navigates to /search?q=<query>.
 *    b. Clicking the icon button also navigates.
 *    c. Empty / whitespace-only input does not navigate.
 *
 * Conventions: vitest + Testing Library; typed client mocked; pinned to 'en';
 * no @testing-library/jest-dom (use .toBeDefined() / .toBeNull() like siblings).
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
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { Search } from "../pages/Search.js";
import { HeaderSearchBar } from "../shell/AppShell.js";
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

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** A SearchResponse with hits in every group. */
const fullResponse: Any = {
  item_definitions: [
    { id: 10, name: "Apple Juice" },
    { id: 11, name: "Orange Juice" },
  ],
  stock_instances: [
    {
      id: 20,
      definition_id: 10,
      definition_name: "Apple Juice",
      serial: "SN-001",
    },
  ],
  locations: [{ id: 30, name: "Kitchen Pantry" }],
  categories: [{ id: 40, name: "Beverages" }],
  tags: [{ id: 50, name: "organic", color: "#00aa00" }],
  totals: {
    item_definitions: 2,
    stock_instances: 1,
    locations: 1,
    categories: 1,
    tags: 1,
  },
};

/** A SearchResponse with capped item_definitions (shown 2, total 5). */
const cappedResponse: Any = {
  item_definitions: [
    { id: 10, name: "Apple Juice" },
    { id: 11, name: "Orange Juice" },
  ],
  stock_instances: [],
  locations: [],
  categories: [],
  tags: [],
  totals: {
    item_definitions: 5,
    stock_instances: 0,
    locations: 0,
    categories: 0,
    tags: 0,
  },
};

/** A SearchResponse with all empty groups. */
const emptyResponse: Any = {
  item_definitions: [],
  stock_instances: [],
  locations: [],
  categories: [],
  tags: [],
  totals: {
    item_definitions: 0,
    stock_instances: 0,
    locations: 0,
    categories: 0,
    tags: 0,
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Render the Search page at a given URL path+search string. */
function renderSearch(path = "/search") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MantineProvider>
        <Routes>
          <Route path="/search" element={<Search />} />
          <Route path="*" element={<div>Other page</div>} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

/** Captures current location for navigation assertions. */
function LocationDisplay() {
  const loc = useLocation();
  return (
    <div data-testid="location-display">
      {loc.pathname + loc.search}
    </div>
  );
}

/** Render the HeaderSearchBar in a MemoryRouter with location tracking. */
function renderHeaderSearchBar(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <MantineProvider>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <HeaderSearchBar />
                <LocationDisplay />
              </>
            }
          />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
  vi.mocked(client.GET as Any).mockResolvedValue({
    data: emptyResponse,
    error: undefined,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1. Search page — grouped results ─────────────────────────────────────────

describe("Search page — grouped results", () => {
  it("renders item_definitions group with all hits", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: fullResponse,
      error: undefined,
    });

    renderSearch("/search?q=juice");

    await waitFor(() =>
      expect(screen.getByTestId("group-item_definitions")).toBeDefined(),
    );

    expect(screen.getByTestId("result-item-def-10")).toBeDefined();
    expect(screen.getByTestId("result-item-def-11")).toBeDefined();
    expect(screen.getByTestId("result-item-def-10").textContent).toBe(
      "Apple Juice",
    );
  });

  it("renders stock_instances group with serial in label", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: fullResponse,
      error: undefined,
    });

    renderSearch("/search?q=juice");

    await waitFor(() =>
      expect(screen.getByTestId("group-stock_instances")).toBeDefined(),
    );

    expect(screen.getByTestId("result-instance-20")).toBeDefined();
    // Should include the serial
    expect(screen.getByTestId("result-instance-20").textContent).toContain(
      "SN-001",
    );
  });

  it("renders locations, categories, and tags groups", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: fullResponse,
      error: undefined,
    });

    renderSearch("/search?q=organic");

    await waitFor(() =>
      expect(screen.getByTestId("group-locations")).toBeDefined(),
    );

    expect(screen.getByTestId("result-location-30").textContent).toBe(
      "Kitchen Pantry",
    );
    expect(screen.getByTestId("result-category-40").textContent).toBe(
      "Beverages",
    );
    expect(screen.getByTestId("result-tag-50").textContent).toBe("organic");
  });

  it("shows cap badge when totals exceed displayed list length", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: cappedResponse,
      error: undefined,
    });

    renderSearch("/search?q=juice");

    await waitFor(() =>
      expect(screen.getByTestId("cap-item_definitions")).toBeDefined(),
    );

    // Badge text should mention "of 5"
    expect(screen.getByTestId("cap-item_definitions").textContent).toContain(
      "5",
    );
  });

  it("does NOT show cap badge when totals equal the list length", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: fullResponse,
      error: undefined,
    });

    renderSearch("/search?q=juice");

    await waitFor(() =>
      expect(screen.getByTestId("group-item_definitions")).toBeDefined(),
    );

    // totals.item_definitions === 2 === item_definitions.length → no cap badge
    expect(screen.queryByTestId("cap-item_definitions")).toBeNull();
  });
});

// ── 2. Search page — subject navigation links ─────────────────────────────────

describe("Search page — subject navigation links", () => {
  beforeEach(() => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: fullResponse,
      error: undefined,
    });
  });

  it("item_definitions hit links to /items/:id", async () => {
    renderSearch("/search?q=juice");

    await waitFor(() =>
      expect(screen.getByTestId("result-item-def-10")).toBeDefined(),
    );

    const link = screen.getByTestId("result-item-def-10") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/items/10");
  });

  it("stock_instances hit links to /instances/:id", async () => {
    renderSearch("/search?q=juice");

    await waitFor(() =>
      expect(screen.getByTestId("result-instance-20")).toBeDefined(),
    );

    const link = screen.getByTestId(
      "result-instance-20",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/instances/20");
  });

  it("locations hit links to /locations", async () => {
    renderSearch("/search?q=kitchen");

    await waitFor(() =>
      expect(screen.getByTestId("result-location-30")).toBeDefined(),
    );

    const link = screen.getByTestId(
      "result-location-30",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/locations");
  });

  it("categories hit links to /categories", async () => {
    renderSearch("/search?q=bev");

    await waitFor(() =>
      expect(screen.getByTestId("result-category-40")).toBeDefined(),
    );

    const link = screen.getByTestId(
      "result-category-40",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/categories");
  });

  it("tags hit links to /items (closest sensible surface)", async () => {
    renderSearch("/search?q=organic");

    await waitFor(() =>
      expect(screen.getByTestId("result-tag-50")).toBeDefined(),
    );

    const link = screen.getByTestId("result-tag-50") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/items");
  });
});

// ── 3. Search page — empty states ─────────────────────────────────────────────

describe("Search page — empty states", () => {
  it("no ?q param → shows prompt-to-search state", () => {
    renderSearch("/search");

    expect(screen.getByTestId("search-prompt")).toBeDefined();
    expect(screen.queryByTestId("search-results")).toBeNull();
    // Client should NOT be called when there's no query
    expect(client.GET).not.toHaveBeenCalled();
  });

  it("q with all-empty groups → shows no-results message", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: emptyResponse,
      error: undefined,
    });

    renderSearch("/search?q=xyz123notfound");

    await waitFor(() =>
      expect(screen.getByTestId("search-no-results")).toBeDefined(),
    );

    expect(screen.getByTestId("search-no-results").textContent).toContain(
      "xyz123notfound",
    );
    expect(screen.queryByTestId("search-results")).toBeNull();
  });

  it("API error → shows error state", async () => {
    vi.mocked(client.GET as Any).mockResolvedValue({
      data: null,
      error: { message: "Internal Server Error" },
    });

    renderSearch("/search?q=test");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeDefined(),
    );
  });
});

// ── 4. HeaderSearchBar — navigation ───────────────────────────────────────────

describe("HeaderSearchBar — navigation", () => {
  it("pressing Enter navigates to /search?q=<query>", async () => {
    renderHeaderSearchBar();

    const input = screen.getByTestId("header-search-input");
    // Mantine TextInput forwards data-testid to the underlying <input>
    fireEvent.change(input, { target: { value: "apple" } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => {
      const loc = screen.getByTestId("location-display").textContent ?? "";
      expect(loc).toBe("/search?q=apple");
    });
  });

  it("clicking the search icon button navigates to /search?q=<query>", async () => {
    renderHeaderSearchBar();

    const input = screen.getByTestId("header-search-input");
    fireEvent.change(input, { target: { value: "orange" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /search/i }));
    });

    await waitFor(() => {
      const loc = screen.getByTestId("location-display").textContent ?? "";
      expect(loc).toBe("/search?q=orange");
    });
  });

  it("whitespace-only input does not navigate", async () => {
    renderHeaderSearchBar();

    const input = screen.getByTestId("header-search-input");
    fireEvent.change(input, { target: { value: "   " } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    // Location should remain at "/"
    expect(screen.getByTestId("location-display").textContent).toBe("/");
  });

  it("empty input does not navigate", async () => {
    renderHeaderSearchBar();

    await act(async () => {
      fireEvent.keyDown(screen.getByTestId("header-search-input"), {
        key: "Enter",
      });
    });

    expect(screen.getByTestId("location-display").textContent).toBe("/");
  });
});

// ── 5. i18n catalog parity ────────────────────────────────────────────────────

import enSearch from "../i18n/locales/en/search.json";
import zhSearch from "../i18n/locales/zh/search.json";

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

describe("i18n catalog parity — search namespace", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("en and zh search namespace have identical keys", () => {
    const enKeys = collectKeys(enSearch).sort();
    const zhKeys = collectKeys(zhSearch).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(missingInZh, "Keys in en/search missing from zh/search").toEqual(
      [],
    );
    expect(
      extraInZh,
      "Extra keys in zh/search not in en/search",
    ).toEqual([]);
  });

  it("search.title resolves in both en and zh", async () => {
    expect(i18n.t("title", { ns: "search" })).toBe(enSearch.title);

    await i18n.changeLanguage("zh");
    const zhVal = i18n.t("title", { ns: "search" });
    expect(zhVal).not.toBe(enSearch.title);
    expect(zhVal.trim().length).toBeGreaterThan(0);
  });
});
