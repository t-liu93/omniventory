/**
 * Setup page tests.
 *
 * Covers:
 * 1. Setup renders the "create admin account" form.
 * 2. Happy path: successful POST /api/auth/setup → calls onSuccess callback.
 * 3. Error path: failed POST (409 conflict) → shows error message.
 *
 * We mock the typed API client module directly (not fetch).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Setup } from "../pages/Setup.js";

/** Mock the typed client module. */
vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
  },
}));

import { client } from "../api/client.js";

/** Wrap in MantineProvider so Mantine components can read the theme. */
function renderSetup(onSuccess = vi.fn()) {
  return render(
    <MantineProvider>
      <Setup onSuccess={onSuccess} />
    </MantineProvider>,
  );
}

/** Helper: fill in the setup form and click submit. */
async function submitForm(email: string, password: string) {
  fireEvent.change(screen.getByLabelText(/email/i), {
    target: { value: email },
  });
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: password },
  });
  fireEvent.click(screen.getByRole("button", { name: /create admin account/i }));
}

describe("Setup — renders", () => {
  it("shows the create-admin form with email, password, and submit button", () => {
    renderSetup();
    expect(screen.getByLabelText(/email/i)).toBeDefined();
    expect(screen.getByLabelText(/password/i)).toBeDefined();
    expect(
      screen.getByRole("button", { name: /create admin account/i }),
    ).toBeDefined();
  });
});

describe("Setup — happy path", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      data: {
        id: 1,
        email: "admin@example.com",
        role: "admin",
        is_active: true,
        created_at: "2026-01-01T00:00:00Z",
      },
      response: new Response(null, { status: 201 }),
    });
  });

  it("calls onSuccess after a successful POST /api/auth/setup", async () => {
    const onSuccess = vi.fn();
    renderSetup(onSuccess);
    await submitForm("admin@example.com", "strongpass!");
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });
});

describe("Setup — error path", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      error: {
        code: "auth.setup_already_complete",
        message: "Setup already complete.",
      },
      response: new Response(null, { status: 409 }),
    });
  });

  it("shows a localized error message and does not call onSuccess on 409", async () => {
    const onSuccess = vi.fn();
    renderSetup(onSuccess);
    await submitForm("intruder@example.com", "intruderpass!");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeDefined();
    });
    // The localized EN message for auth.setup_already_complete
    expect(
      screen.getByText("Setup is already complete. An admin account already exists."),
    ).toBeDefined();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
