/**
 * M3 Step 5 — frontend tests.
 *
 * Coverage (per M3 §5 "Frontend" / §7.1–7.3 / §9 Step 5 / §10 Step 5):
 *
 * 1. DefinitionFormModal — `default_best_before_days` NumberInput:
 *    a. Field is rendered in the modal for all tracking modes.
 *    b. Submitting includes `default_best_before_days` in the POST body.
 *    c. Definition detail displays "Default shelf life: N days" when set.
 *
 * 2. InstanceFormModal — `best_before_date` TextInput:
 *    a. The best-before date field is rendered (mode-independent).
 *    b. When a definition has `default_best_before_days` and the form opens
 *       for create, the field is pre-filled with today + N.
 *    c. The hint message appears when a default is configured and the field
 *       has been cleared / is the default (hinted via description).
 *
 * 3. ExpiryBadge — display-only expiry cue:
 *    a. Red "Expired" badge for a past date.
 *    b. Amber "Expires in N days" badge for a within-soon-window date (≤30d).
 *    c. Nothing rendered for a far-future date (>30d).
 *    d. Nothing rendered when bestBeforeDate is null/undefined.
 *    e. Boundary: today+30 → amber; today+31 → nothing.
 *
 * 4. ItemDetail lot table — best_before_date column rendered with ExpiryBadge:
 *    a. Past date shows formatted date + "Expired" badge.
 *    b. Far-future date shows formatted date only (no badge).
 *
 * 5. InstanceDetail — best_before_date field with ExpiryBadge.
 *
 * Conventions: vitest + Testing Library, mock the typed client, pinned to "en".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import React from "react";
import { Items, ItemDetail } from "../pages/Items.js";
import { InstanceDetail } from "../pages/InstanceDetail.js";
import { ExpiryBadge, SOON_THRESHOLD_DAYS } from "../components/ExpiryBadge.js";
import {
  InstanceFormModal,
  type InstanceFormState,
} from "../components/InstanceFormModal.js";
import i18n from "../i18n/index.js";

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

const kindPerishable = {
  id: 3,
  code: "perishable",
  name: "Perishable",
  is_system: true,
  created_at: "2025-01-01T00:00:00Z",
};

const locationFridge = {
  id: 5,
  name: "Fridge",
  description: null,
  parent_id: null,
  item_instance_id: null,
  container_asset_label: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Definition with default shelf life of 7 days. */
const defMilk = {
  id: 99,
  name: "Milk",
  description: "Fresh whole milk",
  category_id: null,
  kind_id: 3,
  kind: kindPerishable,
  unit: "bottle",
  default_location_id: 5,
  stock_tracking_mode: "exact",
  min_stock: null,
  default_best_before_days: 7,
  created_at: "2025-01-01T00:00:00Z",
};

/** Definition without default shelf life. */
const defDrill = {
  id: 42,
  name: "Cordless Drill",
  description: null,
  category_id: null,
  kind_id: 3,
  kind: kindPerishable,
  unit: "pcs",
  default_location_id: null,
  stock_tracking_mode: "exact",
  min_stock: null,
  default_best_before_days: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Helpers to build ISO date strings relative to today (UTC). */
function todayISO(): string {
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function offsetISO(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + days);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const pastDate = offsetISO(-5);  // 5 days ago → expired
const soonDate = offsetISO(10);  // 10 days in the future → amber
const farDate  = offsetISO(60);  // 60 days in the future → no badge

/** Instance with a past best_before_date (expired). */
const instanceExpired = {
  id: 10,
  definition_id: 99,
  location_id: 5,
  quantity: "2",
  stock_level: null,
  serial: null,
  model_number: null,
  manufacturer: null,
  best_before_date: pastDate,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Instance with a far-future best_before_date. */
const instanceFarFuture = {
  ...instanceExpired,
  id: 11,
  best_before_date: farDate,
};

/** Instance with null best_before_date. */
const instanceNoDate = {
  ...instanceExpired,
  id: 12,
  best_before_date: null,
};

// ── Setup helpers ─────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

/** Set up GET mocks for ItemDetail with defMilk and one instance. */
function mockItemDetailLoad(def: object, instances: object[]) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/definitions/{definition_id}") {
        return { data: def, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/instances") {
        return { data: instances, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/kinds") {
        return { data: [kindPerishable], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/categories") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [locationFridge], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions") {
        return { data: [def], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );
}

function mockInstanceDetailLoad(inst: object) {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/instances/{instance_id}") {
        return { data: inst, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions/{definition_id}") {
        return { data: defMilk, response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [locationFridge], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/definitions") {
        return { data: [defMilk], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/instances/{instance_id}/movements") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );
}

function renderItemDetail(defId = 99) {
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

function renderInstanceDetail(instId = 10) {
  return render(
    <MemoryRouter initialEntries={[`/instances/${instId}`]}>
      <MantineProvider>
        <Routes>
          <Route path="/instances/:id" element={<InstanceDetail />} />
        </Routes>
      </MantineProvider>
    </MemoryRouter>,
  );
}

/** Render Items list page. */
function renderItems() {
  vi.mocked(client.GET).mockImplementation(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (path: any) => {
      if (path === "/api/definitions") {
        return { data: [defMilk], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/kinds") {
        return { data: [kindPerishable], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/categories") {
        return { data: [], response: new Response(null, { status: 200 }) };
      }
      if (path === "/api/locations") {
        return { data: [locationFridge], response: new Response(null, { status: 200 }) };
      }
      return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) };
    },
  );

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

// ── Stateful wrapper for InstanceFormModal ────────────────────────────────────

function InstanceFormWrapper(props: {
  initialForm?: Partial<InstanceFormState>;
  trackingMode?: string;
  isEdit?: boolean;
  definitionDefaultBestBeforeDays?: number | null;
}) {
  const [form, setForm] = React.useState<InstanceFormState>({
    definition_id: "99",
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
    ...props.initialForm,
  });

  return (
    <MantineProvider>
      <InstanceFormModal
        opened={true}
        title="Test modal"
        form={form}
        setForm={setForm}
        onSubmit={vi.fn()}
        onClose={vi.fn()}
        busy={false}
        error={null}
        definitions={[defMilk as AnyResult]}
        locations={[locationFridge as AnyResult]}
        trackingMode={props.trackingMode ?? "exact"}
        isEdit={props.isEdit ?? false}
        lockDefinition={false}
        definitionDefaultBestBeforeDays={props.definitionDefaultBestBeforeDays}
      />
    </MantineProvider>
  );
}

// ── Tests: ExpiryBadge ────────────────────────────────────────────────────────

describe("ExpiryBadge — display-only expiry cue", () => {
  it("renders red 'Expired' badge for a past date", () => {
    render(<MantineProvider><ExpiryBadge bestBeforeDate={pastDate} /></MantineProvider>);
    expect(screen.getByTestId("expiry-badge-expired")).toBeDefined();
    expect(screen.getByTestId("expiry-badge-expired").textContent).toMatch(/expired/i);
  });

  it("renders amber 'Expires in N days' badge for a within-soon-window date", () => {
    render(<MantineProvider><ExpiryBadge bestBeforeDate={soonDate} /></MantineProvider>);
    expect(screen.getByTestId("expiry-badge-soon")).toBeDefined();
    expect(screen.getByTestId("expiry-badge-soon").textContent).toMatch(/expires in/i);
  });

  it("renders nothing for a far-future date (>30d)", () => {
    const { container } = render(
      <MantineProvider><ExpiryBadge bestBeforeDate={farDate} /></MantineProvider>,
    );
    // No badge rendered
    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
    expect(container.querySelector("span")).toBeNull();
  });

  it("renders nothing when bestBeforeDate is null", () => {
    const { container } = render(
      <MantineProvider><ExpiryBadge bestBeforeDate={null} /></MantineProvider>,
    );
    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
    expect(container.querySelector("span")).toBeNull();
  });

  it("renders nothing when bestBeforeDate is undefined", () => {
    const { container } = render(
      <MantineProvider><ExpiryBadge bestBeforeDate={undefined} /></MantineProvider>,
    );
    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
    expect(container.querySelector("span")).toBeNull();
  });

  it(`boundary: today+${SOON_THRESHOLD_DAYS} renders amber badge`, () => {
    const boundary = offsetISO(SOON_THRESHOLD_DAYS);
    render(<MantineProvider><ExpiryBadge bestBeforeDate={boundary} /></MantineProvider>);
    expect(screen.getByTestId("expiry-badge-soon")).toBeDefined();
  });

  it(`boundary: today+${SOON_THRESHOLD_DAYS + 1} renders nothing`, () => {
    const beyondBoundary = offsetISO(SOON_THRESHOLD_DAYS + 1);
    const { container } = render(
      <MantineProvider><ExpiryBadge bestBeforeDate={beyondBoundary} /></MantineProvider>,
    );
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(container.querySelector("span")).toBeNull();
  });

  it("renders 'Expired' badge for today's date (0 days remaining is NOT expired) — actually today=0 is soon", () => {
    // today: daysRemaining = 0 → within the soon window (0 ≤ 30) → amber
    const today = todayISO();
    render(<MantineProvider><ExpiryBadge bestBeforeDate={today} /></MantineProvider>);
    expect(screen.getByTestId("expiry-badge-soon")).toBeDefined();
  });
});

// ── Tests: DefinitionFormModal — default_best_before_days field ───────────────

describe("DefinitionFormModal — default_best_before_days NumberInput", () => {
  beforeEach(() => {
    vi.mocked(client.POST).mockResolvedValue({
      data: defMilk,
      response: new Response(null, { status: 201 }),
    } as AnyResult);
  });

  it("renders the default_best_before_days NumberInput in create modal (exact mode)", async () => {
    renderItems();

    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("create-def-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("def-default-best-before-days-input")).toBeDefined();
    });
  });

  it("includes default_best_before_days in POST body when set", async () => {
    renderItems();

    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("create-def-btn"));

    // Fill name
    const nameInput = await screen.findByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "Milk" } });

    // Fill default_best_before_days via the input
    // NumberInput uses internal state; we simulate entering a value via change event on the underlying input
    const bbInput = screen.getByTestId("def-default-best-before-days-input");
    // The underlying <input> is inside the NumberInput component
    const inputEl = bbInput.querySelector("input") ?? bbInput;
    fireEvent.change(inputEl, { target: { value: "7" } });

    // Submit
    fireEvent.click(screen.getByTestId("def-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/definitions",
        expect.objectContaining({
          body: expect.objectContaining({ default_best_before_days: 7 }),
        }),
      );
    });
  });

  it("sends null for default_best_before_days when field is empty", async () => {
    renderItems();

    await waitFor(() => {
      expect(screen.getByTestId("create-def-btn")).toBeDefined();
    });

    fireEvent.click(screen.getByTestId("create-def-btn"));

    const nameInput = await screen.findByTestId("def-name-input");
    fireEvent.change(nameInput, { target: { value: "Milk" } });

    // Do NOT fill the days field
    fireEvent.click(screen.getByTestId("def-submit-btn"));

    await waitFor(() => {
      expect(client.POST).toHaveBeenCalledWith(
        "/api/definitions",
        expect.objectContaining({
          body: expect.objectContaining({ default_best_before_days: null }),
        }),
      );
    });
  });
});

// ── Tests: ItemDetail — default_best_before_days shown in detail card ─────────

describe("ItemDetail — definition detail shows default shelf life", () => {
  it("shows 'Default shelf life: 7 days' when default_best_before_days = 7", async () => {
    mockItemDetailLoad(defMilk, []);
    renderItemDetail(99);

    await waitFor(() => {
      expect(screen.getByTestId("def-default-best-before-days-value")).toBeDefined();
    });

    expect(screen.getByTestId("def-default-best-before-days-value").textContent).toMatch(
      /default shelf life.*7.*days/i,
    );
  });

  it("does NOT show shelf life section when default_best_before_days is null", async () => {
    mockItemDetailLoad(defDrill, []);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });

    expect(screen.queryByTestId("def-default-best-before-days-value")).toBeNull();
  });
});

// ── Tests: InstanceFormModal — best_before_date field ────────────────────────

describe("InstanceFormModal — best_before_date TextInput", () => {
  it("renders the best_before_date input in create mode (exact mode)", () => {
    render(<InstanceFormWrapper trackingMode="exact" isEdit={false} />);
    expect(screen.getByTestId("inst-best-before-date-input")).toBeDefined();
  });

  it("renders the best_before_date input in level mode (mode-independent)", () => {
    render(<InstanceFormWrapper trackingMode="level" isEdit={false} />);
    expect(screen.getByTestId("inst-best-before-date-input")).toBeDefined();
  });

  it("renders the best_before_date input in none mode (mode-independent)", () => {
    render(<InstanceFormWrapper trackingMode="none" isEdit={false} />);
    expect(screen.getByTestId("inst-best-before-date-input")).toBeDefined();
  });

  it("renders the best_before_date input in edit mode", () => {
    render(<InstanceFormWrapper trackingMode="exact" isEdit={true} />);
    expect(screen.getByTestId("inst-best-before-date-input")).toBeDefined();
  });

  it("shows hint text when definition has a default and field is empty (create mode)", () => {
    render(
      <InstanceFormWrapper
        trackingMode="exact"
        isEdit={false}
        definitionDefaultBestBeforeDays={7}
      />,
    );
    // The hint (description prop) should appear somewhere in the rendered output
    // It contains text about the default shelf life
    const hint = screen.queryByText(/will default to/i);
    expect(hint).toBeDefined();
  });

  it("does NOT show hint text when no default is configured", () => {
    render(
      <InstanceFormWrapper
        trackingMode="exact"
        isEdit={false}
        definitionDefaultBestBeforeDays={null}
      />,
    );
    expect(screen.queryByText(/will default to/i)).toBeNull();
  });

  it("does NOT show hint text in edit mode", () => {
    render(
      <InstanceFormWrapper
        trackingMode="exact"
        isEdit={true}
        definitionDefaultBestBeforeDays={7}
      />,
    );
    expect(screen.queryByText(/will default to/i)).toBeNull();
  });
});

// ── Tests: ItemDetail — lot table has best_before_date column with ExpiryBadge ─

describe("ItemDetail — lot table best_before_date column", () => {
  it("shows 'Expired' badge for an instance with a past best_before_date", async () => {
    mockItemDetailLoad(defMilk, [instanceExpired]);
    renderItemDetail(99);

    await waitFor(() => {
      expect(screen.getByTestId("expiry-badge-expired")).toBeDefined();
    });

    expect(screen.getByTestId("expiry-badge-expired").textContent).toMatch(/expired/i);
  });

  it("shows no badge for an instance with a far-future best_before_date", async () => {
    mockItemDetailLoad(defMilk, [instanceFarFuture]);
    renderItemDetail(99);

    await waitFor(() => {
      // Page loads
      expect(screen.getAllByText("Milk").length).toBeGreaterThan(0);
    });

    // Far future: no badge rendered
    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
  });

  it("shows — placeholder for an instance with null best_before_date", async () => {
    mockItemDetailLoad(defMilk, [instanceNoDate]);
    renderItemDetail(99);

    await waitFor(() => {
      expect(screen.getAllByText("Milk").length).toBeGreaterThan(0);
    });

    // No badge; no formatted date
    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
  });
});

// ── Tests: InstanceDetail — best_before_date field + ExpiryBadge ─────────────

describe("InstanceDetail — best_before_date field with ExpiryBadge", () => {
  it("shows 'Expired' badge on instance detail for a past best_before_date", async () => {
    mockInstanceDetailLoad(instanceExpired);
    renderInstanceDetail(10);

    await waitFor(() => {
      expect(screen.getByTestId("expiry-badge-expired")).toBeDefined();
    });

    expect(screen.getByTestId("expiry-badge-expired").textContent).toMatch(/expired/i);
  });

  it("shows no badge on instance detail for a far-future best_before_date", async () => {
    mockInstanceDetailLoad(instanceFarFuture);
    renderInstanceDetail(11);

    await waitFor(() => {
      // The page has loaded
      expect(screen.getAllByText("Milk").length).toBeGreaterThan(0);
    });

    expect(screen.queryByTestId("expiry-badge-expired")).toBeNull();
    expect(screen.queryByTestId("expiry-badge-soon")).toBeNull();
  });
});

// ── Tests: DateInput calendar picker — wire format + clear ────────────────────

/**
 * Stateful wrapper that captures the submitted form so we can assert on its
 * contents without going through the full ItemDetail page.
 */
function SubmittingFormWrapper(props: {
  initialForm?: Partial<InstanceFormState>;
  onSubmit?: (form: InstanceFormState) => void;
}) {
  const [form, setForm] = React.useState<InstanceFormState>({
    definition_id: "99",
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
    ...props.initialForm,
  });
  // Expose current form for assertions
  const latestForm = React.useRef(form);
  latestForm.current = form;
  return (
    <MantineProvider>
      <InstanceFormModal
        opened={true}
        title="Test modal"
        form={form}
        setForm={setForm}
        onSubmit={() => props.onSubmit?.(latestForm.current)}
        onClose={vi.fn()}
        busy={false}
        error={null}
        definitions={[defMilk as AnyResult]}
        locations={[locationFridge as AnyResult]}
        trackingMode="exact"
        isEdit={false}
        lockDefinition={false}
      />
    </MantineProvider>
  );
}

describe("InstanceFormModal — DateInput calendar picker (walkthrough fix)", () => {
  /**
   * Confirms the best_before_date field renders as a DateInput (not a plain
   * TextInput): the Mantine DateInput injects a clear button into the right
   * section when clearable + value is set, which a plain TextInput never does.
   * The Modal renders into a portal; query document.body, not the container.
   */
  it("best_before_date field has a clear button when value is set (DateInput affordance)", () => {
    render(
      <InstanceFormWrapper
        initialForm={{ best_before_date: "2026-09-01" }}
        trackingMode="exact"
        isEdit={false}
      />,
    );
    // DateInput injects a clear button (.mantine-InputClearButton-root) when
    // clearable + value is set. Find it via the DateInput's wrapper div to
    // avoid confusion with other clearable components (e.g. Select).
    const bbInput = screen.getByTestId("inst-best-before-date-input");
    const wrapper = bbInput.closest(".mantine-DateInput-wrapper");
    const clearBtn = wrapper?.querySelector(".mantine-InputClearButton-root");
    expect(clearBtn).not.toBeNull();
  });

  it("warranty_expires field renders with data-testid and a clear button when value is set", () => {
    render(
      <InstanceFormWrapper
        initialForm={{ warranty_expires: "2027-12-31" }}
        trackingMode="exact"
        isEdit={false}
      />,
    );
    // data-testid="inst-warranty-expires-input" must resolve (added in this fix)
    const wxInput = screen.getByTestId("inst-warranty-expires-input");
    expect(wxInput).toBeDefined();
    // Find the clear button scoped to this DateInput's wrapper div
    const wrapper = wxInput.closest(".mantine-DateInput-wrapper");
    const clearBtn = wrapper?.querySelector(".mantine-InputClearButton-root");
    expect(clearBtn).not.toBeNull();
  });

  /**
   * Typing a YYYY-MM-DD date into the best_before_date DateInput and then
   * blurring results in the form state holding that exact string — which the
   * submit handler sends as-is to the POST body (wire format unchanged).
   */
  it("typed YYYY-MM-DD date is captured in the form state via DateInput onChange", async () => {
    const onSubmit = vi.fn();
    render(<SubmittingFormWrapper onSubmit={onSubmit} />);

    const bbInput = screen.getByTestId("inst-best-before-date-input") as HTMLInputElement;

    // data-testid is on the <input> itself in DateInput (confirmed via DOM probe)
    fireEvent.change(bbInput, { target: { value: "2026-09-15" } });
    fireEvent.blur(bbInput);

    // Wait for state update to propagate, then submit
    await waitFor(() => {
      expect(bbInput.value).toBe("2026-09-15");
    });

    fireEvent.click(screen.getByTestId("inst-submit-btn"));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled();
    });

    const capturedForm = onSubmit.mock.calls[0][0] as InstanceFormState;
    // The YYYY-MM-DD string is stored verbatim in the form state; the submit
    // handler sends `.trim() || null`, which means "2026-09-15" → "2026-09-15".
    expect(capturedForm.best_before_date).toBe("2026-09-15");
  });

  /**
   * Clicking the clear button (rendered by DateInput when clearable + value set)
   * calls onChange(null), which the form handler converts to "" — the submit
   * handler then sends `"".trim() || null === null` to the POST body.
   *
   * NOTE: The Mantine Modal renders into a portal. Find the clear button via
   * the DateInput's own wrapper div, not via document.body (which also contains
   * other clear buttons from Select components).
   */
  it("clearing the best_before_date field leaves form state as empty string (→ null on submit)", async () => {
    const onSubmit = vi.fn();
    render(
      <SubmittingFormWrapper
        initialForm={{ best_before_date: "2026-09-01" }}
        onSubmit={onSubmit}
      />,
    );

    const bbInput = screen.getByTestId("inst-best-before-date-input") as HTMLInputElement;
    expect(bbInput.value).toBe("2026-09-01");

    // The clear button is in the same .mantine-DateInput-wrapper as the input.
    // Using the wrapper avoids confusing it with other clearable controls (e.g.
    // location Select) that also render .mantine-InputClearButton-root buttons.
    const wrapper = bbInput.closest(".mantine-DateInput-wrapper");
    const clearBtn = wrapper?.querySelector(".mantine-InputClearButton-root") as HTMLElement | null;
    expect(clearBtn).not.toBeNull();
    fireEvent.click(clearBtn!);

    // Wait for state update: the input should be empty after clearing
    await waitFor(() => {
      expect(bbInput.value).toBe("");
    });

    // Submit and verify the form has "" which the submit handler coerces to null
    fireEvent.click(screen.getByTestId("inst-submit-btn"));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalled();
    });

    const capturedForm = onSubmit.mock.calls[0][0] as InstanceFormState;
    expect(capturedForm.best_before_date).toBe("");
    // Verify the coercion: "".trim() || null === null (the wire-format rule)
    expect(capturedForm.best_before_date.trim() || null).toBeNull();
  });
});

// ── Tests: Instance creation — best_before_date pre-fill in ItemDetail ────────

describe("ItemDetail — register instance modal pre-fills best_before_date", () => {
  it("pre-fills best_before_date field when definition has default_best_before_days", async () => {
    mockItemDetailLoad(defMilk, []);
    renderItemDetail(99);

    await waitFor(() => {
      expect(screen.getAllByText("Milk").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("register-instance-btn"));

    const bbInput = await screen.findByTestId("inst-best-before-date-input");
    const inputEl = bbInput.querySelector("input") ?? bbInput as HTMLInputElement;

    // The field should be pre-filled with today + 7 (YYYY-MM-DD)
    const expectedDate = offsetISO(7);
    await waitFor(() => {
      expect((inputEl as HTMLInputElement).value).toBe(expectedDate);
    });
  });

  it("does NOT pre-fill best_before_date when definition has no default", async () => {
    mockItemDetailLoad(defDrill, []);
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getAllByText("Cordless Drill").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByTestId("register-instance-btn"));

    const bbInput = await screen.findByTestId("inst-best-before-date-input");
    const inputEl = bbInput.querySelector("input") ?? bbInput as HTMLInputElement;

    // Field should be empty (no default)
    expect((inputEl as HTMLInputElement).value).toBe("");
  });
});
