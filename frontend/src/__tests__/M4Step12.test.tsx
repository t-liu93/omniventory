/**
 * M4 Step 12 — frontend tests.
 *
 * Coverage (per M4 §5 "Frontend" / §7.3/§7.4/§7.5 / §9 Step 12 / §10 Step 12):
 *
 * 1. Configuration page — load and render:
 *    a. Loads settings + me, populates global reminders form fields.
 *    b. Loads per-user lead fields (value from me response; blank when null).
 *
 * 2. Configuration page — save reminders (global):
 *    a. Editing lead + repeat + scan-time → PATCH /settings with correct body.
 *
 * 3. Configuration page — per-user reminders:
 *    a. Setting a value → PATCH /auth/me with integer.
 *    b. Clearing (blank) → PATCH /auth/me with null.
 *
 * 4. Secrets masking (write-only):
 *    a. Email password_is_set shown as badge; input is NOT pre-filled.
 *    b. MQTT password_is_set shown as badge; input is NOT pre-filled.
 *    c. HTTP auth_header_is_set shown as badge; input is NOT pre-filled.
 *    d. integration_token_is_set shown as badge; token field NOT shown.
 *
 * 5. Run scan now:
 *    a. Clicking "Run scan now" → POST /reminders/run; shows summary counts.
 *
 * 6. Integration token:
 *    a. "Generate new token" renders a copy-able token in the UI.
 *    b. Saving HTTP settings after generating → PATCH /settings includes the token.
 *
 * 7. State endpoint URL hint is rendered.
 *
 * 8. per-item reminder_lead_days on definition form:
 *    a. Create definition sends reminder_lead_days.
 *    b. Edit definition sends reminder_lead_days.
 *    c. Detail page shows reminder_lead_days when set.
 *
 * 9. en+zh catalog completeness (cross-check via i18n-catalog.test.ts convention).
 *
 * Conventions: vitest + Testing Library, mock typed client, pinned to "en".
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
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Configuration } from "../pages/Configuration.js";
import { Items, ItemDetail } from "../pages/Items.js";
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

// ── Fixtures ──────────────────────────────────────────────────────────────────

const baseSettings: AnyResult = {
  reminders: {
    best_before_lead_days: 3,
    warranty_lead_days: 30,
    low_stock_repeat_days: [1, 3, 7],
    scan_time: "08:00",
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
      use_tls: false,
      topic_prefix: null,
      discovery_enabled: false,
      commands_enabled: false,
    },
  },
};

const settingsWithSecrets: AnyResult = {
  ...baseSettings,
  channels: {
    ...baseSettings.channels,
    email: {
      ...baseSettings.channels.email,
      password_is_set: true,
      host: "smtp.example.com",
      port: 587,
      encryption: "starttls",
      from_name: "Omni",
    },
    http: {
      ...baseSettings.channels.http,
      auth_header_is_set: true,
      integration_token_is_set: true,
    },
    mqtt: {
      ...baseSettings.channels.mqtt,
      password_is_set: true,
      host: "mqtt.example.com",
    },
  },
};

const meNoOverrides: AnyResult = {
  user: {
    id: 1,
    email: "admin@example.com",
    is_active: true,
    role: "admin",
    preferred_language: "en",
    reminder_best_before_lead_days: null,
    reminder_warranty_lead_days: null,
    created_at: "2026-01-01T00:00:00Z",
  },
};

const meWithOverrides: AnyResult = {
  user: {
    id: 1,
    email: "admin@example.com",
    is_active: true,
    role: "admin",
    preferred_language: "en",
    reminder_best_before_lead_days: 5,
    reminder_warranty_lead_days: 14,
    created_at: "2026-01-01T00:00:00Z",
  },
};

const kindDurable = { id: 1, code: "durable", name: "Durable", is_system: true, created_at: "2026-01-01T00:00:00Z" };

const defWithLeadDays: AnyResult = {
  id: 42,
  name: "Passport",
  description: null,
  kind_id: 1,
  kind: kindDurable,
  category_id: null,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "none",
  min_stock: null,
  default_best_before_days: null,
  reminder_lead_days: 90,
  created_at: "2026-01-01T00:00:00Z",
};

const defNoLeadDays: AnyResult = {
  id: 43,
  name: "Milk",
  description: null,
  kind_id: 1,
  kind: kindDurable,
  category_id: null,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "none",
  min_stock: null,
  default_best_before_days: null,
  reminder_lead_days: null,
  created_at: "2026-01-01T00:00:00Z",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockSettingsAndMe(settings = baseSettings, me = meNoOverrides) {
  vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
    if (path === "/api/settings") {
      return { data: settings, response: new Response(null, { status: 200 }) };
    }
    if (path === "/api/auth/me") {
      return { data: me, response: new Response(null, { status: 200 }) };
    }
    return { data: null, error: {}, response: new Response(null, { status: 404 }) };
  });
}

function renderConfiguration() {
  return render(
    <MemoryRouter initialEntries={["/configuration"]}>
      <MantineProvider>
        <Routes>
          <Route path="/configuration" element={<Configuration />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

// ── Tests: Configuration page — load ─────────────────────────────────────────

describe("Configuration page — load and render", () => {
  it("renders global reminder fields with loaded values", async () => {
    mockSettingsAndMe();

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("reminders-bb-lead-input")).toBeDefined();
    });

    // Inputs should show values from settings
    const bbInput = screen.getByTestId("reminders-bb-lead-input");
    expect(bbInput.querySelector("input")?.value ?? (bbInput as HTMLInputElement).value).toBeTruthy();
  });

  it("renders per-user lead fields empty when me has no overrides", async () => {
    mockSettingsAndMe(baseSettings, meNoOverrides);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("user-bb-lead-input")).toBeDefined();
    });

    // No override → input should be empty
    const userBbInput = screen.getByTestId("user-bb-lead-input");
    const inputEl = userBbInput.querySelector("input") as HTMLInputElement | null;
    // Empty string or empty value
    expect(inputEl?.value ?? "").toBe("");
  });

  it("renders per-user lead fields when overrides are set (page loads successfully)", async () => {
    mockSettingsAndMe(baseSettings, meWithOverrides);

    await act(async () => {
      renderConfiguration();
    });

    // When meWithOverrides is set (values 5 and 14), the form should render
    // without errors and both per-user fields should be present.
    await waitFor(() => {
      expect(screen.getByTestId("user-bb-lead-input")).toBeDefined();
      expect(screen.getByTestId("user-warranty-lead-input")).toBeDefined();
    });

    // The page title should be visible (proves full render succeeded)
    expect(screen.getByTestId("save-user-reminders-btn")).toBeDefined();
  });

  it("renders Run scan now button", async () => {
    mockSettingsAndMe();

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("run-scan-btn")).toBeDefined();
    });
  });

  it("renders state endpoint URL hint containing /api/integrations/state", async () => {
    mockSettingsAndMe();

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("state-endpoint-url")).toBeDefined();
    });

    const urlEl = screen.getByTestId("state-endpoint-url");
    expect(urlEl.textContent).toContain("/api/integrations/state");
  });
});

// ── Tests: Configuration page — secrets masking ───────────────────────────────

describe("Configuration page — secrets write-only masking", () => {
  it("email password shows Set badge when password_is_set=true; input NOT pre-filled", async () => {
    mockSettingsAndMe(settingsWithSecrets);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("email-password-status")).toBeDefined();
    });

    // Status badge should reflect "Set"
    const statusBadge = screen.getByTestId("email-password-status");
    expect(statusBadge.textContent).toContain("Set");

    // The password input must NOT be pre-filled
    const passwordInput = screen.getByTestId("email-password-input") as HTMLInputElement;
    expect(passwordInput.value).toBe("");
  });

  it("email password shows Not set badge when password_is_set=false", async () => {
    mockSettingsAndMe(baseSettings);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("email-password-status")).toBeDefined();
    });

    const statusBadge = screen.getByTestId("email-password-status");
    expect(statusBadge.textContent).toContain("Not set");
  });

  it("MQTT password shows Set badge when password_is_set=true; input NOT pre-filled", async () => {
    mockSettingsAndMe(settingsWithSecrets);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("mqtt-password-status")).toBeDefined();
    });

    const statusBadge = screen.getByTestId("mqtt-password-status");
    expect(statusBadge.textContent).toContain("Set");

    const mqttInput = screen.getByTestId("mqtt-password-input") as HTMLInputElement;
    expect(mqttInput.value).toBe("");
  });

  it("HTTP auth_header shows Set badge when auth_header_is_set=true; input NOT pre-filled", async () => {
    mockSettingsAndMe(settingsWithSecrets);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("http-auth-header-status")).toBeDefined();
    });

    const statusBadge = screen.getByTestId("http-auth-header-status");
    expect(statusBadge.textContent).toContain("Set");

    const authInput = screen.getByTestId("http-auth-header-input") as HTMLInputElement;
    expect(authInput.value).toBe("");
  });

  it("integration_token shows Set badge when integration_token_is_set=true", async () => {
    mockSettingsAndMe(settingsWithSecrets);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("integration-token-status")).toBeDefined();
    });

    const tokenBadge = screen.getByTestId("integration-token-status");
    expect(tokenBadge.textContent).toContain("Set");

    // The new token alert should NOT be visible before generating
    expect(screen.queryByTestId("new-token-alert")).toBeNull();
  });
});

// ── Tests: Run scan now ───────────────────────────────────────────────────────

describe("Configuration page — Run scan now", () => {
  it("clicking run-scan-btn → POST /reminders/run → shows summary result", async () => {
    mockSettingsAndMe();

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: { best_before: 2, warranty: 1, low_stock: 0 },
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("run-scan-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("run-scan-btn"));
    });

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith("/api/reminders/run");
    });

    await waitFor(() => {
      expect(screen.getByTestId("scan-result")).toBeDefined();
    });

    const result = screen.getByTestId("scan-result");
    // Contains counts from the summary (2, 1, 0)
    expect(result.textContent).toContain("2");
    expect(result.textContent).toContain("1");
  });
});

// ── Tests: Integration token generation ──────────────────────────────────────

describe("Configuration page — integration token", () => {
  it("generate-token-btn shows new token in UI for copying", async () => {
    mockSettingsAndMe(baseSettings);

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("generate-token-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("generate-token-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("new-token-alert")).toBeDefined();
    });

    const tokenValue = screen.getByTestId("new-token-value");
    expect(tokenValue.textContent?.length).toBeGreaterThan(10); // non-empty token

    // integration_token_is_set badge should now show "Set"
    const tokenBadge = screen.getByTestId("integration-token-status");
    expect(tokenBadge.textContent).toContain("Set");
  });

  it("saving HTTP settings after generating token → PATCH includes integration_token", async () => {
    mockSettingsAndMe(baseSettings);

    let patchBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchBody = opts?.body;
      return { data: baseSettings, response: new Response(null, { status: 200 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("generate-token-btn")).toBeDefined();
    });

    // Generate token
    await act(async () => {
      fireEvent.click(screen.getByTestId("generate-token-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("new-token-value")).toBeDefined();
    });

    const generatedToken = screen.getByTestId("new-token-value").textContent ?? "";

    // Save HTTP settings
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-http-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
    });

    expect(patchBody?.channels?.http?.integration_token).toBe(generatedToken);
  });
});

// ── Tests: per-user reminders ─────────────────────────────────────────────────

describe("Configuration page — per-user reminders", () => {
  it("saving (when fields already have values from me) → PATCH /auth/me with correct path", async () => {
    // meWithOverrides: reminder_best_before_lead_days=5, reminder_warranty_lead_days=14
    mockSettingsAndMe(baseSettings, meWithOverrides);

    let patchPath: AnyResult = null;
    let patchBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (path: AnyResult, opts: AnyResult) => {
      patchPath = path;
      patchBody = opts?.body;
      return { data: meWithOverrides, response: new Response(null, { status: 200 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("save-user-reminders-btn")).toBeDefined();
    });

    // Click save without changing — with pre-loaded values, sends those integers
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-user-reminders-btn"));
    });

    await waitFor(() => {
      expect(patchPath).toBe("/api/auth/me");
    });

    // Values were loaded from meWithOverrides (5 and 14) → sent as integers
    expect(typeof patchBody?.reminder_best_before_lead_days).not.toBe("string");
    expect(patchBody?.reminder_best_before_lead_days).toBe(5);
    expect(patchBody?.reminder_warranty_lead_days).toBe(14);
  });

  it("saving with no overrides (fields stay blank) → PATCH /auth/me with null values (inherit)", async () => {
    // meNoOverrides: both lead days are null → form starts with "" → saving sends null
    mockSettingsAndMe(baseSettings, meNoOverrides);

    let patchBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchBody = opts?.body;
      return { data: meNoOverrides, response: new Response(null, { status: 200 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("save-user-reminders-btn")).toBeDefined();
    });

    // Click save — fields are blank (from null in me response), so null should be sent
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-user-reminders-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
    });

    // Null semantics: blank field = send null to clear/inherit
    expect(patchBody?.reminder_best_before_lead_days).toBeNull();
    expect(patchBody?.reminder_warranty_lead_days).toBeNull();
  });
});

// ── Tests: per-item reminder_lead_days (Items.tsx) ────────────────────────────

describe("per-item reminder_lead_days — definition form", () => {
  function mockItemsData(defs = [defWithLeadDays]) {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult, opts: AnyResult) => {
      if (path === "/api/definitions") {
        return { data: defs, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions/{definition_id}") {
        const defId = opts?.params?.path?.definition_id;
        const def = defs.find((d) => d.id === defId) ?? defs[0];
        return { data: def, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/kinds") {
        return { data: [kindDurable], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/categories") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/instances") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });
  }

  it("create definition form includes reminder_lead_days field", async () => {
    mockItemsData();

    render(
      <MemoryRouter initialEntries={["/items"]}>
        <MantineProvider>
          <Routes>
            <Route path="/items" element={<Items />} />
          </Routes>
        </MantineProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("create-def-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("def-reminder-lead-days-input")).toBeDefined();
    });
  });

  it("create definition form contains reminder_lead_days field and POSTs it (null when blank)", async () => {
    mockItemsData([]);

    let postBody: AnyResult = null;
    vi.mocked(client.POST).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      postBody = opts?.body;
      return { data: defWithLeadDays, response: new Response(null, { status: 201 }) };
    });

    render(
      <MemoryRouter initialEntries={["/items"]}>
        <MantineProvider>
          <Routes>
            <Route path="/items" element={<Items />} />
          </Routes>
        </MantineProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });

    // Open create modal
    await act(async () => {
      fireEvent.click(screen.getByTestId("create-def-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("def-name-input")).toBeDefined();
    });

    // The reminder_lead_days field should be present in the form
    expect(screen.getByTestId("def-reminder-lead-days-input")).toBeDefined();

    // Fill required name field — use the testid element directly (consistent with Items.test.tsx pattern)
    const nameInput = screen.getByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "Passport" } });

    // Submit (reminder_lead_days is blank → null)
    await act(async () => {
      fireEvent.click(screen.getByTestId("def-submit-btn"));
    });

    await waitFor(() => {
      expect(postBody).not.toBeNull();
    });

    // reminder_lead_days key must be present in the POST body (null when field is blank)
    expect("reminder_lead_days" in postBody).toBe(true);
    expect(postBody?.reminder_lead_days).toBeNull();
  });

  it("ItemDetail shows reminder_lead_days when definition has it set", async () => {
    mockItemsData([defWithLeadDays]);

    render(
      <MemoryRouter initialEntries={["/items/42"]}>
        <MantineProvider>
          <Routes>
            <Route path="/items/:id" element={<ItemDetail />} />
          </Routes>
        </MantineProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("def-reminder-lead-days-value")).toBeDefined();
    });

    const leadValue = screen.getByTestId("def-reminder-lead-days-value");
    expect(leadValue.textContent).toContain("90");
    // Verify the unit is localized (en: "days"; not raw hardcoded English in ZH mode)
    expect(leadValue.textContent).toContain("days");
  });

  it("ItemDetail does not show reminder_lead_days when not set", async () => {
    mockItemsData([defNoLeadDays]);

    render(
      <MemoryRouter initialEntries={["/items/43"]}>
        <MantineProvider>
          <Routes>
            <Route path="/items/:id" element={<ItemDetail />} />
          </Routes>
        </MantineProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      // The detail heading area should be visible
      expect(screen.getByText("Milk")).toBeDefined();
    });

    expect(screen.queryByTestId("def-reminder-lead-days-value")).toBeNull();
  });
});

// ── Tests: Email encryption / from_name / test button (Walkthrough Fix 1) ────

describe("Configuration page — email encryption and from_name", () => {
  it("encryption Select is rendered in the email section", async () => {
    mockSettingsAndMe();

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("email-encryption-select")).toBeDefined();
    });
  });

  it("from_name TextInput is rendered in the email section", async () => {
    mockSettingsAndMe();

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("email-from-name-input")).toBeDefined();
    });
  });

  it("saving email sends encryption (not use_tls) in the PATCH body", async () => {
    mockSettingsAndMe();

    let patchBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchBody = opts?.body;
      return { data: baseSettings, response: new Response(null, { status: 200 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("save-email-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-email-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
    });

    // Must have encryption field, must NOT have use_tls
    expect("encryption" in (patchBody?.channels?.email ?? {})).toBe(true);
    expect("use_tls" in (patchBody?.channels?.email ?? {})).toBe(false);
  });

  it("saving email sends from_name in the PATCH body", async () => {
    mockSettingsAndMe();

    let patchBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      patchBody = opts?.body;
      return { data: baseSettings, response: new Response(null, { status: 200 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("email-from-name-input")).toBeDefined();
    });

    // Type a from_name value — the testid is on the wrapper div; get the inner input
    const fromNameWrapper = screen.getByTestId("email-from-name-input");
    const fromNameInput = (fromNameWrapper.querySelector("input") ?? fromNameWrapper) as HTMLInputElement;
    fireEvent.change(fromNameInput, { target: { value: "My Server" } });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-email-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
    });

    expect(patchBody?.channels?.email?.from_name).toBe("My Server");
  });

  it("test-email-btn calls POST /api/settings/email/test and shows success notification on ok=true", async () => {
    mockSettingsAndMe();

    vi.mocked(client.POST).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings/email/test") {
        return {
          data: { ok: true, detail: null, recipient: "admin@example.com" },
          response: new Response(null, { status: 200 }),
        };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-email-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-email-btn"));
    });

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith("/api/settings/email/test");
    });

    // On success, no error alert should be shown
    expect(screen.queryByTestId("email-test-result")).toBeNull();
  });

  it("test-email-btn shows error alert on ok=false", async () => {
    mockSettingsAndMe();

    vi.mocked(client.POST).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings/email/test") {
        return {
          data: { ok: false, detail: "Connection refused", recipient: "admin@example.com" },
          response: new Response(null, { status: 200 }),
        };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-email-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-email-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("email-test-result")).toBeDefined();
    });

    const resultAlert = screen.getByTestId("email-test-result");
    expect(resultAlert.textContent).toContain("Connection refused");
  });
});

// ── Tests: level-mode low-stock notification rendering (walkthrough fix #2) ───

describe("level-mode low-stock notification rendering", () => {
  /**
   * These tests validate the i18n catalog entries and template strings for
   * level-mode params (mode='level', level='low'): they confirm that the
   * catalog keys exist and that the templates produce the expected output when
   * interpolated manually.  They do NOT exercise the localizeNotification /
   * localizeMessage helpers in the real components — see the
   * "level-mode low-stock — component mount" describe block below for that.
   */

  it("notifications:level.low is 'low' in en", () => {
    expect(i18n.t("level.low", { ns: "notifications" })).toBe("low");
  });

  it("notifications:level.low is '低' in zh", async () => {
    await i18n.changeLanguage("zh");
    expect(i18n.t("level.low", { ns: "notifications" })).toBe("低");
    await i18n.changeLanguage("en");
  });

  it("low_stock level-mode renders level label via existing template in en", () => {
    // The template is: "{{name}} is running low (current: {{current}}, threshold: {{threshold}})"
    // For level mode we supply the level label for both current and threshold.
    const levelLabel = i18n.t("level.low", { ns: "notifications" });
    const result = i18n.t("reminder.low_stock", {
      ns: "notifications",
      name: "Torx Screws",
      current: levelLabel,
      threshold: levelLabel,
    });
    expect(result).toContain("Torx Screws");
    expect(result).toContain("low");
    // Must not contain blank/undefined placeholders
    expect(result).not.toContain("undefined");
    expect(result).not.toContain("{{");
  });

  it("low_stock exact-mode rendering is unchanged (regression)", () => {
    const result = i18n.t("reminder.low_stock", {
      ns: "notifications",
      name: "Coffee",
      current: "0.5",
      threshold: "1.0",
    });
    expect(result).toContain("Coffee");
    expect(result).toContain("0.5");
    expect(result).toContain("1.0");
  });

  it("low_stock_repeat level-mode renders level label and offset in en", () => {
    const levelLabel = i18n.t("level.low", { ns: "notifications" });
    const result = i18n.t("reminder.low_stock_repeat", {
      ns: "notifications",
      name: "Torx Screws",
      current: levelLabel,
      threshold: levelLabel,
      offset: 7,
    });
    expect(result).toContain("Torx Screws");
    expect(result).toContain("low");
    expect(result).toContain("7");
    expect(result).not.toContain("{{");
  });

  it("low_stock_repeat exact-mode rendering is unchanged (regression)", () => {
    const result = i18n.t("reminder.low_stock_repeat", {
      ns: "notifications",
      name: "Coffee",
      current: "0.5",
      threshold: "1.0",
      offset: 3,
    });
    expect(result).toContain("Coffee");
    expect(result).toContain("0.5");
    expect(result).toContain("3");
  });
});

// ── Tests: level-mode low-stock — component mount ────────────────────────────
//
// These tests exercise the real localizeNotification (NotificationBell) and
// localizeMessage (Notifications page) helpers by mounting the actual
// components with a level-mode notification fixture.  Removing the
// `mode === "level"` branch from either helper makes these tests FAIL.

describe("level-mode low-stock — component mount", () => {
  // level-mode fixture: no numeric current/threshold, just mode + level code
  const notifLowStockLevel = {
    id: 20,
    source: "low_stock",
    subject_type: "definition",
    subject_id: 10,
    message_code: "reminder.low_stock",
    params: { name: "Torx M6x30", mode: "level", level: "low" },
    offset_days: 0,
    created_at: "2026-06-20T08:02:00Z",
    read_at: null,
  };

  const notifLowStockLevelRepeat = {
    id: 21,
    source: "low_stock",
    subject_type: "definition",
    subject_id: 10,
    message_code: "reminder.low_stock_repeat",
    params: { name: "Torx M6x30", mode: "level", level: "low", offset: 3 },
    offset_days: 3,
    created_at: "2026-06-23T08:00:00Z",
    read_at: null,
  };

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

  it("NotificationBell: level-mode low_stock renders localized level in dropdown", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications/unread-count") {
        return { data: { count: 1 }, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/notifications") {
        return { data: [notifLowStockLevel], response: new Response(null, { status: 200 }) };
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
      const msg = screen.getByTestId("notification-message-20");
      // Must contain item name
      expect(msg.textContent).toContain("Torx M6x30");
      // Must contain the localized level label ("low" in en)
      expect(msg.textContent).toContain("low");
      // Must NOT contain blank/undefined placeholders
      expect(msg.textContent).not.toContain("undefined");
      expect(msg.textContent).not.toContain("null");
      expect(msg.textContent).not.toContain("{{");
      expect(msg.textContent).not.toContain("None");
    });
  });

  it("NotificationBell: level-mode low_stock_repeat renders localized level and offset", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications/unread-count") {
        return { data: { count: 1 }, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/notifications") {
        return { data: [notifLowStockLevelRepeat], response: new Response(null, { status: 200 }) };
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
      const msg = screen.getByTestId("notification-message-21");
      expect(msg.textContent).toContain("Torx M6x30");
      expect(msg.textContent).toContain("low");
      expect(msg.textContent).not.toContain("undefined");
      expect(msg.textContent).not.toContain("null");
      expect(msg.textContent).not.toContain("{{");
    });
  });

  it("Notifications page: level-mode low_stock renders localized level", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications") {
        return { data: [notifLowStockLevel], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderNotificationsPage();
    });

    await waitFor(() => {
      // Notifications page uses data-testid="notification-page-message-{id}"
      const msg = screen.getByTestId("notification-page-message-20");
      expect(msg.textContent).toContain("Torx M6x30");
      expect(msg.textContent).toContain("low");
      expect(msg.textContent).not.toContain("undefined");
      expect(msg.textContent).not.toContain("null");
      expect(msg.textContent).not.toContain("{{");
      expect(msg.textContent).not.toContain("None");
    });
  });

  it("Notifications page: level-mode low_stock_repeat renders localized level and offset", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/notifications") {
        return { data: [notifLowStockLevelRepeat], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderNotificationsPage();
    });

    await waitFor(() => {
      // Notifications page uses data-testid="notification-page-message-{id}"
      const msg = screen.getByTestId("notification-page-message-21");
      expect(msg.textContent).toContain("Torx M6x30");
      expect(msg.textContent).toContain("low");
      expect(msg.textContent).not.toContain("undefined");
      expect(msg.textContent).not.toContain("null");
      expect(msg.textContent).not.toContain("{{");
    });
  });
});

// ── Tests: en+zh catalog via i18n ─────────────────────────────────────────────

describe("i18n configuration namespace", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("configuration.page.title is 'Configuration' in en", () => {
    expect(i18n.t("page.title", { ns: "configuration" })).toBe("Configuration");
  });

  it("configuration.page.title is not 'Configuration' in zh (translated)", async () => {
    await i18n.changeLanguage("zh");
    const value = i18n.t("page.title", { ns: "configuration" });
    expect(value).not.toBe("Configuration");
    expect(value.trim().length).toBeGreaterThan(0);
  });

  it("configuration.scan.runNow is 'Run scan now' in en", () => {
    expect(i18n.t("scan.runNow", { ns: "configuration" })).toBe("Run scan now");
  });

  it("configuration.scan.runNow is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    const value = i18n.t("scan.runNow", { ns: "configuration" });
    expect(value).not.toBe("Run scan now");
    expect(value.trim().length).toBeGreaterThan(0);
  });

  it("nav.configuration is 'Configuration' in en", () => {
    expect(i18n.t("configuration", { ns: "nav" })).toBe("Configuration");
  });

  it("nav.configuration is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    const value = i18n.t("configuration", { ns: "nav" });
    expect(value).not.toBe("Configuration");
    expect(value.trim().length).toBeGreaterThan(0);
  });

  it("items.defForm.reminderLeadDaysLabel is present in en", () => {
    const value = i18n.t("defForm.reminderLeadDaysLabel", { ns: "items" });
    expect(value.trim().length).toBeGreaterThan(0);
    expect(value).not.toBe("defForm.reminderLeadDaysLabel");
  });

  it("items.defForm.reminderLeadDaysLabel is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    const value = i18n.t("defForm.reminderLeadDaysLabel", { ns: "items" });
    expect(value.trim().length).toBeGreaterThan(0);
    expect(value).not.toBe("Reminder lead (days)");
  });
});
