/**
 * Login page tests.
 *
 * Covers:
 * 1. Login renders the sign-in form.
 * 2. Happy path: successful POST → calls onSuccess callback.
 * 3. Error path: failed POST (401) → shows error message, does NOT call onSuccess.
 *
 * We mock the typed API client module directly (not fetch) because openapi-fetch
 * captures globalThis.fetch at createClient() time, so stubbing fetch after the
 * module loads has no effect.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { Login } from "../pages/Login.js";

/** Mock the typed client module. */
vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
  },
}));

import { client } from "../api/client.js";

/** Wrap in MantineProvider so Mantine components can read the theme. */
function renderLogin(onSuccess = vi.fn()) {
  return render(
    <MantineProvider>
      <Login onSuccess={onSuccess} />
    </MantineProvider>,
  );
}

/** Helper: fill in the login form and click submit. */
async function submitForm(email: string, password: string) {
  fireEvent.change(screen.getByLabelText(/email/i), {
    target: { value: email },
  });
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: password },
  });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("Login — renders", () => {
  it("shows the sign-in form with email, password, and submit button", () => {
    renderLogin();
    expect(screen.getByLabelText(/email/i)).toBeDefined();
    expect(screen.getByLabelText(/password/i)).toBeDefined();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeDefined();
  });
});

describe("Login — happy path", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      data: {
        id: 1,
        email: "admin@example.com",
        role: "admin",
        is_active: true,
        created_at: "2025-01-01T00:00:00Z",
      },
      response: new Response(null, { status: 200 }),
    });
  });

  it("calls onSuccess after a successful POST /api/auth/login", async () => {
    const onSuccess = vi.fn();
    renderLogin(onSuccess);
    await submitForm("admin@example.com", "secret");
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });
});

describe("Login — error path", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      error: {
        code: "auth.invalid_credentials",
        message: "Invalid credentials.",
      },
      response: new Response(null, { status: 401 }),
    });
  });

  it("shows a localized error message and does not call onSuccess on 401", async () => {
    const onSuccess = vi.fn();
    renderLogin(onSuccess);
    await submitForm("admin@example.com", "wrong");
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeDefined();
    });
    // The localized EN message for auth.invalid_credentials
    expect(screen.getByText("Invalid email or password.")).toBeDefined();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
