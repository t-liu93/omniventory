/**
 * Tests for the formatQuantity helper and its usage at display sites.
 *
 * Unit tests cover the helper's trimming logic across documented cases.
 * Component tests assert that quantity display sites render trimmed values.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { formatQuantity } from "../i18n/format.js";
import { ItemDetail } from "../pages/Items.js";
import { InstanceDetail } from "../pages/InstanceDetail.js";

// ── Unit tests for formatQuantity ─────────────────────────────────────────────

describe("formatQuantity — unit tests", () => {
  it("trims all trailing zeros and decimal point: '1.000000' → '1'", () => {
    expect(formatQuantity("1.000000")).toBe("1");
  });

  it("trims trailing zeros but keeps significant fraction: '1.200000' → '1.2'", () => {
    expect(formatQuantity("1.200000")).toBe("1.2");
  });

  it("trims trailing zeros with two significant fraction digits: '1.210000' → '1.21'", () => {
    expect(formatQuantity("1.210000")).toBe("1.21");
  });

  it("leaves integer string unchanged: '5' → '5'", () => {
    expect(formatQuantity("5")).toBe("5");
  });

  it("leaves decimal fraction unchanged when already clean: '0.5' → '0.5'", () => {
    expect(formatQuantity("0.5")).toBe("0.5");
  });

  it("accepts a number input: 5 → '5'", () => {
    expect(formatQuantity(5)).toBe("5");
  });

  it("accepts a number with fraction: 1.2 → '1.2'", () => {
    expect(formatQuantity(1.2)).toBe("1.2");
  });

  it("leaves already-trimmed value unchanged: '1.21' → '1.21'", () => {
    expect(formatQuantity("1.21")).toBe("1.21");
  });

  it("handles empty string gracefully — returns original", () => {
    expect(formatQuantity("")).toBe("");
  });

  it("handles a value with no fractional digits unchanged: '42' → '42'", () => {
    expect(formatQuantity("42")).toBe("42");
  });

  it("handles a value that is purely zeros after decimal: '0.000' → '0'", () => {
    expect(formatQuantity("0.000")).toBe("0");
  });

  // Integer-ending-in-zero regression lock: a naive /0+$/ without the
  // str.includes(".") guard would corrupt "10" → "1", "100" → "1", etc.
  // These cases verify that integer magnitude is never corrupted.
  it("leaves integer-ending-in-zero unchanged: '10' → '10'", () => {
    expect(formatQuantity("10")).toBe("10");
  });

  it("leaves hundred unchanged: '100' → '100'", () => {
    expect(formatQuantity("100")).toBe("100");
  });

  it("leaves mixed-digit integer ending in zero unchanged: '150' → '150'", () => {
    expect(formatQuantity("150")).toBe("150");
  });

  it("trims trailing zeros from Decimal-string integer ten: '20.000000' → '20'", () => {
    expect(formatQuantity("20.000000")).toBe("20");
  });
});

// ── Component render tests ────────────────────────────────────────────────────

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

const kindDurable = {
  id: 1,
  code: "durable",
  name: "Durable",
  is_system: true,
  created_at: "2025-01-01T00:00:00Z",
};

const defDrill = {
  id: 42,
  name: "Cordless Drill",
  description: null,
  category_id: null,
  kind_id: 1,
  kind: kindDurable,
  unit: "pcs",
  default_location_id: null,
  created_at: "2025-01-01T00:00:00Z",
};

const locationGarage = {
  id: 1,
  name: "Garage",
  description: null,
  parent_id: null,
  item_instance_id: null,
  container_asset_label: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Instance with a Decimal quantity string that has trailing zeros. */
const instanceWithTrailingZeros = {
  id: 1,
  definition_id: 42,
  location_id: 1,
  quantity: "1.000000",
  serial: null,
  model_number: null,
  manufacturer: null,
  warranty_expires: null,
  warranty_details: null,
  purchase_price: null,
  purchase_date: null,
  purchase_source: null,
  created_at: "2025-01-01T00:00:00Z",
};

/** Instance fixture with non-trivial fraction quantity. */
const instanceWithFraction = {
  ...instanceWithTrailingZeros,
  id: 2,
  quantity: "1.200000",
};

// ── ItemDetail instance list: quantity column shows trimmed value ──────────────

function renderItemDetail(defId: number) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/definitions/{definition_id}") {
      return { data: defDrill, response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/instances") {
      return { data: [instanceWithTrailingZeros, instanceWithFraction], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/kinds") {
      return { data: [kindDurable], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/categories") {
      return { data: [], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/locations") {
      return { data: [locationGarage], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/definitions") {
      return { data: [defDrill], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) } as AnyResult;
  });

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

describe("ItemDetail — quantity display in instance list", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("displays '1' (not '1.000000') in the quantity column", async () => {
    renderItemDetail(42);

    await waitFor(() => {
      // Positive assertion: the trimmed value "1" is rendered in instance row 1.
      // Scoped to inst-row-1 to avoid false positives from other numeric text.
      const row1 = screen.getByTestId("inst-row-1");
      expect(within(row1).getByText("1")).toBeDefined();
      // Negative assertion: the raw wire format must not appear anywhere.
      expect(screen.queryByText("1.000000")).toBeNull();
    });
  });

  it("displays '1.2' (not '1.200000') in the quantity column", async () => {
    renderItemDetail(42);

    await waitFor(() => {
      expect(screen.getByText("1.2")).toBeDefined();
      expect(screen.queryByText("1.200000")).toBeNull();
    });
  });
});

// ── InstanceDetail page: quantity detail field shows trimmed value ─────────────

function renderInstanceDetail(instId = 1, instanceFixture = instanceWithTrailingZeros) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vi.mocked(client.GET).mockImplementation(async (path: any) => {
    if (path === "/api/instances/{instance_id}") {
      return { data: instanceFixture, response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/definitions/{definition_id}") {
      return { data: defDrill, response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/locations") {
      return { data: [locationGarage], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    if (path === "/api/definitions") {
      return { data: [defDrill], response: new Response(null, { status: 200 }) } as AnyResult;
    }
    return { data: null, error: { detail: "Not found" }, response: new Response(null, { status: 404 }) } as AnyResult;
  });

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

describe("InstanceDetail — quantity detail field shows trimmed value", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows '1' rather than '1.000000' in the Quantity detail field", async () => {
    renderInstanceDetail(1);

    await waitFor(() => {
      // The "Quantity" label should be present
      expect(screen.getByText("Quantity")).toBeDefined();
    });

    // The raw "1.000000" must not appear anywhere in the rendered output
    expect(screen.queryByText("1.000000")).toBeNull();
  });

  it("positively renders trimmed quantity '1.2' (not '1.200000') in the Quantity detail field", async () => {
    // Use an unambiguous fraction fixture so the positive assertion is precise.
    renderInstanceDetail(2, instanceWithFraction);

    await waitFor(() => {
      // Positive assertion: the trimmed "1.2" value is rendered.
      expect(screen.getByText("1.2")).toBeDefined();
    });

    // Negative assertion: the raw wire format must not appear.
    expect(screen.queryByText("1.200000")).toBeNull();
  });
});
