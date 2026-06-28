/**
 * M6 Step 9 — Users admin page + invitations UI tests.
 *
 * Coverage (per §10 Step 9 blind-review checkpoints):
 *  1. List renders — GET /api/users rows shown (email/role/active).
 *  2. Role change — changing the role Select calls PATCH /api/users/{id} with {role}.
 *  3. Activate/deactivate — clicking the toggle calls PATCH with {is_active}.
 *  4. Delete + last-admin surfaced — DELETE called; 409 user.last_admin → localized error shown.
 *  5. Invite — POST /api/invitations; accept_url displayed with copy control + emailed status.
 *  6. email_exists — 409 user.email_exists → invite error surfaced.
 *  7. Revoke — DELETE /api/invitations/{id} called and list refreshes.
 *  8. Reset password — POST /api/users/{id}/reset-password → reset_url shown.
 *  9. Nav gating — Users nav item shown for admin; absent for member.
 * 10. Route gating — /users redirects non-admin to /.
 * (Fixup) F1. Copy buttons degrade gracefully when navigator.clipboard is unavailable.
 * (Fixup) F2. Delete success toast uses delete.success string, not the button label.
 * (Fixup) F3. Table stays rendered during post-mutation refetch (no full-page spinner).
 *
 * All tests are pinned to 'en' (M1.5 convention).
 * Client is mocked; AuthProvider is seeded with admin role.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import * as notifyModule from "../components/notify";

// i18n must be initialized before any component that calls useTranslation().
import "../i18n/index.js";

import { AuthProvider } from "../auth/AuthContext";
import { RequirePermission } from "../auth/RequirePermission";
import { NavContent_testable } from "../shell/AppShell";
import { Users } from "../pages/Users";
import type { components } from "../api/schema";

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

// ── Fixtures ──────────────────────────────────────────────────────────────────

type UserResponse = components["schemas"]["UserResponse"];
type UserSummary = components["schemas"]["UserSummary"];

function makeUser(role: "admin" | "member" | "viewer"): UserResponse {
  return {
    id: 1,
    email: `${role}@example.com`,
    role,
    is_active: true,
    notify_in_app: true,
    notify_email_digest: true,
    created_at: "2025-01-01T00:00:00Z",
    preferred_language: "en",
  };
}

const adminUser = makeUser("admin");
const memberUser = makeUser("member");

const userSummaryAdmin: UserSummary = {
  id: 1,
  email: "admin@example.com",
  role: "admin",
  is_active: true,
};

const userSummaryMember: UserSummary = {
  id: 2,
  email: "member@example.com",
  role: "member",
  is_active: true,
};

const pendingInvite = {
  id: 10,
  email: "newuser@example.com",
  role: "member",
  expires_at: "2026-07-04T12:00:00Z",
  created_at: "2026-06-27T12:00:00Z",
};

/** Wrap children in admin AuthProvider + MantineProvider + MemoryRouter. */
function withAdminAuth(children: React.ReactNode) {
  return (
    <AuthProvider
      user={adminUser}
      onRefresh={vi.fn()}
      onLogout={vi.fn()}
    >
      {children}
    </AuthProvider>
  );
}

/** Standard mock: GET /api/users returns two summaries, GET /api/invitations returns empty. */
function mockStandardLoad() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/users") {
      return { data: [userSummaryAdmin, userSummaryMember], error: undefined, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/invitations") {
      return { data: [], error: undefined, response: new Response(null, { status: 200 }) };
    }
    return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
  });
}

/** Render the Users page as admin. */
function renderUsersPage() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        {withAdminAuth(<Users />)}
      </MantineProvider>
    </MemoryRouter>,
  );
}

// Mock navigator.clipboard (jsdom doesn't have it by default).
Object.defineProperty(navigator, "clipboard", {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  writable: true,
  configurable: true,
});

// ── 1. List renders ────────────────────────────────────────────────────────────

describe("Users page — list renders", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  it("shows user email in the table", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByText("admin@example.com")).toBeDefined();
    });
    expect(screen.getByText("member@example.com")).toBeDefined();
  });

  it("shows a row for each user with the correct testid", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("user-row-1")).toBeDefined();
      expect(screen.getByTestId("user-row-2")).toBeDefined();
    });
  });

  it("shows a role Select for each user", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("role-select-1")).toBeDefined();
      expect(screen.getByTestId("role-select-2")).toBeDefined();
    });
  });

  it("shows an active toggle for each user", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("active-toggle-1")).toBeDefined();
      expect(screen.getByTestId("active-toggle-2")).toBeDefined();
    });
  });
});

// ── 2. Role change ─────────────────────────────────────────────────────────────

describe("Users page — role change", () => {
  beforeEach(() => {
    mockStandardLoad();
    vi.mocked(client.PATCH).mockResolvedValue({ data: undefined, error: undefined, response: new Response(null, { status: 200 }) } as AnyResult);
  });

  it("calls PATCH /api/users/{user_id} with {role} when role Select changes", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("role-select-2")).toBeDefined();
    });

    // NativeSelect renders a native <select>; fireEvent.change on it triggers onChange.
    const nativeSelect = screen.getByTestId("role-select-2");
    fireEvent.change(nativeSelect, { target: { value: "viewer" } });

    await waitFor(() => {
      expect(vi.mocked(client.PATCH)).toHaveBeenCalledWith(
        "/api/users/{user_id}",
        expect.objectContaining({
          params: { path: { user_id: 2 } },
          body: { role: "viewer" },
        }),
      );
    });
  });
});

// ── 3. Activate/deactivate toggle ─────────────────────────────────────────────

describe("Users page — active toggle", () => {
  beforeEach(() => {
    mockStandardLoad();
    vi.mocked(client.PATCH).mockResolvedValue({ data: undefined, error: undefined, response: new Response(null, { status: 200 }) } as AnyResult);
  });

  it("calls PATCH /api/users/{user_id} with {is_active: false} when deactivating an active user", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("active-toggle-2")).toBeDefined();
    });

    // member user (id=2) is active; clicking the toggle deactivates them
    fireEvent.click(screen.getByTestId("active-toggle-2"));

    await waitFor(() => {
      expect(vi.mocked(client.PATCH)).toHaveBeenCalledWith(
        "/api/users/{user_id}",
        expect.objectContaining({
          params: { path: { user_id: 2 } },
          body: { is_active: false },
        }),
      );
    });
  });
});

// ── 4. Delete + last-admin surfaced ───────────────────────────────────────────

describe("Users page — delete + last-admin error", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  it("calls DELETE /api/users/{user_id} on confirm", async () => {
    vi.mocked(client.DELETE).mockResolvedValue({ data: undefined, error: undefined, response: new Response(null, { status: 204 }) } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("delete-btn-2")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("delete-btn-2"));

    // Wait for delete confirm modal
    await waitFor(() => {
      expect(screen.getByTestId("delete-confirm-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("delete-confirm-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.DELETE)).toHaveBeenCalledWith(
        "/api/users/{user_id}",
        expect.objectContaining({
          params: { path: { user_id: 2 } },
        }),
      );
    });
  });

  it("shows localized user.last_admin error when deleting the last admin", async () => {
    vi.mocked(client.DELETE).mockResolvedValue({
      data: undefined,
      error: { code: "user.last_admin", message: "Last admin" },
      response: new Response(null, { status: 409 }),
    } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("delete-btn-1")).toBeDefined();
    });

    // Try to delete the admin user (id=1)
    fireEvent.click(screen.getByTestId("delete-btn-1"));
    await waitFor(() => {
      expect(screen.getByTestId("delete-confirm-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("delete-confirm-btn"));

    // The localized error should appear in the modal
    await waitFor(() => {
      expect(screen.getByTestId("delete-error")).toBeDefined();
    });
    // Verify the localized message contains the key content
    const errorEl = screen.getByTestId("delete-error");
    expect(errorEl.textContent).toContain("Cannot remove the last active admin");
  });
});

// ── 5. Invite — success: accept_url + copy + emailed ─────────────────────────

describe("Users page — invite success", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  it("POSTs invite and shows accept_url with copy and emailed status", async () => {
    const inviteResponse = {
      id: 99,
      email: "newuser@example.com",
      role: "member",
      expires_at: "2026-07-04T12:00:00Z",
      accept_url: "http://localhost/invite/accept?token=abc123",
      emailed: true,
    };
    vi.mocked(client.POST).mockResolvedValue({ data: inviteResponse, error: undefined, response: new Response(null, { status: 201 }) } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("invite-btn")).toBeDefined();
    });

    // Open invite modal
    fireEvent.click(screen.getByTestId("invite-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("invite-email-input")).toBeDefined();
    });

    // Fill in email
    fireEvent.change(screen.getByTestId("invite-email-input"), {
      target: { value: "newuser@example.com" },
    });

    // Submit
    fireEvent.click(screen.getByTestId("invite-submit-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/invitations",
        expect.objectContaining({
          body: expect.objectContaining({ email: "newuser@example.com" }),
        }),
      );
    });

    // Result should appear
    await waitFor(() => {
      expect(screen.getByTestId("invite-accept-url")).toBeDefined();
    });
    expect(screen.getByTestId("invite-accept-url").textContent).toContain("abc123");

    // Copy button present
    expect(screen.getByTestId("invite-copy-btn")).toBeDefined();

    // emailed = true → email-sent indicator
    expect(screen.getByTestId("invite-emailed")).toBeDefined();
  });

  it("shows invite-not-emailed when emailed is false", async () => {
    const inviteResponse = {
      id: 99,
      email: "newuser@example.com",
      role: "member",
      expires_at: "2026-07-04T12:00:00Z",
      accept_url: "http://localhost/invite/accept?token=abc123",
      emailed: false,
    };
    vi.mocked(client.POST).mockResolvedValue({ data: inviteResponse, error: undefined, response: new Response(null, { status: 201 }) } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("invite-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("invite-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("invite-email-input")).toBeDefined();
    });
    fireEvent.change(screen.getByTestId("invite-email-input"), {
      target: { value: "newuser@example.com" },
    });
    fireEvent.click(screen.getByTestId("invite-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("invite-not-emailed")).toBeDefined();
    });
  });
});

// ── 6. user.email_exists surfaced in invite modal ─────────────────────────────

describe("Users page — invite user.email_exists", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  it("shows invite-email-exists-error when server returns 409 user.email_exists", async () => {
    vi.mocked(client.POST).mockResolvedValue({
      data: undefined,
      error: { code: "user.email_exists", message: "Email exists" },
      response: new Response(null, { status: 409 }),
    } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("invite-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("invite-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("invite-submit-btn")).toBeDefined();
    });
    fireEvent.change(screen.getByTestId("invite-email-input"), {
      target: { value: "existing@example.com" },
    });
    fireEvent.click(screen.getByTestId("invite-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("invite-email-exists-error")).toBeDefined();
    });
    // Verify the localized message
    const errorEl = screen.getByTestId("invite-email-exists-error");
    expect(errorEl.textContent).toContain("A user with this email address already exists");
  });
});

// ── 7. Revoke pending invitation ──────────────────────────────────────────────

describe("Users page — revoke invitation", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/users") {
        return { data: [userSummaryAdmin, userSummaryMember], error: undefined, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/invitations") {
        return { data: [pendingInvite], error: undefined, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    });
    vi.mocked(client.DELETE).mockResolvedValue({ data: undefined, error: undefined, response: new Response(null, { status: 204 }) } as AnyResult);
  });

  it("shows pending invitation row", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("pending-inv-row-10")).toBeDefined();
    });
    expect(screen.getByText("newuser@example.com")).toBeDefined();
  });

  it("calls DELETE /api/invitations/{invite_id} when revoking", async () => {
    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("revoke-inv-10")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("revoke-inv-10"));

    await waitFor(() => {
      expect(vi.mocked(client.DELETE)).toHaveBeenCalledWith(
        "/api/invitations/{invite_id}",
        expect.objectContaining({
          params: { path: { invite_id: 10 } },
        }),
      );
    });
  });

  it("refreshes the invitation list after revoke", async () => {
    // After the DELETE, GET /api/invitations returns empty
    let callCount = 0;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(client.GET).mockImplementation(async (path: any) => {
      if (path === "/api/users") {
        return { data: [userSummaryAdmin, userSummaryMember], error: undefined, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/invitations") {
        callCount += 1;
        if (callCount === 1) {
          return { data: [pendingInvite], error: undefined, response: new Response(null, { status: 200 }) };
        }
        return { data: [], error: undefined, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { code: "http.404", message: "Not found" }, response: new Response(null, { status: 404 }) };
    });

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("revoke-inv-10")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("revoke-inv-10"));

    // After revoke + refresh, the row should disappear
    await waitFor(() => {
      expect(screen.queryByTestId("pending-inv-row-10")).toBeNull();
    });
  });
});

// ── 8. Reset password ─────────────────────────────────────────────────────────

describe("Users page — reset password", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  it("calls POST /api/users/{user_id}/reset-password and shows reset_url", async () => {
    const resetResponse = {
      reset_url: "http://localhost/password-reset/accept?token=reset123",
      emailed: false,
    };
    vi.mocked(client.POST).mockResolvedValue({ data: resetResponse, error: undefined, response: new Response(null, { status: 200 }) } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("reset-btn-1")).toBeDefined();
    });

    // Open reset password modal
    fireEvent.click(screen.getByTestId("reset-btn-1"));

    // Wait for the modal's "Generate link" button
    await waitFor(() => {
      expect(screen.getByTestId("reset-issue-btn")).toBeDefined();
    });

    // Click the button to trigger the POST
    fireEvent.click(screen.getByTestId("reset-issue-btn"));

    await waitFor(() => {
      expect(vi.mocked(client.POST)).toHaveBeenCalledWith(
        "/api/users/{user_id}/reset-password",
        expect.objectContaining({
          params: { path: { user_id: 1 } },
        }),
      );
    });

    // reset_url appears in the modal
    await waitFor(() => {
      expect(screen.getByTestId("reset-url-display")).toBeDefined();
    });
    expect(screen.getByTestId("reset-url-display").textContent).toContain("reset123");

    // Copy button is present
    expect(screen.getByTestId("reset-copy-btn")).toBeDefined();
  });

  it("shows reset-emailed when emailed is true", async () => {
    const resetResponse = {
      reset_url: "http://localhost/password-reset/accept?token=x",
      emailed: true,
    };
    vi.mocked(client.POST).mockResolvedValue({ data: resetResponse, error: undefined, response: new Response(null, { status: 200 }) } as AnyResult);

    renderUsersPage();
    await waitFor(() => {
      expect(screen.getByTestId("reset-btn-1")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("reset-btn-1"));
    await waitFor(() => {
      expect(screen.getByTestId("reset-issue-btn")).toBeDefined();
    });
    fireEvent.click(screen.getByTestId("reset-issue-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("reset-emailed")).toBeDefined();
    });
  });
});

// ── 9. Nav gating ─────────────────────────────────────────────────────────────

describe("Nav — Users item gated by MANAGE_USERS", () => {
  function renderNav(userObj: UserResponse) {
    return render(
      <MemoryRouter initialEntries={["/"]}>
        <MantineProvider>
          <AuthProvider user={userObj} onRefresh={vi.fn()} onLogout={vi.fn()}>
            <NavContent_testable />
          </AuthProvider>
        </MantineProvider>
      </MemoryRouter>,
    );
  }

  it("admin sees Users in the nav", async () => {
    renderNav(adminUser);
    await waitFor(() => {
      expect(screen.getByText("Users")).toBeDefined();
    });
  });

  it("member does NOT see Users in the nav", async () => {
    renderNav(memberUser);
    await waitFor(() => {
      // Dashboard is always present — confirms the nav has rendered
      expect(screen.getByText("Dashboard")).toBeDefined();
    });
    expect(screen.queryByText("Users")).toBeNull();
  });
});

// ── 10. Route gating ──────────────────────────────────────────────────────────

describe("Route gating — /users redirects non-admins to /", () => {
  // Set up a minimal mock so the Users page doesn't crash when admin accesses it.
  beforeEach(() => {
    mockStandardLoad();
  });

  function renderRoute(userObj: UserResponse) {
    return render(
      <MemoryRouter initialEntries={["/users"]}>
        <MantineProvider>
          <AuthProvider user={userObj} onRefresh={vi.fn()} onLogout={vi.fn()}>
            <Routes>
              <Route path="/" element={<div data-testid="dashboard-page">Dashboard</div>} />
              <Route
                path="/users"
                element={
                  <RequirePermission permission="MANAGE_USERS">
                    <Users />
                  </RequirePermission>
                }
              />
            </Routes>
          </AuthProvider>
        </MantineProvider>
      </MemoryRouter>,
    );
  }

  it("admin can access /users", async () => {
    renderRoute(adminUser);
    // The Users page loads (invite button appears)
    await waitFor(() => {
      expect(screen.getByTestId("invite-btn")).toBeDefined();
    });
    expect(screen.queryByTestId("dashboard-page")).toBeNull();
  });

  it("member is redirected from /users to /", async () => {
    renderRoute(memberUser);
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-page")).toBeDefined();
    });
    expect(screen.queryByTestId("invite-btn")).toBeNull();
  });
});

// ── F1. Copy buttons degrade gracefully without navigator.clipboard ────────────

describe("Copy buttons — degrade gracefully when clipboard is unavailable", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  afterEach(() => {
    // Restore clipboard mock that the module-level defineProperty set up.
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      writable: true,
      configurable: true,
    });
  });

  it("invite-copy-btn does not throw and link text is visible when clipboard is undefined", async () => {
    const inviteResponse = {
      id: 99,
      email: "newuser@example.com",
      role: "member",
      expires_at: "2026-07-04T12:00:00Z",
      accept_url: "http://localhost/invite/accept?token=abc999",
      emailed: false,
    };
    vi.mocked(client.POST).mockResolvedValue({
      data: inviteResponse,
      error: undefined,
      response: new Response(null, { status: 201 }),
    } as AnyResult);

    // Remove the clipboard API to simulate a non-secure (plain HTTP) context.
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    renderUsersPage();
    await waitFor(() => expect(screen.getByTestId("invite-btn")).toBeDefined());

    fireEvent.click(screen.getByTestId("invite-btn"));
    await waitFor(() => expect(screen.getByTestId("invite-email-input")).toBeDefined());
    fireEvent.change(screen.getByTestId("invite-email-input"), {
      target: { value: "newuser@example.com" },
    });
    fireEvent.click(screen.getByTestId("invite-submit-btn"));

    await waitFor(() => expect(screen.getByTestId("invite-copy-btn")).toBeDefined());

    // Clicking must not throw even though clipboard is unavailable.
    expect(() => {
      fireEvent.click(screen.getByTestId("invite-copy-btn"));
    }).not.toThrow();

    // The link text remains visible/selectable in the DOM.
    expect(screen.getByTestId("invite-accept-url").textContent).toContain("abc999");
  });

  it("reset-copy-btn does not throw and link text is visible when clipboard is undefined", async () => {
    const resetResponse = {
      reset_url: "http://localhost/password-reset/accept?token=rst888",
      emailed: false,
    };
    vi.mocked(client.POST).mockResolvedValue({
      data: resetResponse,
      error: undefined,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    // Remove the clipboard API.
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    renderUsersPage();
    await waitFor(() => expect(screen.getByTestId("reset-btn-1")).toBeDefined());

    fireEvent.click(screen.getByTestId("reset-btn-1"));
    await waitFor(() => expect(screen.getByTestId("reset-issue-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("reset-issue-btn"));

    await waitFor(() => expect(screen.getByTestId("reset-copy-btn")).toBeDefined());

    // Clicking must not throw even though clipboard is unavailable.
    expect(() => {
      fireEvent.click(screen.getByTestId("reset-copy-btn"));
    }).not.toThrow();

    // The link text remains visible/selectable in the DOM.
    expect(screen.getByTestId("reset-url-display").textContent).toContain("rst888");
  });
});

// ── F2. Delete success toast uses delete.success string ───────────────────────

describe("Delete success — toast uses delete.success string", () => {
  beforeEach(() => {
    mockStandardLoad();
  });

  it("notifySuccess is called with the localized 'User deleted' message, not the button label", async () => {
    vi.mocked(client.DELETE).mockResolvedValue({
      data: undefined,
      error: undefined,
      response: new Response(null, { status: 204 }),
    } as AnyResult);

    const notifySuccessSpy = vi.spyOn(notifyModule, "notifySuccess");

    renderUsersPage();
    await waitFor(() => expect(screen.getByTestId("delete-btn-2")).toBeDefined());

    fireEvent.click(screen.getByTestId("delete-btn-2"));
    await waitFor(() => expect(screen.getByTestId("delete-confirm-btn")).toBeDefined());
    fireEvent.click(screen.getByTestId("delete-confirm-btn"));

    await waitFor(() => {
      expect(notifySuccessSpy).toHaveBeenCalled();
    });

    const callArg = notifySuccessSpy.mock.calls[0][0];
    // Must be the success message, not the button label "Delete"
    expect(callArg).toBe("User deleted");
    expect(callArg).not.toBe("Delete");

    notifySuccessSpy.mockRestore();
  });
});

// ── F3. Table stays rendered during post-mutation refetch ─────────────────────

describe("Full-page spinner — only on initial load, not on refetch", () => {
  it("table rows remain in DOM while a post-mutation GET /api/users is in flight", async () => {
    // Control when the second GET /api/users resolves so we can inspect the DOM
    // while the refetch is still pending.
    let getCallCount = 0;
    let resolveRefetch!: (value: unknown) => void;
    const refetchPending = new Promise<unknown>((resolve) => {
      resolveRefetch = resolve;
    });

    vi.mocked(client.GET).mockImplementation(async (path: unknown) => {
      if (path === "/api/users") {
        getCallCount++;
        if (getCallCount === 1) {
          // Initial load resolves immediately.
          return {
            data: [userSummaryAdmin, userSummaryMember],
            error: undefined,
            response: new Response(null, { status: 200 }),
          };
        }
        // Refetch hangs until we resolve it below.
        return refetchPending as Promise<{ data: UserSummary[]; error: undefined; response: Response }>;
      }
      if (path === "/api/invitations") {
        return { data: [], error: undefined, response: new Response(null, { status: 200 }) };
      }
      return {
        data: null,
        error: { code: "http.404", message: "Not found" },
        response: new Response(null, { status: 404 }),
      };
    });

    vi.mocked(client.PATCH).mockResolvedValue({
      data: undefined,
      error: undefined,
      response: new Response(null, { status: 200 }),
    } as AnyResult);

    renderUsersPage();

    // Wait for initial load: table must be present.
    await waitFor(() => {
      expect(screen.getByTestId("user-row-1")).toBeDefined();
    });

    // Trigger a mutation that causes a refetch (role change).
    fireEvent.change(screen.getByTestId("role-select-2"), { target: { value: "viewer" } });

    // Wait for PATCH to be called (the mutation completed).
    await waitFor(() => {
      expect(vi.mocked(client.PATCH)).toHaveBeenCalled();
    });

    // At this point the refetch GET is in-flight but pending.
    // The table must still be rendered (no full-page spinner replacing it).
    expect(screen.getByTestId("user-row-1")).toBeDefined();
    expect(screen.getByTestId("user-row-2")).toBeDefined();

    // Clean up: resolve the pending refetch so React state settles.
    resolveRefetch({
      data: [userSummaryAdmin, userSummaryMember],
      error: undefined,
      response: new Response(null, { status: 200 }),
    });
  });
});
