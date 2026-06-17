/**
 * Dashboard page tests.
 *
 * Verifies that the three concept-overview cards render and are labeled as
 * "coming soon" (no fabricated metrics). Also checks that the durable-goods
 * card includes a link to the existing /items route.
 *
 * The Dashboard is a pure presentation component with no API calls,
 * so no client mock is needed — we render it directly inside a minimal
 * MantineProvider + MemoryRouter.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../pages/Dashboard.js";

function renderDashboard() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <Dashboard />
      </MantineProvider>
    </MemoryRouter>,
  );
}

describe("Dashboard — concept overview cards", () => {
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

  it("renders the Consumable Stock card", () => {
    renderDashboard();
    expect(
      screen.getByRole("heading", { name: /consumable stock/i }),
    ).toBeDefined();
  });

  it("all three cards carry a 'Coming soon' badge (no fake metrics)", () => {
    renderDashboard();
    const badges = screen.getAllByText(/coming soon/i);
    expect(badges.length).toBe(3);
  });

  it("durable-goods card links to the existing /items route", () => {
    renderDashboard();
    const link = screen.getByRole("link", { name: /items/i });
    expect(link).toBeDefined();
    expect((link as HTMLAnchorElement).getAttribute("href")).toBe("/items");
  });
});
