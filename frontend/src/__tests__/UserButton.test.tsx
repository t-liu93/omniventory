/**
 * UserButton tests.
 *
 * Covers:
 * 1. emailToInitial — derives the correct Avatar initial from various email shapes.
 * 2. UserButton renders avatar initial, email, and opens a menu on click.
 * 3. UserButton menu contains logout item that calls the onLogout callback.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";
import { emailToInitial } from "../components/emailToInitial.js";
import { UserButton } from "../components/UserButton.js";

/** Mock the typed client module (LanguageSwitcher imports it). */
vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
    PATCH: vi.fn(),
  },
}));

import { client } from "../api/client.js";

function renderUserButton(email: string, onLogout = vi.fn()) {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <UserButton email={email} onLogout={onLogout} />
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── 1. emailToInitial ─────────────────────────────────────────────────────────

describe("emailToInitial", () => {
  it("returns uppercase first letter of local part for standard email", () => {
    expect(emailToInitial("admin@example.com")).toBe("A");
  });

  it("returns uppercase for lowercase first letter", () => {
    expect(emailToInitial("john.doe@company.org")).toBe("J");
  });

  it("returns '?' for empty string", () => {
    expect(emailToInitial("")).toBe("?");
  });

  it("returns '?' for email with empty local part", () => {
    expect(emailToInitial("@example.com")).toBe("?");
  });

  it("handles email with no @ sign (entire string is local part)", () => {
    expect(emailToInitial("noemail")).toBe("N");
  });

  it("handles numeric first character", () => {
    expect(emailToInitial("1user@example.com")).toBe("1");
  });
});

// ── 2. UserButton renders correctly ──────────────────────────────────────────

describe("UserButton — rendering", () => {
  beforeEach(() => {
    vi.mocked(client.PATCH).mockResolvedValue({
      data: { user: { id: 1, email: "admin@example.com", role: "admin", is_active: true, created_at: "2025-01-01T00:00:00Z" } },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("renders the user menu button with correct aria-label", () => {
    renderUserButton("admin@example.com");
    expect(screen.getByRole("button", { name: /user menu/i })).toBeDefined();
  });

  it("displays the email in the button", () => {
    renderUserButton("admin@example.com");
    expect(screen.getByText("admin@example.com")).toBeDefined();
  });

  it("displays the derived avatar initial", () => {
    renderUserButton("admin@example.com");
    expect(screen.getByText("A")).toBeDefined();
  });
});

// ── 3. UserButton menu — logout ───────────────────────────────────────────────

describe("UserButton — logout menu item", () => {
  beforeEach(() => {
    vi.mocked(client.PATCH).mockResolvedValue({
      data: { user: { id: 1, email: "admin@example.com", role: "admin", is_active: true, created_at: "2025-01-01T00:00:00Z" } },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("clicking the user menu button opens the menu with logout item", async () => {
    renderUserButton("admin@example.com");

    fireEvent.click(screen.getByRole("button", { name: /user menu/i }));

    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: /logout/i })).toBeDefined();
    });
  });

  it("clicking logout in the menu calls onLogout", async () => {
    const onLogout = vi.fn();
    renderUserButton("admin@example.com", onLogout);

    fireEvent.click(screen.getByRole("button", { name: /user menu/i }));

    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: /logout/i })).toBeDefined();
    });

    fireEvent.click(screen.getByRole("menuitem", { name: /logout/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
});
