/**
 * Routing tests.
 *
 * Verifies that react-router-dom is correctly mounted inside the authenticated
 * AppShell, and that navigating to /locations shows the Locations page and
 * /categories shows the Categories page.
 *
 * We use MemoryRouter (with initialEntries) to control the URL in tests, and
 * mock both the typed API client and the TreeBrowser component so that routing
 * tests stay focused on route matching — not on tree data loading.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { Routes, Route } from "react-router-dom";
import { AppShell } from "../shell/AppShell.js";
import { Dashboard } from "../pages/Dashboard.js";
import { Locations } from "../pages/Locations.js";
import { Categories } from "../pages/Categories.js";

/** Mock the typed client module. */
vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
    PATCH: vi.fn(),
    DELETE: vi.fn(),
  },
}));

/** Mock TreeBrowser so routing tests don't depend on API calls for tree data. */
vi.mock("../components/TreeBrowser.js", () => ({
  TreeBrowser: ({ resource }: { resource: string }) => (
    <div data-testid={`tree-browser-${resource}`}>
      {resource === "locations" ? "Locations Tree" : "Categories Tree"}
    </div>
  ),
}));

import { client } from "../api/client.js";

function renderAtPath(path: string) {
  const onLogout = vi.fn();
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MantineProvider>
        <AppShell onLogout={onLogout}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/locations" element={<Locations />} />
            <Route path="/categories" element={<Categories />} />
          </Routes>
        </AppShell>
      </MantineProvider>
    </MemoryRouter>,
  );
}

describe("Routing — route matching", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      data: { message: "Logged out" },
      response: new Response(null, { status: 200 }),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("renders Dashboard at /", async () => {
    renderAtPath("/");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /dashboard/i })).toBeDefined();
    });
  });

  it("renders Locations page at /locations", async () => {
    renderAtPath("/locations");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /locations/i })).toBeDefined();
      expect(screen.getByTestId("tree-browser-locations")).toBeDefined();
    });
  });

  it("renders Categories page at /categories", async () => {
    renderAtPath("/categories");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /categories/i })).toBeDefined();
      expect(screen.getByTestId("tree-browser-categories")).toBeDefined();
    });
  });

  it("does NOT render Locations tree at /", async () => {
    renderAtPath("/");
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /dashboard/i })).toBeDefined();
    });
    expect(screen.queryByTestId("tree-browser-locations")).toBeNull();
  });

  it("does NOT render Categories tree at /locations", async () => {
    renderAtPath("/locations");
    await waitFor(() => {
      expect(screen.getByTestId("tree-browser-locations")).toBeDefined();
    });
    expect(screen.queryByTestId("tree-browser-categories")).toBeNull();
  });
});

describe("Routing — nav links in shell", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      data: { message: "Logged out" },
      response: new Response(null, { status: 200 }),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("shows Dashboard, Locations, and Categories nav links", async () => {
    renderAtPath("/");
    await waitFor(() => {
      expect(screen.getAllByText("Dashboard").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Locations").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Categories").length).toBeGreaterThan(0);
    });
  });

  it("shows the logout button in the header", async () => {
    renderAtPath("/");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });
  });
});
