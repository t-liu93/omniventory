/**
 * M1.5 Step 6 — LanguageSwitcher tests.
 *
 * Covers:
 * 1. Live switch re-renders a pre-login surface (Login) into zh when toggled.
 * 2. Live switch re-renders an authed surface (AppShell) into zh when toggled.
 * 3. <html lang> follows after switch.
 * 4. Authed switch calls PATCH /api/auth/me with the chosen language.
 * 5. Gate applies me.preferred_language='zh' on load (switches + writes localStorage).
 * 6. Gate leaves language alone when me.preferred_language is null.
 * 7. Pre-login switch writes localStorage only (no PATCH call).
 * 8. PATCH failure is non-fatal: local language change still applies.
 * 9. Catalog key-parity remains green (nav namespace — the only one changed in step 6).
 *
 * Style: "mock the typed client" approach from existing test files.
 * Test env is pinned to 'en' by setup.ts beforeEach.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import i18n from "../i18n";

// ── Mock the typed client ────────────────────────────────────────────────────

vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
    PATCH: vi.fn(),
  },
}));

import { client } from "../api/client.js";
import { Login } from "../pages/Login.js";
import App from "../App.js";

// ── Helpers ──────────────────────────────────────────────────────────────────

function renderLogin(onSuccess = vi.fn()) {
  return render(
    <MantineProvider>
      <Login onSuccess={onSuccess} />
    </MantineProvider>,
  );
}

function renderApp() {
  return render(
    <MantineProvider>
      <App />
    </MantineProvider>,
  );
}

// ── 1. Pre-login switch re-renders Login into zh ─────────────────────────────

describe("LanguageSwitcher — pre-login mode (Login page)", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.removeItem("omniventory_lang");
  });

  it("shows EN and 中文 buttons on the Login page", () => {
    renderLogin();
    expect(screen.getByRole("button", { name: "EN" })).toBeDefined();
    expect(screen.getByRole("button", { name: "中文" })).toBeDefined();
  });

  it("switching to zh re-renders Login with Chinese copy", async () => {
    renderLogin();

    // Confirm English is active first
    expect(screen.getByRole("button", { name: /sign in/i })).toBeDefined();

    // Click the 中文 button
    fireEvent.click(screen.getByRole("button", { name: "中文" }));

    // Login submit button should now show Chinese text
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "登录" })).toBeDefined();
    });
  });

  it("<html lang> updates to 'zh' after switching on Login", async () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    await waitFor(() => {
      expect(document.documentElement.lang).toBe("zh");
    });
  });

  it("switching back to EN re-renders Login with English copy", async () => {
    renderLogin();

    // Switch to zh first
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "登录" })).toBeDefined();
    });

    // Switch back to EN
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /sign in/i })).toBeDefined();
    });
  });
});

// ── 2. Pre-login switch writes localStorage only (no PATCH) ─────────────────

describe("LanguageSwitcher — pre-login persists to localStorage only", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.removeItem("omniventory_lang");
  });

  it("writes 'zh' to localStorage after switching on Login", async () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    await waitFor(() => {
      expect(localStorage.getItem("omniventory_lang")).toBe("zh");
    });
  });

  it("does NOT call PATCH /api/auth/me when switching language on pre-login page", async () => {
    const patchSpy = vi.mocked(client.PATCH);
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "中文" }));
    // Wait for any async work to complete
    await waitFor(() => {
      expect(document.documentElement.lang).toBe("zh");
    });
    expect(patchSpy).not.toHaveBeenCalled();
  });
});

// ── 3. Authed mode: switch calls PATCH /api/auth/me ─────────────────────────

describe("LanguageSwitcher — authed mode (AppShell)", () => {
  beforeEach(() => {
    // Successful setup-status + me (authenticated)
    vi.mocked(client.GET).mockImplementation((path) => {
      if (path === "/api/auth/setup-status") {
        return Promise.resolve({
          data: { setup_required: false },
          response: new Response(null, { status: 200 }),
        });
      }
      // /api/auth/me
      return Promise.resolve({
        data: {
          user: {
            id: 1,
            email: "admin@example.com",
            role: "admin",
            is_active: true,
            created_at: "2025-01-01T00:00:00Z",
            preferred_language: null,
            notify_in_app: true,
            notify_email_digest: true,
          },
        },
        response: new Response(null, { status: 200 }),
      });
    });
    vi.mocked(client.POST).mockResolvedValue({
      data: { message: "Logged out" },
      response: new Response(null, { status: 200 }),
    });
    vi.mocked(client.PATCH).mockResolvedValue({
      data: {
        user: {
          id: 1,
          email: "admin@example.com",
          role: "admin",
          is_active: true,
          created_at: "2025-01-01T00:00:00Z",
          preferred_language: "zh",
          notify_in_app: true,
          notify_email_digest: true,
        },
      },
      response: new Response(null, { status: 200 }),
    });
  });

  afterEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.removeItem("omniventory_lang");
    vi.clearAllMocks();
  });

  it("renders the language switcher in the authed header (EN and 中文 buttons)", async () => {
    renderApp();
    // Wait for the shell to mount
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });
    expect(screen.getByRole("button", { name: "EN" })).toBeDefined();
    expect(screen.getByRole("button", { name: "中文" })).toBeDefined();
  });

  it("switching to zh in authed mode calls PATCH /api/auth/me with preferred_language:'zh'", async () => {
    renderApp();
    // Wait for authed shell
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });

    // Click 中文
    fireEvent.click(screen.getByRole("button", { name: "中文" }));

    await waitFor(() => {
      expect(vi.mocked(client.PATCH)).toHaveBeenCalledWith(
        "/api/auth/me",
        expect.objectContaining({
          body: { preferred_language: "zh" },
        }),
      );
    });
  });

  it("switching to zh in authed mode re-renders the shell with Chinese copy", async () => {
    renderApp();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });

    fireEvent.click(screen.getByRole("button", { name: "中文" }));

    // The nav should show Chinese labels
    await waitFor(() => {
      // "退出登录" is the zh translation of "Logout"
      expect(screen.getByRole("button", { name: "退出登录" })).toBeDefined();
    });
  });

  it("<html lang> updates to 'zh' after authed switch", async () => {
    renderApp();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });

    fireEvent.click(screen.getByRole("button", { name: "中文" }));

    await waitFor(() => {
      expect(document.documentElement.lang).toBe("zh");
    });
  });
});

// ── 4. PATCH failure is non-fatal ────────────────────────────────────────────

describe("LanguageSwitcher — authed mode PATCH failure is non-fatal", () => {
  beforeEach(() => {
    vi.mocked(client.GET).mockImplementation((path) => {
      if (path === "/api/auth/setup-status") {
        return Promise.resolve({
          data: { setup_required: false },
          response: new Response(null, { status: 200 }),
        });
      }
      return Promise.resolve({
        data: {
          user: {
            id: 1,
            email: "admin@example.com",
            role: "admin",
            is_active: true,
            created_at: "2025-01-01T00:00:00Z",
            preferred_language: null,
            notify_in_app: true,
            notify_email_digest: true,
          },
        },
        response: new Response(null, { status: 200 }),
      });
    });
    vi.mocked(client.POST).mockResolvedValue({
      data: { message: "Logged out" },
      response: new Response(null, { status: 200 }),
    });
    // PATCH fails
    vi.mocked(client.PATCH).mockResolvedValue({
      error: {
        code: "internal.error",
        message: "Internal server error",
      },
      response: new Response(null, { status: 500 }),
    });
  });

  afterEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.removeItem("omniventory_lang");
    vi.clearAllMocks();
  });

  it("local language change still applies even when PATCH fails", async () => {
    renderApp();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });

    fireEvent.click(screen.getByRole("button", { name: "中文" }));

    // The UI should switch to Chinese despite PATCH failure
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "退出登录" })).toBeDefined();
    });
    expect(document.documentElement.lang).toBe("zh");
  });

  it("does not crash when PATCH fails", async () => {
    renderApp();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });

    // Should not throw
    expect(() => {
      fireEvent.click(screen.getByRole("button", { name: "中文" }));
    }).not.toThrow();

    // Wait for async work to settle
    await waitFor(() => {
      expect(document.documentElement.lang).toBe("zh");
    });
  });
});

// ── 5. Gate applies me.preferred_language='zh' on load ──────────────────────

describe("App gate — applies me.preferred_language on load", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.removeItem("omniventory_lang");
    vi.clearAllMocks();
  });

  it("switches to 'zh' and writes localStorage when me.preferred_language='zh'", async () => {
    vi.mocked(client.GET).mockImplementation((path) => {
      if (path === "/api/auth/setup-status") {
        return Promise.resolve({
          data: { setup_required: false },
          response: new Response(null, { status: 200 }),
        });
      }
      // me returns preferred_language: 'zh'
      return Promise.resolve({
        data: {
          user: {
            id: 1,
            email: "admin@example.com",
            role: "admin",
            is_active: true,
            created_at: "2025-01-01T00:00:00Z",
            preferred_language: "zh",
            notify_in_app: true,
            notify_email_digest: true,
          },
        },
        response: new Response(null, { status: 200 }),
      });
    });
    vi.mocked(client.POST).mockResolvedValue({
      data: { message: "Logged out" },
      response: new Response(null, { status: 200 }),
    });

    renderApp();

    // After me resolves, the app should be in zh
    await waitFor(() => {
      // The shell renders in zh when preferred_language is zh
      expect(screen.getByRole("button", { name: "退出登录" })).toBeDefined();
    });
    // localStorage should be updated
    expect(localStorage.getItem("omniventory_lang")).toBe("zh");
    // <html lang> should be zh
    expect(document.documentElement.lang).toBe("zh");
  });

  it("leaves language at detector's choice when me.preferred_language is null", async () => {
    // Start in en (the test env default from setup.ts).
    // Note: setup.ts beforeEach calls i18n.changeLanguage('en') which may write
    // 'en' to localStorage via the caches:['localStorage'] detector config.
    // The gate contract is: when preferred_language is null, it does NOT call
    // i18n.changeLanguage — so the language stays whatever the detector resolved.
    vi.mocked(client.GET).mockImplementation((path) => {
      if (path === "/api/auth/setup-status") {
        return Promise.resolve({
          data: { setup_required: false },
          response: new Response(null, { status: 200 }),
        });
      }
      return Promise.resolve({
        data: {
          user: {
            id: 1,
            email: "admin@example.com",
            role: "admin",
            is_active: true,
            created_at: "2025-01-01T00:00:00Z",
            preferred_language: null,
            notify_in_app: true,
            notify_email_digest: true,
          },
        },
        response: new Response(null, { status: 200 }),
      });
    });
    vi.mocked(client.POST).mockResolvedValue({
      data: { message: "Logged out" },
      response: new Response(null, { status: 200 }),
    });

    // Spy on localStorage.setItem to assert the gate does NOT write anything.
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    renderApp();

    // Shell renders in English (detector default)
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /logout/i })).toBeDefined();
    });
    // Language is still en (not forced to anything)
    expect(i18n.language).toBe("en");
    // Gate must NOT have written 'omniventory_lang' because preferred_language is null.
    expect(
      setItemSpy.mock.calls.some(([key]) => key === "omniventory_lang"),
    ).toBe(false);
    setItemSpy.mockRestore();
  });
});

// ── 6. Catalog key-parity still green (nav namespace) ───────────────────────

describe("Catalog key-parity — nav namespace after step 6 additions", () => {
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

  it("en/nav.json and zh/nav.json have identical key sets", async () => {
    const enNav = (await import("../i18n/locales/en/nav.json")).default;
    const zhNav = (await import("../i18n/locales/zh/nav.json")).default;

    const enKeys = collectKeys(enNav).sort();
    const zhKeys = collectKeys(zhNav).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(missingInZh, "Keys in en/nav missing from zh/nav").toEqual([]);
    expect(extraInZh, "Extra keys in zh/nav not present in en/nav").toEqual([]);
  });

  it("nav.changeLanguage is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    expect(i18n.t("changeLanguage", { ns: "nav" })).toBe("切换语言");
    await i18n.changeLanguage("en");
  });
});
