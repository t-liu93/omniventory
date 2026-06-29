/**
 * M7 Step 8 — Configuration additions + notification rendering tests.
 *
 * Coverage (per M7.md §5 "Frontend" / §7.4/§7.5 / §9 Step 8 / §10 Step 8):
 *
 * 1. Configuration — loads: maintenance_lead_days shown in Reminders input.
 * 2. Configuration — loads: auto_add_low_stock shown in Shopping list switch.
 * 3. Configuration — PATCH reminders includes maintenance_lead_days.
 * 4. Configuration — PATCH shopping_list sends { shopping_list: { auto_add_low_stock } }.
 * 5. NotificationBell — renders reminder.maintenance (normal, days_remaining >= 0).
 * 6. NotificationBell — renders reminder.maintenance_overdue (days_remaining < 0).
 * 7. /notifications page — renders reminder.maintenance (normal).
 * 8. /notifications page — renders reminder.maintenance_overdue (overdue).
 * 9. i18n — en+zh key parity for notifications namespace (incl. new maintenance keys).
 * 10. i18n — en+zh key parity for configuration namespace (incl. new shoppingList keys).
 * 11. i18n — reminder.maintenance_overdue and reminder.maintenance present in both locales.
 *
 * Conventions: vitest + Testing Library, mock typed client, pinned to "en".
 * No backend changes; no codegen changes (contract already includes both fields).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Configuration } from "../pages/Configuration.js";
import { NotificationBell } from "../components/NotificationBell.js";
import { Notifications } from "../pages/Notifications.js";
import i18n from "../i18n/index.js";

// ── Mock client ───────────────────────────────────────────────────────────────

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

// ── i18n catalog imports for parity tests ─────────────────────────────────────

import enNotifications from "../i18n/locales/en/notifications.json";
import zhNotifications from "../i18n/locales/zh/notifications.json";
import enConfiguration from "../i18n/locales/en/configuration.json";
import zhConfiguration from "../i18n/locales/zh/configuration.json";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** Full SettingsResponse mock with the new fields. */
const MOCK_SETTINGS = {
  reminders: {
    best_before_lead_days: 7,
    warranty_lead_days: 30,
    maintenance_lead_days: 7,
    low_stock_repeat_days: [1, 3, 7],
    scan_time: "08:00",
  },
  shopping_list: {
    auto_add_low_stock: true,
  },
  channels: {
    email: {
      enabled: false,
      host: null,
      port: null,
      username: null,
      password_is_set: false,
      encryption: "none",
      from_address: null,
      from_name: null,
    },
    http: {
      enabled: false,
      webhook_url: null,
      auth_header_is_set: false,
      integration_token_is_set: false,
    },
    mqtt: {
      enabled: false,
      host: null,
      port: null,
      username: null,
      password_is_set: false,
      topic_prefix: null,
      use_tls: false,
      discovery_enabled: false,
      commands_enabled: false,
    },
  },
};

/** A maintenance reminder — normal (7 days remaining). */
const notifMaintenanceNormal = {
  id: 10,
  source: "maintenance",
  subject_type: "maintenance_schedule",
  subject_id: 5,
  message_code: "reminder.maintenance",
  params: {
    name: "Replace AC filter",
    instance_name: "Samsung AC",
    next_due_date: "2026-07-06",
    days_remaining: 7,
    location_id: 1,
    instance_id: 42,
  },
  offset_days: null,
  created_at: "2026-06-29T08:00:00Z",
  read_at: null,
};

/** A maintenance reminder — overdue (3 days overdue, days_remaining = -3). */
const notifMaintenanceOverdue = {
  id: 11,
  source: "maintenance",
  subject_type: "maintenance_schedule",
  subject_id: 6,
  message_code: "reminder.maintenance",
  params: {
    name: "Oil change",
    instance_name: "Family car",
    next_due_date: "2026-06-26",
    days_remaining: -3,
    location_id: 2,
    instance_id: 43,
  },
  offset_days: null,
  created_at: "2026-06-29T08:00:00Z",
  read_at: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderConfiguration() {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <Configuration />
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderBell() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <MantineProvider>
        <NotificationBell />
      </MantineProvider>
    </MemoryRouter>,
  );
}

function renderNotificationsPage() {
  return render(
    <MemoryRouter initialEntries={["/notifications"]}>
      <MantineProvider>
        <Routes>
          <Route path="/notifications" element={<Notifications />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

/** Deep key extraction (mirrors i18n-catalog.test.ts). */
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

// ── Tests: Configuration — load ───────────────────────────────────────────────

describe("Configuration — loads new settings fields", () => {
  it("shows maintenance_lead_days value from settings in the reminders input", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      const input = screen.getByTestId("reminders-maintenance-lead-input");
      expect(input).toBeDefined();
      // The input value should reflect the loaded setting (7).
      // Mantine NumberInput with a suffix may render the value including the suffix text.
      const rawValue = input.getAttribute("value") ?? "";
      expect(rawValue).toContain("7");
    });
  });

  it("shows auto_add_low_stock value from settings in the shopping-list switch", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      const sw = screen.getByTestId("shopping-list-auto-add-switch");
      expect(sw).toBeDefined();
      // Switch should be checked (auto_add_low_stock = true in fixture)
      expect((sw as HTMLInputElement).checked).toBe(true);
    });
  });

  it("shows Shopping list section heading", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      // The shopping list section title should be rendered from i18n
      expect(screen.getByText("Shopping list")).toBeDefined();
    });
  });
});

// ── Tests: Configuration — save reminders (incl. maintenance_lead_days) ──────

describe("Configuration — PATCH reminders with maintenance_lead_days", () => {
  it("includes maintenance_lead_days in the reminders PATCH body", async () => {
    const patchCalls: AnyResult[] = [];

    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchCalls.push(opts?.body ?? {});
      return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) } as AnyResult;
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("reminders-maintenance-lead-input")).toBeDefined();
    });

    // Click Save reminder settings
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-reminders-btn"));
    });

    await waitFor(() => {
      expect(patchCalls.length).toBeGreaterThanOrEqual(1);
    });

    // The first PATCH call body should have reminders.maintenance_lead_days
    const body = patchCalls[0] as Record<string, unknown>;
    expect(body).toHaveProperty("reminders");
    const reminders = body["reminders"] as Record<string, unknown>;
    expect(reminders).toHaveProperty("maintenance_lead_days", 7);
  });

  it("sends updated maintenance_lead_days value after user changes the input", async () => {
    const patchCalls: AnyResult[] = [];

    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchCalls.push(opts?.body ?? {});
      return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) } as AnyResult;
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("reminders-maintenance-lead-input")).toBeDefined();
    });

    // Change the maintenance lead days input to 14
    await act(async () => {
      fireEvent.change(screen.getByTestId("reminders-maintenance-lead-input"), {
        target: { value: "14" },
      });
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-reminders-btn"));
    });

    await waitFor(() => {
      expect(patchCalls.length).toBeGreaterThanOrEqual(1);
    });

    const body = patchCalls[0] as Record<string, unknown>;
    const reminders = body["reminders"] as Record<string, unknown>;
    // Should reflect the new value (14)
    expect(reminders).toHaveProperty("maintenance_lead_days", 14);
  });
});

// ── Tests: Configuration — save shopping list (auto_add_low_stock) ────────────

describe("Configuration — PATCH shopping_list with auto_add_low_stock", () => {
  it("sends { shopping_list: { auto_add_low_stock: true } } when switch is on", async () => {
    const patchBodies: AnyResult[] = [];

    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchBodies.push(opts?.body ?? {});
      return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) } as AnyResult;
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("save-shopping-list-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-shopping-list-btn"));
    });

    await waitFor(() => {
      expect(patchBodies.length).toBeGreaterThanOrEqual(1);
    });

    // Find the shopping-list PATCH (body with shopping_list key)
    const shoppingListPatch = patchBodies.find(
      (b) => typeof b === "object" && b !== null && "shopping_list" in (b as object),
    ) as Record<string, unknown> | undefined;
    expect(shoppingListPatch).toBeDefined();
    const sl = (shoppingListPatch!["shopping_list"] as Record<string, unknown>);
    expect(sl).toHaveProperty("auto_add_low_stock", true);
  });

  it("sends auto_add_low_stock: false after user toggles the switch off", async () => {
    const patchBodies: AnyResult[] = [];

    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchBodies.push(opts?.body ?? {});
      return { data: MOCK_SETTINGS, response: new Response(null, { status: 200 }) } as AnyResult;
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("shopping-list-auto-add-switch")).toBeDefined();
    });

    // Toggle switch off (currently true → false)
    await act(async () => {
      fireEvent.click(screen.getByTestId("shopping-list-auto-add-switch"));
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-shopping-list-btn"));
    });

    await waitFor(() => {
      expect(patchBodies.length).toBeGreaterThanOrEqual(1);
    });

    const shoppingListPatch = patchBodies.find(
      (b) => typeof b === "object" && b !== null && "shopping_list" in (b as object),
    ) as Record<string, unknown> | undefined;
    expect(shoppingListPatch).toBeDefined();
    const sl = (shoppingListPatch!["shopping_list"] as Record<string, unknown>);
    expect(sl).toHaveProperty("auto_add_low_stock", false);
  });
});

// ── Tests: NotificationBell — reminder.maintenance rendering ─────────────────

describe("NotificationBell — renders reminder.maintenance", () => {
  it("renders normal maintenance reminder (days_remaining >= 0) with localized text", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications/unread-count") {
        return { data: { count: 1 }, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/notifications") {
        return { data: [notifMaintenanceNormal], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderBell();
    });

    // Open the dropdown
    await act(async () => {
      fireEvent.click(screen.getByTestId("notification-bell-btn"));
    });

    await waitFor(() => {
      const msg = screen.getByTestId("notification-message-10");
      // Must contain the item name and instance name from params
      expect(msg.textContent).toContain("Replace AC filter");
      expect(msg.textContent).toContain("Samsung AC");
      // Should NOT be the raw message code
      expect(msg.textContent).not.toBe("reminder.maintenance");
      // Normal variant: should contain "days remaining", NOT "overdue"
      expect(msg.textContent).toContain("days remaining");
      expect(msg.textContent).not.toContain("overdue");
    });
  });

  it("renders overdue maintenance reminder (days_remaining < 0) with overdue text", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications/unread-count") {
        return { data: { count: 1 }, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/notifications") {
        return { data: [notifMaintenanceOverdue], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderBell();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("notification-bell-btn"));
    });

    await waitFor(() => {
      const msg = screen.getByTestId("notification-message-11");
      // Must contain item and instance name
      expect(msg.textContent).toContain("Oil change");
      expect(msg.textContent).toContain("Family car");
      // Overdue variant: should say "overdue" and show absolute days (3)
      expect(msg.textContent).toContain("overdue");
      expect(msg.textContent).toContain("3");
      // Should NOT say "days remaining"
      expect(msg.textContent).not.toContain("days remaining");
    });
  });
});

// ── Tests: /notifications page — reminder.maintenance rendering ───────────────

describe("/notifications page — renders reminder.maintenance", () => {
  it("renders normal maintenance reminder on the notifications page", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications") {
        return { data: [notifMaintenanceNormal], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    renderNotificationsPage();

    await waitFor(() => {
      const msg = screen.getByTestId("notification-page-message-10");
      expect(msg.textContent).toContain("Replace AC filter");
      expect(msg.textContent).toContain("Samsung AC");
      expect(msg.textContent).toContain("days remaining");
      expect(msg.textContent).not.toContain("overdue");
      expect(msg.textContent).not.toBe("reminder.maintenance");
    });
  });

  it("renders overdue maintenance reminder on the notifications page", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications") {
        return { data: [notifMaintenanceOverdue], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    renderNotificationsPage();

    await waitFor(() => {
      const msg = screen.getByTestId("notification-page-message-11");
      expect(msg.textContent).toContain("Oil change");
      expect(msg.textContent).toContain("Family car");
      expect(msg.textContent).toContain("overdue");
      expect(msg.textContent).toContain("3");
      expect(msg.textContent).not.toContain("days remaining");
    });
  });

  it("maintenance notification subject links to /instances/:id via params.instance_id", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications") {
        return { data: [notifMaintenanceNormal], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    renderNotificationsPage();

    await waitFor(() => {
      const link = screen.getByTestId("notification-subject-link-10");
      expect(link).toBeDefined();
      // Must navigate to the owning instance, not to /items/{scheduleId}
      const href = link.getAttribute("href") ?? "";
      expect(href).toContain("/instances/42");
      expect(href).not.toContain("/items/");
    });
  });

  it("maintenance notification without instance_id falls back to dashboard, not /items/", async () => {
    const legacyNotif = {
      ...notifMaintenanceNormal,
      id: 20,
      params: {
        name: "Old schedule",
        instance_name: "Old device",
        next_due_date: "2026-07-06",
        days_remaining: 7,
        location_id: 1,
        // instance_id intentionally absent (legacy row)
      },
    };

    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications") {
        return { data: [legacyNotif], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    renderNotificationsPage();

    await waitFor(() => {
      const link = screen.getByTestId("notification-subject-link-20");
      expect(link).toBeDefined();
      const href = link.getAttribute("href") ?? "";
      // Legacy fallback: must NOT link to /items/{scheduleId}
      expect(href).not.toContain("/items/");
      // Falls back to the dashboard root
      expect(href).toBe("/");
    });
  });
});

// ── Tests: i18n parity ────────────────────────────────────────────────────────

describe("i18n — notifications namespace en+zh parity (incl. new maintenance keys)", () => {
  it("en and zh notifications have identical key sets", () => {
    const enKeys = collectKeys(enNotifications).sort();
    const zhKeys = collectKeys(zhNotifications).sort();
    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));
    expect(missingInZh, "Keys in en/notifications missing from zh").toEqual([]);
    expect(extraInZh, "Extra keys in zh/notifications not in en").toEqual([]);
  });

  it("en notifications has reminder.maintenance key", () => {
    const enKeys = collectKeys(enNotifications);
    expect(enKeys).toContain("reminder.maintenance");
  });

  it("en notifications has reminder.maintenance_overdue key", () => {
    const enKeys = collectKeys(enNotifications);
    expect(enKeys).toContain("reminder.maintenance_overdue");
  });

  it("zh notifications has reminder.maintenance key", () => {
    const zhKeys = collectKeys(zhNotifications);
    expect(zhKeys).toContain("reminder.maintenance");
  });

  it("zh notifications has reminder.maintenance_overdue key", () => {
    const zhKeys = collectKeys(zhNotifications);
    expect(zhKeys).toContain("reminder.maintenance_overdue");
  });
});

describe("i18n — configuration namespace en+zh parity (incl. new shoppingList keys)", () => {
  it("en and zh configuration have identical key sets", () => {
    const enKeys = collectKeys(enConfiguration).sort();
    const zhKeys = collectKeys(zhConfiguration).sort();
    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));
    expect(missingInZh, "Keys in en/configuration missing from zh").toEqual([]);
    expect(extraInZh, "Extra keys in zh/configuration not in en").toEqual([]);
  });

  it("en configuration has reminders.maintenanceLeadDaysLabel", () => {
    const enKeys = collectKeys(enConfiguration);
    expect(enKeys).toContain("reminders.maintenanceLeadDaysLabel");
  });

  it("en configuration has shoppingList.autoAddLowStockLabel", () => {
    const enKeys = collectKeys(enConfiguration);
    expect(enKeys).toContain("shoppingList.autoAddLowStockLabel");
  });

  it("en configuration has shoppingList.saveShoppingList", () => {
    const enKeys = collectKeys(enConfiguration);
    expect(enKeys).toContain("shoppingList.saveShoppingList");
  });

  it("en configuration has section.shoppingList", () => {
    const enKeys = collectKeys(enConfiguration);
    expect(enKeys).toContain("section.shoppingList");
  });
});
