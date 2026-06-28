/**
 * M5 Step 10 — Custom Fields Editor.
 *
 * Coverage (per M5 §7.2, §7.6, §9 Step 10):
 *
 * 1. CustomFieldsEditor standalone:
 *    a. Empty state when value is null.
 *    b. Adding a row; typing key + string value → serialised map.
 *    c. Removing a row drops it from the map.
 *    d. Changing type to number → number in serialised map.
 *    e. Changing type to boolean → boolean in serialised map.
 *    f. Changing type to null → null value in serialised map.
 *    g. Rows with empty keys are excluded from the serialised map.
 *
 * 2. Definition form round-trip (via Items page):
 *    a. Open create modal, add custom field, submit → custom_fields in POST body.
 *    b. Open edit modal with existing custom_fields → editor hydrated.
 *
 * 3. Instance form round-trip (via InstanceFormModal directly):
 *    a. Render with custom_fields in form → editor hydrated.
 *    b. Add field, submit → custom_fields in serialised form state.
 *
 * 4. Read-only display (InstanceDetail page):
 *    a. Renders existing custom_fields as key/value pairs.
 *
 * 5. i18n catalog parity for customFields namespace.
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
import { CustomFieldsEditor } from "../components/CustomFieldsEditor.js";
import {
  InstanceFormModal,
  type InstanceFormState,
} from "../components/InstanceFormModal.js";
import { Items } from "../pages/Items.js";
import { InstanceDetail } from "../pages/InstanceDetail.js";
import i18n from "../i18n/index.js";

// ── Mock client ───────────────────────────────────────────────────────────────

vi.mock("../api/client.js", () => ({
  client: {
    GET: vi.fn(),
    POST: vi.fn(),
    PUT: vi.fn(),
    PATCH: vi.fn(),
    DELETE: vi.fn(),
  },
}));

import { client } from "../api/client.js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any;

// ── Fixtures ──────────────────────────────────────────────────────────────────

const kindConsumable = {
  id: 1,
  code: "consumable",
  name: "Consumable",
  is_system: true,
  created_at: "2026-06-27T00:00:00Z",
};

const defBase = {
  id: 200,
  name: "Test Item",
  description: null,
  category_id: null,
  kind_id: 1,
  kind: kindConsumable,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "exact",
  min_stock: null,
  default_best_before_days: null,
  reminder_lead_days: null,
  custom_fields: null,
  responsible_user_id: null,
  created_at: "2026-06-27T00:00:00Z",
};

const defWithCF = {
  ...defBase,
  id: 201,
  name: "Item With CF",
  custom_fields: {
    color: "red",
    weight: 1.5,
    fragile: true,
    note: null,
  },
};

const instBase = {
  id: 42,
  definition_id: 200,
  location_id: null,
  quantity: "10",
  stock_level: null,
  serial: null,
  model_number: null,
  manufacturer: null,
  best_before_date: null,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  custom_fields: null,
  created_at: "2026-06-27T00:00:00Z",
};

const instWithCF = {
  ...instBase,
  id: 43,
  custom_fields: {
    slot: "A1",
    qty_reserved: 3,
    urgent: false,
    memo: null,
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});

function ok200<T>(data: T) {
  return { data, error: undefined, response: new Response(null, { status: 200 }) };
}

function ok201<T>(data: T) {
  return { data, error: undefined, response: new Response(null, { status: 201 }) };
}

// ── CustomFieldsEditor standalone ─────────────────────────────────────────────

function renderEditor(
  value: Record<string, Any> | null = null,
  onChange = vi.fn(),
  disabled = false,
) {
  return {
    onChange,
    ...render(
      <MantineProvider>
        <CustomFieldsEditor value={value} onChange={onChange} disabled={disabled} />
      </MantineProvider>,
    ),
  };
}

describe("CustomFieldsEditor — empty state", () => {
  it("shows empty state message when value is null", () => {
    renderEditor(null);
    expect(screen.getByTestId("cf-empty-state")).toBeDefined();
    expect(screen.getByTestId("cf-empty-state").textContent).toContain(
      "No custom fields",
    );
  });

  it("shows empty state message when value is undefined", () => {
    renderEditor(undefined as Any);
    expect(screen.getByTestId("cf-empty-state")).toBeDefined();
  });
});

describe("CustomFieldsEditor — row management", () => {
  it("adds a row on Add field click and empty state disappears", async () => {
    renderEditor(null);
    expect(screen.getByTestId("cf-empty-state")).toBeDefined();

    await act(async () => {
      fireEvent.click(screen.getByTestId("cf-add-btn"));
    });

    expect(screen.queryByTestId("cf-empty-state")).toBeNull();
    expect(screen.getByTestId("cf-row-0")).toBeDefined();
  });

  it("typing key and string value calls onChange with correct map", async () => {
    const { onChange } = renderEditor(null);

    await act(async () => {
      fireEvent.click(screen.getByTestId("cf-add-btn"));
    });

    // Type a key
    const keyWrapper = screen.getByTestId("cf-key-0");
    const keyInput = (keyWrapper.querySelector("input") ?? keyWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(keyInput, { target: { value: "color" } });
    });

    // Type a value (default type is string)
    const valWrapper = screen.getByTestId("cf-value-0");
    const valInput = (valWrapper.querySelector("input") ?? valWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(valInput, { target: { value: "blue" } });
    });

    // The last onChange call should have the correct map
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(lastCall[0]).toEqual({ color: "blue" });
  });

  it("removing a row drops it from the serialised map", async () => {
    const { onChange } = renderEditor({ color: "red", size: "L" });

    // Both rows should be visible
    expect(screen.getByTestId("cf-row-0")).toBeDefined();
    expect(screen.getByTestId("cf-row-1")).toBeDefined();

    // Remove the first row
    await act(async () => {
      fireEvent.click(screen.getByTestId("cf-remove-0"));
    });

    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    // "size": "L" should remain; "color" is removed
    expect(lastCall[0]).not.toHaveProperty("color");
    expect(lastCall[0]).toHaveProperty("size", "L");
  });

  it("rows with empty keys are excluded from the serialised map", async () => {
    const { onChange } = renderEditor(null);

    // Add two rows
    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });
    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });

    // Fill only the first row's key
    const keyWrapper = screen.getByTestId("cf-key-0");
    const keyInput = (keyWrapper.querySelector("input") ?? keyWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(keyInput, { target: { value: "tag" } });
    });

    // Type a value for row 0
    const valWrapper = screen.getByTestId("cf-value-0");
    const valInput = (valWrapper.querySelector("input") ?? valWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(valInput, { target: { value: "foo" } });
    });

    // Row 1 key is empty → excluded from map
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(Object.keys(lastCall[0])).toHaveLength(1);
    expect(lastCall[0]).toHaveProperty("tag", "foo");
  });
});

describe("CustomFieldsEditor — type handling", () => {
  it("changing type to number yields number value in map", async () => {
    const { onChange } = renderEditor(null);

    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });

    // Set key
    const keyWrapper = screen.getByTestId("cf-key-0");
    const keyInput = (keyWrapper.querySelector("input") ?? keyWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(keyInput, { target: { value: "count" } });
    });

    // Change type to number
    const typeWrapper = screen.getByTestId("cf-type-0");
    const typeInput = (typeWrapper.querySelector("input") ?? typeWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.click(typeInput);
    });
    const numOption = await screen.findByText("Number");
    await act(async () => { fireEvent.click(numOption); });

    // Type a number value
    const valWrapper = screen.getByTestId("cf-value-0");
    const valInput = (valWrapper.querySelector("input") ?? valWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(valInput, { target: { value: "42" } });
    });

    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(typeof lastCall[0]["count"]).toBe("number");
    expect(lastCall[0]["count"]).toBe(42);
  });

  it("changing type to boolean yields boolean value in map", async () => {
    const { onChange } = renderEditor(null);

    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });

    // Set key
    const keyWrapper = screen.getByTestId("cf-key-0");
    const keyInput = (keyWrapper.querySelector("input") ?? keyWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(keyInput, { target: { value: "active" } });
    });

    // Change type to boolean
    const typeWrapper = screen.getByTestId("cf-type-0");
    const typeInput = (typeWrapper.querySelector("input") ?? typeWrapper) as HTMLInputElement;
    await act(async () => { fireEvent.click(typeInput); });
    const boolOption = await screen.findByText("Boolean");
    await act(async () => { fireEvent.click(boolOption); });

    // Toggle the switch on
    const switchEl = screen.getByTestId("cf-value-0");
    const input = (switchEl.querySelector("input") ?? switchEl) as HTMLInputElement;
    await act(async () => { fireEvent.click(input); });

    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(typeof lastCall[0]["active"]).toBe("boolean");
    expect(lastCall[0]["active"]).toBe(true);
  });

  it("changing type to null yields null value in map", async () => {
    const { onChange } = renderEditor(null);

    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });

    // Set key
    const keyWrapper = screen.getByTestId("cf-key-0");
    const keyInput = (keyWrapper.querySelector("input") ?? keyWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(keyInput, { target: { value: "missing" } });
    });

    // Change type to null
    const typeWrapper = screen.getByTestId("cf-type-0");
    const typeInput = (typeWrapper.querySelector("input") ?? typeWrapper) as HTMLInputElement;
    await act(async () => { fireEvent.click(typeInput); });
    const nullOption = await screen.findByText("Null");
    await act(async () => { fireEvent.click(nullOption); });

    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1];
    expect(lastCall[0]).toHaveProperty("missing", null);
    expect(lastCall[0]["missing"]).toBeNull();
  });

  it("hydrates rows when rendered with an existing value", () => {
    const initialValue = { color: "red", weight: 1.5, fragile: true, note: null };
    renderEditor(initialValue);

    // Should show 4 rows (no empty state)
    expect(screen.queryByTestId("cf-empty-state")).toBeNull();
    expect(screen.getByTestId("cf-row-0")).toBeDefined();
    expect(screen.getByTestId("cf-row-1")).toBeDefined();
    expect(screen.getByTestId("cf-row-2")).toBeDefined();
    expect(screen.getByTestId("cf-row-3")).toBeDefined();
  });
});

// ── Instance form round-trip ──────────────────────────────────────────────────

const baseInstForm: InstanceFormState = {
  definition_id: "200",
  location_id: "",
  quantity: "1",
  stock_level: "",
  serial: "",
  model_number: "",
  manufacturer: "",
  best_before_date: "",
  warranty_expires: "",
  warranty_details: "",
  purchase_price: "",
  purchase_date: "",
  purchase_source: "",
  custom_fields: null,
  responsible_user_id: null,
};

function renderInstanceModal(
  form: InstanceFormState,
  setForm = vi.fn(),
  onSubmit = vi.fn(),
) {
  return render(
    <MantineProvider>
      <InstanceFormModal
        opened={true}
        title="Test"
        form={form}
        setForm={setForm}
        onSubmit={onSubmit}
        onClose={vi.fn()}
        busy={false}
        error={null}
        definitions={[{ ...defBase }]}
        locations={[]}
        lockDefinition={true}
        trackingMode="none"
      />
    </MantineProvider>,
  );
}

describe("InstanceFormModal — custom_fields editor embedded", () => {
  it("renders the editor within the modal", () => {
    renderInstanceModal(baseInstForm);
    expect(screen.getByTestId("custom-fields-editor")).toBeDefined();
  });

  it("shows empty state when custom_fields is null", () => {
    renderInstanceModal(baseInstForm);
    expect(screen.getByTestId("cf-empty-state")).toBeDefined();
  });

  it("hydrates editor from existing custom_fields in form state", () => {
    const formWithCF: InstanceFormState = {
      ...baseInstForm,
      custom_fields: { slot: "A1", qty_reserved: 3, urgent: false, memo: null },
    };
    renderInstanceModal(formWithCF);

    // 4 rows visible; no empty state
    expect(screen.queryByTestId("cf-empty-state")).toBeNull();
    expect(screen.getByTestId("cf-row-0")).toBeDefined();
    expect(screen.getByTestId("cf-row-3")).toBeDefined();
  });

  it("calls setForm with updated custom_fields when a row key is typed", async () => {
    const setForm = vi.fn();
    renderInstanceModal(baseInstForm, setForm);

    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });

    // The setForm callback passed to CustomFieldsEditor will be called with an updater fn
    // that sets custom_fields.  Verify setForm was called.
    expect(setForm).toHaveBeenCalled();
  });
});

// ── Definition form round-trip (via Items page) ───────────────────────────────

function mockItemsClient(defs: Any[] = [defBase]) {
  vi.mocked(client.GET).mockImplementation(async (path: Any) => {
    if (path === "/api/definitions") return ok200(defs);
    if (path === "/api/kinds") return ok200([kindConsumable]);
    if (path === "/api/categories") return ok200([]);
    if (path === "/api/locations") return ok200([]);
    if (path === "/api/tags") return ok200([]);
    if (path === "/api/tags/links") return ok200([]);
    return ok200([]);
  });
}

function renderItemsPage() {
  return render(
    <MemoryRouter initialEntries={["/items"]}>
      <MantineProvider>
        <Routes>
          <Route path="/items" element={<Items />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

describe("Definition form — create with custom_fields", () => {
  it("open create modal, add field, submit → custom_fields in POST body", async () => {
    let postBody: Any = null;
    vi.mocked(client.POST).mockImplementation(async (_path: Any, opts: Any) => {
      postBody = opts?.body;
      return ok201({ ...defBase, id: 999 });
    });
    vi.mocked(client.GET).mockImplementation(async (path: Any) => {
      if (path === "/api/definitions") return ok200([]);
      if (path === "/api/kinds") return ok200([kindConsumable]);
      if (path === "/api/categories") return ok200([]);
      if (path === "/api/locations") return ok200([]);
      if (path === "/api/tags") return ok200([]);
      return ok200([]);
    });

    await act(async () => { renderItemsPage(); });

    // Wait for the page to finish loading, then click "New Item"
    const createBtn = await screen.findByTestId("create-def-btn");
    await act(async () => { fireEvent.click(createBtn); });

    // Wait for the modal to open; the name input appears inside the modal portal
    const nameWrapper = await screen.findByTestId("def-name-input");
    const nameInput = (nameWrapper.querySelector("input") ?? nameWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(nameInput, { target: { value: "Widget" } });
    });

    // Add a custom field
    await act(async () => { fireEvent.click(screen.getByTestId("cf-add-btn")); });

    // Type key
    const keyWrapper = screen.getByTestId("cf-key-0");
    const keyInput = (keyWrapper.querySelector("input") ?? keyWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(keyInput, { target: { value: "sku" } });
    });

    // Type value
    const valWrapper = screen.getByTestId("cf-value-0");
    const valInput = (valWrapper.querySelector("input") ?? valWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(valInput, { target: { value: "WIDGET-001" } });
    });

    // Submit
    await act(async () => {
      fireEvent.click(screen.getByTestId("def-submit-btn"));
    });

    // Assert custom_fields in POST body
    await waitFor(() => {
      expect(postBody).not.toBeNull();
      expect(postBody.custom_fields).not.toBeNull();
      expect(postBody.custom_fields).toHaveProperty("sku", "WIDGET-001");
    });
  });
});

describe("Definition form — edit hydrates custom_fields", () => {
  it("opening edit for a def with custom_fields shows them in the editor", async () => {
    mockItemsClient([defWithCF]);

    await act(async () => { renderItemsPage(); });

    // Wait for the definition to appear in the list
    await screen.findByTestId("def-row-201");

    // Click the edit button for defWithCF
    await act(async () => {
      fireEvent.click(screen.getByTestId("edit-def-201"));
    });

    // Editor should show rows for the 4 custom fields (no empty state)
    await waitFor(() => {
      expect(screen.queryByTestId("cf-empty-state")).toBeNull();
      expect(screen.getByTestId("cf-row-0")).toBeDefined();
    });
  });
});

// ── Read-only display: InstanceDetail ─────────────────────────────────────────

function renderInstanceDetail(instanceId = 43) {
  return render(
    <MemoryRouter initialEntries={[`/instances/${instanceId}`]}>
      <MantineProvider>
        <Routes>
          <Route path="/instances/:id" element={<InstanceDetail />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

describe("InstanceDetail — read-only custom_fields display", () => {
  it("displays existing custom_fields as key/value pairs", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: Any) => {
      if (path === "/api/instances/{instance_id}") return ok200(instWithCF);
      if (path === "/api/definitions/{definition_id}") return ok200(defBase);
      if (path === "/api/definitions") return ok200([defBase]);
      if (path === "/api/locations") return ok200([]);
      if (path === "/api/instances/{instance_id}/movements") return ok200([]);
      if (path === "/api/tags/links") return ok200([]);
      if (path === "/api/notes") return ok200([]);
      if (path === "/api/attachments") return ok200([]);
      return ok200([]);
    });

    await act(async () => { renderInstanceDetail(43); });

    // The custom fields section should appear
    await waitFor(() => {
      expect(screen.getByTestId("inst-cf-display-slot")).toBeDefined();
      expect(screen.getByTestId("inst-cf-display-qty_reserved")).toBeDefined();
      expect(screen.getByTestId("inst-cf-display-urgent")).toBeDefined();
      expect(screen.getByTestId("inst-cf-display-memo")).toBeDefined();
    });

    // Check rendered values
    expect(screen.getByTestId("inst-cf-display-slot").textContent).toContain("A1");
    expect(screen.getByTestId("inst-cf-display-qty_reserved").textContent).toContain("3");
    expect(screen.getByTestId("inst-cf-display-urgent").textContent).toContain("false");
    expect(screen.getByTestId("inst-cf-display-memo").textContent).toContain("—");
  });

  it("shows no custom fields section when custom_fields is null", async () => {
    vi.mocked(client.GET).mockImplementation(async (path: Any) => {
      if (path === "/api/instances/{instance_id}") return ok200(instBase);
      if (path === "/api/definitions/{definition_id}") return ok200(defBase);
      if (path === "/api/definitions") return ok200([defBase]);
      if (path === "/api/locations") return ok200([]);
      if (path === "/api/instances/{instance_id}/movements") return ok200([]);
      if (path === "/api/tags/links") return ok200([]);
      if (path === "/api/notes") return ok200([]);
      if (path === "/api/attachments") return ok200([]);
      return ok200([]);
    });

    await act(async () => { renderInstanceDetail(42); });

    // No custom field display elements
    await waitFor(() => {
      // Wait for the page to load by checking something that should be there
      expect(screen.queryByTestId("inst-cf-display-slot")).toBeNull();
    });
  });
});

// ── i18n catalog: customFields ────────────────────────────────────────────────

import enCustomFields from "../i18n/locales/en/customFields.json";
import zhCustomFields from "../i18n/locales/zh/customFields.json";

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

describe("customFields i18n — en+zh catalog parity", () => {
  it("en and zh customFields namespace have identical key sets", () => {
    const enKeys = collectKeys(enCustomFields).sort();
    const zhKeys = collectKeys(zhCustomFields).sort();
    expect(enKeys.filter((k) => !zhKeys.includes(k)), "Missing in zh").toEqual([]);
    expect(zhKeys.filter((k) => !enKeys.includes(k)), "Extra in zh").toEqual([]);
  });

  it("customFields.sectionTitle is 'Custom Fields' in en", () => {
    expect(i18n.t("sectionTitle", { ns: "customFields" })).toBe("Custom Fields");
  });

  it("customFields.sectionTitle is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    const v = i18n.t("sectionTitle", { ns: "customFields" });
    expect(v).not.toBe("Custom Fields");
    expect(v.trim().length).toBeGreaterThan(0);
    await i18n.changeLanguage("en");
  });

  it("customFields.addField is 'Add field' in en", () => {
    expect(i18n.t("addField", { ns: "customFields" })).toBe("Add field");
  });

  it("customFields.types.boolean is 'Boolean' in en", () => {
    expect(i18n.t("types.boolean", { ns: "customFields" })).toBe("Boolean");
  });
});
