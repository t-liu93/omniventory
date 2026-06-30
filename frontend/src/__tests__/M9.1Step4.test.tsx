/**
 * M9.1 Step 4 — LLM provider settings section + test-connection UI + i18n.
 *
 * Coverage (per M9.1 §5 "Frontend" / §7.1/§7.2 / §9 Step 4 / §10 Step 4):
 *
 * 1. Load: LLM section shows key as "Not set" when api_key_is_set=false.
 * 2. Load: LLM section shows key as "Set" when api_key_is_set=true; key value never appears.
 * 3. Save — clear branch: Clear pressed → PATCH body includes api_key: "".
 * 4. Save — new value branch: new key typed → PATCH body includes api_key: <value>.
 * 5. Save — omit branch: neither clear nor new value → PATCH body omits api_key.
 * 6. Save — base_url/model/enabled round-trip → body includes those fields.
 * 7. Test connection — success: three stage rows rendered (pass/pass/pass) with correct badges.
 * 8. Test connection — connectivity fail: connectivity=fail, model_answers/multimodal=skipped.
 * 9. Test connection — multimodal fail: connectivity=pass, model=pass, multimodal=fail.
 * 10. Test connection — detail localized via mapApiError (llm.auth_failed code).
 * 11. Test connection — network failure: error Alert shown, no staged rows.
 * 12. i18n — llm namespace parity: en and zh have identical key sets.
 * 13. i18n — errors namespace includes all seven llm.* codes in en and zh.
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

// Catalog imports for parity / key-presence tests
import enLlm from "../i18n/locales/en/llm.json";
import zhLlm from "../i18n/locales/zh/llm.json";
import enErrors from "../i18n/locales/en/errors.json";
import zhErrors from "../i18n/locales/zh/errors.json";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyResult = any;

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** Minimal SettingsResponse with the llm block. */
function makeSettings(llmOverride: Partial<{
  enabled: boolean;
  base_url: string | null;
  model: string | null;
  api_key_is_set: boolean;
}> = {}): AnyResult {
  return {
    reminders: {
      best_before_lead_days: 3,
      warranty_lead_days: 30,
      maintenance_lead_days: 7,
      low_stock_repeat_days: [1, 3],
      scan_time: "08:00",
    },
    shopping_list: { auto_add_low_stock: true },
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
    llm: {
      enabled: false,
      base_url: null,
      model: null,
      api_key_is_set: false,
      ...llmOverride,
    },
  };
}

/** Full-pass LlmTestResult. */
const fullPassResult: AnyResult = {
  ok: true,
  connectivity: { status: "pass", detail: null },
  model_answers: { status: "pass", detail: null },
  multimodal: { status: "pass", detail: null },
};

/** Connectivity failure — model + multimodal skipped. */
const connectivityFailResult: AnyResult = {
  ok: false,
  connectivity: { status: "fail", detail: "llm.auth_failed" },
  model_answers: { status: "skipped", detail: null },
  multimodal: { status: "skipped", detail: null },
};

/** Model answers OK but multimodal fails. */
const multimodalFailResult: AnyResult = {
  ok: false,
  connectivity: { status: "pass", detail: null },
  model_answers: { status: "pass", detail: null },
  multimodal: { status: "fail", detail: "llm.not_multimodal" },
};

/**
 * Multimodal fail with the **actual** composite wire format the backend emits
 * (§4.3): "llm.not_multimodal: '<model reply repr>'".  This is the real path
 * for a text-only model — the FE must split the code from the reply and
 * localize only the code token.
 */
const multimodalFailCompositeResult: AnyResult = {
  ok: false,
  connectivity: { status: "pass", detail: null },
  model_answers: { status: "pass", detail: null },
  multimodal: { status: "fail", detail: "llm.not_multimodal: 'I see a cat.'" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mockGet(settings: AnyResult = makeSettings()) {
  vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
    if (path === "/api/settings") {
      return { data: settings, response: new Response(null, { status: 200 }) };
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

// ── Tests: Load ───────────────────────────────────────────────────────────────

describe("LLM section — load (api_key never plaintext)", () => {
  it("shows 'Not set' badge when api_key_is_set=false", async () => {
    mockGet(makeSettings({ api_key_is_set: false }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-api-key-status")).toBeDefined();
    });

    const badge = screen.getByTestId("llm-api-key-status");
    expect(badge.textContent).toBe("Not set");

    // The password input must be blank — never pre-filled with a real value
    const input = screen.getByTestId("llm-api-key-input") as HTMLInputElement;
    expect(input.value).toBe("");
  });

  it("shows 'Set' badge when api_key_is_set=true, never reveals key value", async () => {
    mockGet(makeSettings({ api_key_is_set: true, base_url: "https://openrouter.ai/api", model: "gpt-4o" }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-api-key-status")).toBeDefined();
    });

    const badge = screen.getByTestId("llm-api-key-status");
    expect(badge.textContent).toBe("Set");

    // Input must remain blank — key is write-only
    const input = screen.getByTestId("llm-api-key-input") as HTMLInputElement;
    expect(input.value).toBe("");
  });

  it("populates base_url and model from settings", async () => {
    mockGet(makeSettings({ base_url: "https://api.openai.com", model: "gpt-4o-mini" }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-base-url-input")).toBeDefined();
    });

    const baseUrlInput = screen.getByTestId("llm-base-url-input") as HTMLInputElement;
    expect(baseUrlInput.value).toBe("https://api.openai.com");

    const modelInput = screen.getByTestId("llm-model-input") as HTMLInputElement;
    expect(modelInput.value).toBe("gpt-4o-mini");
  });
});

// ── Tests: Save — clear > new > omit ─────────────────────────────────────────

describe("LLM section — Save (clear > new value > omit on api_key)", () => {
  it("Clear pressed → PATCH body includes api_key: '' (empty string)", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    let capturedBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      capturedBody = opts?.body;
      return { data: makeSettings({ api_key_is_set: false }), response: new Response(null, { status: 200 }) } as AnyResult;
    });
    // After save loadAll re-fetches
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: makeSettings({ api_key_is_set: false }), response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-api-key-clear-btn")).toBeDefined();
    });

    // Press Clear
    await act(async () => {
      fireEvent.click(screen.getByTestId("llm-api-key-clear-btn"));
    });

    // Press Save
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-llm-btn"));
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody.llm.api_key).toBe("");
  });

  it("New key typed → PATCH body includes api_key: <value>", async () => {
    mockGet(makeSettings());

    let capturedBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      capturedBody = opts?.body;
      return { data: makeSettings({ api_key_is_set: true }), response: new Response(null, { status: 200 }) } as AnyResult;
    });
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: makeSettings({ api_key_is_set: true }), response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-api-key-input")).toBeDefined();
    });

    // Type a new key
    await act(async () => {
      fireEvent.change(screen.getByTestId("llm-api-key-input"), {
        target: { value: "sk-new-secret-key" },
      });
    });

    // Press Save
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-llm-btn"));
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody.llm.api_key).toBe("sk-new-secret-key");
  });

  it("Neither clear nor new value → PATCH body omits api_key field entirely", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    let capturedBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      capturedBody = opts?.body;
      return { data: makeSettings({ api_key_is_set: true }), response: new Response(null, { status: 200 }) } as AnyResult;
    });
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: makeSettings({ api_key_is_set: true }), response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("save-llm-btn")).toBeDefined();
    });

    // Press Save without changing anything
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-llm-btn"));
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // api_key must be absent (undefined / not present), not "" or null
    expect("api_key" in capturedBody.llm).toBe(false);
  });

  it("base_url, model, enabled included in PATCH body", async () => {
    mockGet(makeSettings());

    let capturedBody: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: AnyResult, opts: AnyResult) => {
      capturedBody = opts?.body;
      return { data: makeSettings(), response: new Response(null, { status: 200 }) } as AnyResult;
    });
    vi.mocked(client.GET).mockImplementation(async (path: AnyResult) => {
      if (path === "/api/settings") {
        return { data: makeSettings(), response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: {}, response: new Response(null, { status: 404 }) };
    });

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-base-url-input")).toBeDefined();
    });

    // Edit fields
    await act(async () => {
      fireEvent.change(screen.getByTestId("llm-base-url-input"), {
        target: { value: "https://openrouter.ai/api" },
      });
      fireEvent.change(screen.getByTestId("llm-model-input"), {
        target: { value: "openai/gpt-4o-mini" },
      });
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("llm-enabled-switch"));
    });

    // Save
    await act(async () => {
      fireEvent.click(screen.getByTestId("save-llm-btn"));
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody.llm.base_url).toBe("https://openrouter.ai/api");
    expect(capturedBody.llm.model).toBe("openai/gpt-4o-mini");
    // enabled toggled from false → true
    expect(capturedBody.llm.enabled).toBe(true);
  });
});

// ── Tests: Save — server error localization ───────────────────────────────────

describe("LLM section — Save server errors localized via mapApiError", () => {
  it("llm.unsafe_url code → localized message shown inline", async () => {
    mockGet(makeSettings());

    vi.mocked(client.PATCH).mockImplementation(async () => ({
      data: undefined,
      error: { code: "llm.unsafe_url", message: "blocked", params: null },
      response: new Response(null, { status: 422 }),
    } as AnyResult));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("save-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("save-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-error")).toBeDefined();
    });

    const errEl = screen.getByTestId("llm-error");
    // Must show a localized string, not the raw code
    expect(errEl.textContent).toContain("SSRF");
    expect(errEl.textContent).not.toBe("llm.unsafe_url");
  });
});

// ── Tests: Test connection ────────────────────────────────────────────────────

describe("LLM test connection — three staged rows", () => {
  it("all-pass: three pass badges rendered, no error shown", async () => {
    mockGet(makeSettings({ api_key_is_set: true, base_url: "https://openrouter.ai/api", model: "gpt-4o" }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: fullPassResult,
      error: undefined,
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-test-results")).toBeDefined();
    });

    // All three badges should read "Pass"
    const badges = [
      screen.getByTestId("llm-test-badge-connectivity"),
      screen.getByTestId("llm-test-badge-model_answers"),
      screen.getByTestId("llm-test-badge-multimodal"),
    ];
    for (const badge of badges) {
      expect(badge.textContent).toBe("Pass");
    }

    // No error alert
    expect(screen.queryByTestId("llm-error")).toBeNull();
  });

  it("connectivity fail → fail badge on connectivity, skipped on model+multimodal", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: connectivityFailResult,
      error: undefined,
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-test-results")).toBeDefined();
    });

    expect(screen.getByTestId("llm-test-badge-connectivity").textContent).toBe("Fail");
    expect(screen.getByTestId("llm-test-badge-model_answers").textContent).toBe("Skipped");
    expect(screen.getByTestId("llm-test-badge-multimodal").textContent).toBe("Skipped");
  });

  it("multimodal fail → connectivity/model pass, multimodal fail badge", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: multimodalFailResult,
      error: undefined,
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-test-results")).toBeDefined();
    });

    expect(screen.getByTestId("llm-test-badge-connectivity").textContent).toBe("Pass");
    expect(screen.getByTestId("llm-test-badge-model_answers").textContent).toBe("Pass");
    expect(screen.getByTestId("llm-test-badge-multimodal").textContent).toBe("Fail");

    // No blocking error alert — failure is inline only
    expect(screen.queryByTestId("llm-error")).toBeNull();
  });

  it("detail code is localized via mapApiError (llm.auth_failed → English message)", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: connectivityFailResult, // connectivity detail = "llm.auth_failed"
      error: undefined,
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-test-detail-connectivity")).toBeDefined();
    });

    const detailEl = screen.getByTestId("llm-test-detail-connectivity");
    // Must be a localized string, not the raw code
    expect(detailEl.textContent).not.toBe("llm.auth_failed");
    expect(detailEl.textContent).toContain("API key");
  });

  it("multimodal row rendered — receipt scan prerequisite label present", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: fullPassResult,
      error: undefined,
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-test-stage-multimodal")).toBeDefined();
    });

    // Multimodal stage row must be identifiable (prerequisite label present)
    expect(screen.getByTestId("llm-test-stage-multimodal")).toBeDefined();
  });

  it("network-level failure: error Alert shown, staged rows absent", async () => {
    mockGet(makeSettings({ api_key_is_set: true }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: undefined,
      error: { code: "llm.connection_failed", message: "Network error", params: null },
      response: new Response(null, { status: 502 }),
    } as AnyResult));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-error")).toBeDefined();
    });

    // Error alert must show; staged rows must NOT be present
    expect(screen.queryByTestId("llm-test-results")).toBeNull();
  });

  it("multimodal composite detail: localizes code, surfaces model reply — real backend wire format", async () => {
    // This test covers the §4.3 / §11 step-4 scenario: a text-only model causes
    // the multimodal stage to emit "llm.not_multimodal: '<model reply repr>'".
    // The FE must split the composite string, localize the code token ("image
    // input" message), and render the model's reply as secondary supplementary
    // text — NOT fall back to the generic "Something went wrong" message.
    mockGet(makeSettings({ api_key_is_set: true }));

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: multimodalFailCompositeResult,
      error: undefined,
      response: new Response(null, { status: 200 }),
    }));

    await act(async () => {
      renderConfiguration();
    });

    await waitFor(() => {
      expect(screen.getByTestId("test-llm-btn")).toBeDefined();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("test-llm-btn"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("llm-test-detail-multimodal")).toBeDefined();
    });

    const detailEl = screen.getByTestId("llm-test-detail-multimodal");

    // Primary: localized "not multimodal" message — NOT the generic fallback
    expect(detailEl.textContent).not.toContain("Something went wrong");
    expect(detailEl.textContent).toContain("image input");

    // Secondary: the model's raw reply must also be surfaced
    const replyEl = screen.getByTestId("llm-test-detail-multimodal-reply");
    expect(replyEl.textContent).toContain("I see a cat.");
  });
});

// ── Tests: i18n parity ────────────────────────────────────────────────────────

describe("i18n — llm namespace key parity (en vs zh)", () => {
  /** Recursively collect leaf key paths. */
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

  it("llm namespace: en and zh have identical key sets", () => {
    const enKeys = collectKeys(enLlm).sort();
    const zhKeys = collectKeys(zhLlm).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(missingInZh, "Keys in en/llm missing from zh/llm").toEqual([]);
    expect(extraInZh, "Extra keys in zh/llm not in en/llm").toEqual([]);
  });
});

describe("i18n — errors namespace includes all llm.* codes (en + zh)", () => {
  const LLM_CODES = [
    "not_configured",
    "unsafe_url",
    "connection_failed",
    "auth_failed",
    "model_unavailable",
    "not_multimodal",
    "provider_error",
  ] as const;

  for (const code of LLM_CODES) {
    it(`en/errors.llm.${code} is present and non-empty`, () => {
      const enLlmErrors = (enErrors as AnyResult).llm;
      expect(enLlmErrors).toBeDefined();
      expect(typeof enLlmErrors[code]).toBe("string");
      expect(enLlmErrors[code].length).toBeGreaterThan(0);
    });

    it(`zh/errors.llm.${code} is present and non-empty`, () => {
      const zhLlmErrors = (zhErrors as AnyResult).llm;
      expect(zhLlmErrors).toBeDefined();
      expect(typeof zhLlmErrors[code]).toBe("string");
      expect(zhLlmErrors[code].length).toBeGreaterThan(0);
    });
  }
});
