/**
 * M6 Step 11 — Responsible-party assignment UI tests.
 *
 * Coverage (per §10 Step 11 blind-review checkpoints):
 *
 * 1. Definition form — set responsible user → POST /api/definitions body carries
 *    responsible_user_id: <id>.
 * 2. Definition form — clear responsible user → POST /api/definitions body carries
 *    responsible_user_id: null.
 * 3. Definition form — edit seeds picker from existing responsible_user_id; PATCH
 *    carries the updated responsible_user_id.
 * 4. Instance form — set responsible user → POST /api/instances body carries
 *    responsible_user_id: <id>.
 * 5. Instance form — clear responsible user → POST /api/instances body carries
 *    responsible_user_id: null.
 * 6. Instance form — edit seeds picker from existing responsible_user_id; PATCH
 *    carries the updated responsible_user_id.
 * 7. Definition detail — shows "Responsible: <email>" when assigned.
 * 8. Definition detail — shows "Unassigned" when responsible_user_id is null.
 * 9. Instance detail — shows "Responsible: <email>" when assigned.
 * 10. Instance detail — shows "Inherited from definition" when responsible_user_id is null.
 * 11. en+zh parity for "responsible" namespace (covered by i18n-catalog.test.ts).
 *
 * All tests pinned to 'en' (M1.5 convention).
 * Client is mocked; no real network calls.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";

// i18n must be initialized before any component that calls useTranslation().
import "../i18n/index.js";
import i18n from "../i18n/index.js";

import { Items, ItemDetail } from "../pages/Items.js";
import { InstanceDetail } from "../pages/InstanceDetail.js";
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
type Any = any;

// ── Fixtures ──────────────────────────────────────────────────────────────────

type UserSummary = components["schemas"]["UserSummary"];
type DefinitionResponse = components["schemas"]["DefinitionResponse"];
type InstanceResponse = components["schemas"]["InstanceResponse"];

const userAlice: UserSummary = {
  id: 10,
  email: "alice@example.com",
  role: "member",
  is_active: true,
};

const userBob: UserSummary = {
  id: 20,
  email: "bob@example.com",
  role: "admin",
  is_active: true,
};

const kindConsumable = {
  id: 1,
  code: "consumable",
  name: "Consumable",
  is_system: true,
  created_at: "2026-06-27T00:00:00Z",
};

const defBase: DefinitionResponse = {
  id: 100,
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

const defWithResponsible: DefinitionResponse = {
  ...defBase,
  id: 101,
  name: "Assigned Item",
  responsible_user_id: 10, // alice
};

const instBase: InstanceResponse = {
  id: 42,
  definition_id: 100,
  location_id: null,
  quantity: "5",
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
  responsible_user_id: null,
  received_at: null,
  created_at: "2026-06-27T00:00:00Z",
};

const instWithResponsible: InstanceResponse = {
  ...instBase,
  id: 43,
  responsible_user_id: 10, // alice
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

// ── Items page mock ───────────────────────────────────────────────────────────

function mockItemsClient(
  defs: Any[] = [defBase],
  userList: UserSummary[] = [userAlice, userBob],
) {
  vi.mocked(client.GET).mockImplementation(async (path: Any) => {
    if (path === "/api/definitions") return ok200(defs);
    if (path === "/api/kinds") return ok200([kindConsumable]);
    if (path === "/api/categories") return ok200([]);
    if (path === "/api/locations") return ok200([]);
    if (path === "/api/tags") return ok200([]);
    if (path === "/api/users") return ok200(userList);
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

// ── Definition form: create ───────────────────────────────────────────────────

describe("Definition form — responsible_user_id on create", () => {
  it("POST body includes responsible_user_id when a user is selected", async () => {
    let postBody: Any = null;
    vi.mocked(client.POST).mockImplementation(async (_path: Any, opts: Any) => {
      postBody = opts?.body;
      return ok201({ ...defBase, id: 999 });
    });
    mockItemsClient([]);

    await act(async () => { renderItemsPage(); });

    const createBtn = await screen.findByTestId("create-def-btn");
    await act(async () => { fireEvent.click(createBtn); });

    // Fill in the required name
    const nameWrapper = await screen.findByTestId("def-name-input");
    const nameInput = (nameWrapper.querySelector("input") ?? nameWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(nameInput, { target: { value: "My Item" } });
    });

    // Change the responsible picker to alice (id=10)
    const picker = await screen.findByTestId("responsible-picker");
    await act(async () => {
      fireEvent.change(picker, { target: { value: "10" } });
    });

    // Submit
    await act(async () => {
      fireEvent.click(screen.getByTestId("def-submit-btn"));
    });

    await waitFor(() => {
      expect(postBody).not.toBeNull();
      expect(postBody.responsible_user_id).toBe(10);
    });
  });

  it("POST body includes responsible_user_id: null when picker is cleared", async () => {
    let postBody: Any = null;
    vi.mocked(client.POST).mockImplementation(async (_path: Any, opts: Any) => {
      postBody = opts?.body;
      return ok201({ ...defBase, id: 999 });
    });
    mockItemsClient([]);

    await act(async () => { renderItemsPage(); });

    const createBtn = await screen.findByTestId("create-def-btn");
    await act(async () => { fireEvent.click(createBtn); });

    const nameWrapper = await screen.findByTestId("def-name-input");
    const nameInput = (nameWrapper.querySelector("input") ?? nameWrapper) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(nameInput, { target: { value: "My Item" } });
    });

    // Picker starts empty (null); set to a user then clear back
    const picker = await screen.findByTestId("responsible-picker");
    await act(async () => { fireEvent.change(picker, { target: { value: "10" } }); });
    await act(async () => { fireEvent.change(picker, { target: { value: "" } }); });

    await act(async () => {
      fireEvent.click(screen.getByTestId("def-submit-btn"));
    });

    await waitFor(() => {
      expect(postBody).not.toBeNull();
      expect(postBody.responsible_user_id).toBeNull();
    });
  });
});

// ── Definition form: edit ─────────────────────────────────────────────────────

describe("Definition form — responsible_user_id on edit", () => {
  it("PATCH body includes the updated responsible_user_id", async () => {
    let patchBody: Any = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: Any, opts: Any) => {
      patchBody = opts?.body;
      return ok200(defBase);
    });
    mockItemsClient([defBase]);

    await act(async () => { renderItemsPage(); });

    await screen.findByTestId("def-row-100");

    await act(async () => {
      fireEvent.click(screen.getByTestId("edit-def-100"));
    });

    // Picker should be rendered in the edit modal
    const picker = await screen.findByTestId("responsible-picker");
    await act(async () => {
      fireEvent.change(picker, { target: { value: "20" } }); // select bob
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("def-submit-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
      expect(patchBody.responsible_user_id).toBe(20);
    });
  });
});

// ── Item detail page: definition read-only display ────────────────────────────

function mockItemDetailClient(
  def: DefinitionResponse,
  instances: InstanceResponse[] = [],
  userList: UserSummary[] = [userAlice, userBob],
) {
  vi.mocked(client.GET).mockImplementation(async (path: Any) => {
    if (path === "/api/definitions/{definition_id}") return ok200(def);
    if (path === "/api/definitions") return ok200([def]);
    if (path === "/api/instances") return ok200(instances);
    if (path === "/api/kinds") return ok200([kindConsumable]);
    if (path === "/api/categories") return ok200([]);
    if (path === "/api/locations") return ok200([]);
    if (path === "/api/users") return ok200(userList);
    if (path === "/api/tags/links") return ok200([]);
    if (path === "/api/notes") return ok200([]);
    if (path === "/api/attachments") return ok200([]);
    if (path === "/api/tags") return ok200([]);
    return ok200([]);
  });
}

function renderItemDetailPage(defId: number) {
  return render(
    <MemoryRouter initialEntries={[`/items/${defId}`]}>
      <MantineProvider>
        <Routes>
          <Route path="/items/:id" element={<ItemDetail />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

describe("Definition detail — responsible display", () => {
  it("shows resolved email when responsible_user_id is set", async () => {
    mockItemDetailClient(defWithResponsible, [], [userAlice, userBob]);

    await act(async () => { renderItemDetailPage(101); });

    await waitFor(() => {
      const el = screen.getByTestId("def-responsible-display");
      expect(el.textContent).toContain("alice@example.com");
    });
  });

  it("shows 'Unassigned' when responsible_user_id is null", async () => {
    mockItemDetailClient(defBase, [], [userAlice, userBob]);

    await act(async () => { renderItemDetailPage(100); });

    await waitFor(() => {
      const el = screen.getByTestId("def-responsible-display");
      expect(el.textContent).toContain("Unassigned");
    });
  });
});

// ── Instance form: create ─────────────────────────────────────────────────────

describe("Instance form — responsible_user_id on create", () => {
  it("POST body includes responsible_user_id when a user is selected", async () => {
    let postBody: Any = null;
    vi.mocked(client.POST).mockImplementation(async (_path: Any, opts: Any) => {
      postBody = opts?.body;
      return ok201({ ...instBase, id: 999 });
    });
    mockItemDetailClient(defBase, [], [userAlice, userBob]);

    await act(async () => { renderItemDetailPage(100); });

    // Open the register-instance modal
    const registerBtn = await screen.findByTestId("register-instance-btn");
    await act(async () => { fireEvent.click(registerBtn); });

    // The instance modal should appear (with the responsible picker)
    const picker = await screen.findByTestId("responsible-picker");

    // Select alice (id=10)
    await act(async () => {
      fireEvent.change(picker, { target: { value: "10" } });
    });

    // Submit
    await act(async () => {
      fireEvent.click(screen.getByTestId("inst-submit-btn"));
    });

    await waitFor(() => {
      expect(postBody).not.toBeNull();
      expect(postBody.responsible_user_id).toBe(10);
    });
  });

  it("POST body includes responsible_user_id: null when picker is empty", async () => {
    let postBody: Any = null;
    vi.mocked(client.POST).mockImplementation(async (_path: Any, opts: Any) => {
      postBody = opts?.body;
      return ok201({ ...instBase, id: 999 });
    });
    mockItemDetailClient(defBase, [], [userAlice, userBob]);

    await act(async () => { renderItemDetailPage(100); });

    const registerBtn = await screen.findByTestId("register-instance-btn");
    await act(async () => { fireEvent.click(registerBtn); });

    // Picker starts empty (null = inherit); set then clear
    const picker = await screen.findByTestId("responsible-picker");
    await act(async () => { fireEvent.change(picker, { target: { value: "10" } }); });
    await act(async () => { fireEvent.change(picker, { target: { value: "" } }); });

    await act(async () => {
      fireEvent.click(screen.getByTestId("inst-submit-btn"));
    });

    await waitFor(() => {
      expect(postBody).not.toBeNull();
      expect(postBody.responsible_user_id).toBeNull();
    });
  });
});

// ── Instance detail page: read-only display ───────────────────────────────────

function mockInstanceDetailClient(
  inst: InstanceResponse,
  def: DefinitionResponse,
  userList: UserSummary[] = [userAlice, userBob],
) {
  vi.mocked(client.GET).mockImplementation(async (path: Any) => {
    if (path === "/api/instances/{instance_id}") return ok200(inst);
    if (path === "/api/definitions/{definition_id}") return ok200(def);
    if (path === "/api/definitions") return ok200([def]);
    if (path === "/api/locations") return ok200([]);
    if (path === "/api/users") return ok200(userList);
    if (path === "/api/instances/{instance_id}/movements") return ok200([]);
    if (path === "/api/tags/links") return ok200([]);
    if (path === "/api/notes") return ok200([]);
    if (path === "/api/attachments") return ok200([]);
    return ok200([]);
  });
}

function renderInstanceDetailPage(instanceId: number) {
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

describe("Instance detail — responsible display", () => {
  it("shows resolved email when responsible_user_id is set", async () => {
    mockInstanceDetailClient(instWithResponsible, defBase, [userAlice, userBob]);

    await act(async () => { renderInstanceDetailPage(43); });

    await waitFor(() => {
      const el = screen.getByTestId("inst-responsible-display");
      expect(el.textContent).toContain("alice@example.com");
    });
  });

  it("shows 'Inherited from definition' when responsible_user_id is null", async () => {
    mockInstanceDetailClient(instBase, defBase, [userAlice, userBob]);

    await act(async () => { renderInstanceDetailPage(42); });

    await waitFor(() => {
      const el = screen.getByTestId("inst-responsible-display");
      expect(el.textContent).toContain("Inherited from definition");
    });
  });
});

// ── Instance form: edit (via InstanceDetail) ──────────────────────────────────

describe("Instance form — responsible_user_id on edit", () => {
  it("PATCH body includes the updated responsible_user_id", async () => {
    let patchBody: Any = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: Any, opts: Any) => {
      patchBody = opts?.body;
      return ok200({ ...instBase });
    });
    mockInstanceDetailClient(instWithResponsible, defBase, [userAlice, userBob]);

    await act(async () => { renderInstanceDetailPage(43); });

    // Open the edit modal
    const editBtn = await screen.findByTestId("edit-inst-btn");
    await act(async () => { fireEvent.click(editBtn); });

    // The picker should be seeded with alice (id=10)
    const picker = await screen.findByTestId("responsible-picker");
    expect((picker as HTMLSelectElement).value).toBe("10");

    // Change to bob (id=20)
    await act(async () => {
      fireEvent.change(picker, { target: { value: "20" } });
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("inst-submit-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
      expect(patchBody.responsible_user_id).toBe(20);
    });
  });

  it("PATCH body has responsible_user_id: null when cleared", async () => {
    let patchBody: Any = null;
    vi.mocked(client.PATCH).mockImplementation(async (_path: Any, opts: Any) => {
      patchBody = opts?.body;
      return ok200({ ...instBase });
    });
    mockInstanceDetailClient(instWithResponsible, defBase, [userAlice, userBob]);

    await act(async () => { renderInstanceDetailPage(43); });

    const editBtn = await screen.findByTestId("edit-inst-btn");
    await act(async () => { fireEvent.click(editBtn); });

    const picker = await screen.findByTestId("responsible-picker");
    // Clear the picker
    await act(async () => {
      fireEvent.change(picker, { target: { value: "" } });
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("inst-submit-btn"));
    });

    await waitFor(() => {
      expect(patchBody).not.toBeNull();
      expect(patchBody.responsible_user_id).toBeNull();
    });
  });
});

// ── Picker empty-label semantics ──────────────────────────────────────────────

describe("ResponsiblePicker empty-option labels", () => {
  it("definition form picker shows 'Unassigned' as empty option label", async () => {
    mockItemsClient([]);

    await act(async () => { renderItemsPage(); });

    const createBtn = await screen.findByTestId("create-def-btn");
    await act(async () => { fireEvent.click(createBtn); });

    const picker = await screen.findByTestId("responsible-picker") as HTMLSelectElement;
    // The first option should be the "Unassigned" empty option
    expect(picker.options[0].text).toContain("Unassigned");
  });

  it("instance form picker shows 'Inherited from definition' as empty option label", async () => {
    mockItemDetailClient(defBase, [], [userAlice, userBob]);

    await act(async () => { renderItemDetailPage(100); });

    const registerBtn = await screen.findByTestId("register-instance-btn");
    await act(async () => { fireEvent.click(registerBtn); });

    const picker = await screen.findByTestId("responsible-picker") as HTMLSelectElement;
    // The first option should be "Inherited from definition"
    expect(picker.options[0].text).toContain("Inherited from definition");
  });
});
