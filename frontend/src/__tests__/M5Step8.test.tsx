/**
 * M5 Step 8 — Attachment panel (upload / gallery / delete).
 *
 * Coverage (per M5 §5 frontend / §9 Step 8 / §10 Step 8):
 *
 * 1. Upload:
 *    a. Selecting a file → POST /api/attachments with FormData containing
 *       model_type, model_id, and file fields.
 *    b. Gallery refreshes after a successful upload.
 *
 * 2. Gallery render:
 *    a. Image attachment → <img> with src == media.url and loading="lazy".
 *    b. Non-image attachment (PDF) → no <img>; download link to media.url.
 *
 * 3. Delete:
 *    a. Clicking delete button → shows confirm modal.
 *    b. Confirming delete → DELETE /api/attachments/{id}; item removed from list.
 *
 * 4. Caption edit:
 *    a. Clicking caption text → shows inline TextInput.
 *    b. Saving → PATCH /api/attachments/{id} with correct title.
 *
 * 5. i18n:
 *    a. attachments namespace keys present in en and zh (parity check).
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
import { MemoryRouter } from "react-router-dom";
import { AttachmentPanel } from "../components/AttachmentPanel.js";
import i18n from "../i18n/index.js";
import enAttachments from "../i18n/locales/en/attachments.json";
import zhAttachments from "../i18n/locales/zh/attachments.json";

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

const imageAttachment: AnyResult = {
  id: 1,
  model_type: "stock_instance",
  model_id: 42,
  title: "Front view",
  original_filename: "photo.jpg",
  sort_order: 0,
  created_at: "2026-06-27T00:00:00Z",
  uploaded_by: 1,
  media: {
    sha256: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    content_type: "image/jpeg",
    byte_size: 12345,
    width: 800,
    height: 600,
    url: "/media/ab/abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
  },
};

const pdfAttachment: AnyResult = {
  id: 2,
  model_type: "stock_instance",
  model_id: 42,
  title: null,
  original_filename: "receipt.pdf",
  sort_order: 1,
  created_at: "2026-06-27T00:00:00Z",
  uploaded_by: 1,
  media: {
    sha256: "deadbeef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    content_type: "application/pdf",
    byte_size: 54321,
    width: null,
    height: null,
    url: "/media/de/deadbeef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPanel(
  modelType: "stock_instance" | "item_definition" | "location" = "stock_instance",
  modelId = 42,
) {
  return render(
    <MemoryRouter>
      <MantineProvider>
        <AttachmentPanel modelType={modelType} modelId={modelId} />
      </MantineProvider>
    </MemoryRouter>,
  );
}

function mockGetAttachments(list: AnyResult[]) {
  vi.mocked(client.GET).mockImplementation(async () => ({
    data: list,
    response: new Response(null, { status: 200 }),
  }));
}

// ── Tests: gallery render ─────────────────────────────────────────────────────

describe("AttachmentPanel — gallery render", () => {
  it("image attachment renders <img> with media.url as src and loading=lazy", async () => {
    mockGetAttachments([imageAttachment]);

    await act(async () => {
      renderPanel();
    });

    const img = await screen.findByTestId("attachment-img-1");
    expect(img.tagName).toBe("IMG");
    expect((img as HTMLImageElement).src).toContain(imageAttachment.media.url);
    expect((img as HTMLImageElement).getAttribute("loading")).toBe("lazy");
  });

  it("image attachment does NOT render a download link (it's an <img>)", async () => {
    mockGetAttachments([imageAttachment]);

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("attachment-img-1");
    expect(screen.queryByTestId("attachment-download-1")).toBeNull();
  });

  it("non-image attachment renders a download link to media.url (not an <img>)", async () => {
    mockGetAttachments([pdfAttachment]);

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("attachment-download-2");
    const link = screen.getByTestId("attachment-download-2") as HTMLAnchorElement;
    expect(link.href).toContain(pdfAttachment.media.url);
    // No <img> for a PDF
    expect(screen.queryByTestId("attachment-img-2")).toBeNull();
  });

  it("non-image attachment renders file icon section", async () => {
    mockGetAttachments([pdfAttachment]);

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("attachment-file-2");
  });

  it("shows empty state when no attachments", async () => {
    mockGetAttachments([]);

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("attachment-empty");
    expect(screen.getByTestId("attachment-empty").textContent).toContain(
      "No attachments yet",
    );
  });
});

// ── Tests: upload ─────────────────────────────────────────────────────────────

describe("AttachmentPanel — upload", () => {
  it("selecting a file calls POST /api/attachments with FormData containing model_type / model_id / file", async () => {
    // Initial load: empty list; after upload, return image attachment.
    let getCallCount = 0;
    vi.mocked(client.GET).mockImplementation(async () => {
      getCallCount++;
      // First load empty, second load (after upload) returns image.
      const list = getCallCount === 1 ? [] : [imageAttachment];
      return { data: list, response: new Response(null, { status: 200 }) };
    });

    let capturedPostArgs: AnyResult = null;
    vi.mocked(client.POST).mockImplementation(async (path, opts) => {
      capturedPostArgs = { path, opts };
      return {
        data: imageAttachment,
        response: new Response(null, { status: 201 }),
      };
    });

    await act(async () => {
      renderPanel("stock_instance", 42);
    });

    // Wait for initial load
    await screen.findByTestId("attachment-empty");

    // Find the hidden <input type="file"> rendered by Mantine's FileButton
    const fileInput = document.querySelector(
      "input[type=file]",
    ) as HTMLInputElement;
    expect(fileInput).not.toBeNull();

    // Create a test file and simulate selection
    const file = new File(["image content"], "photo.jpg", {
      type: "image/jpeg",
    });

    // Set the files property (read-only in jsdom; use Object.defineProperty)
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: (i: number) => (i === 0 ? file : null) },
      configurable: true,
    });

    await act(async () => {
      fireEvent.change(fileInput);
    });

    // POST must have been called with the right path
    await waitFor(() => {
      expect(capturedPostArgs).not.toBeNull();
    });

    expect(capturedPostArgs.path).toBe("/api/attachments");

    // body should be a FormData with the correct fields
    const body = capturedPostArgs.opts?.body as FormData;
    expect(body instanceof FormData).toBe(true);
    expect(body.get("model_type")).toBe("stock_instance");
    expect(body.get("model_id")).toBe("42");
    expect(body.get("file")).toBe(file);
  });

  it("gallery refreshes after a successful upload", async () => {
    let getCallCount = 0;
    vi.mocked(client.GET).mockImplementation(async () => {
      getCallCount++;
      const list = getCallCount === 1 ? [] : [imageAttachment];
      return { data: list, response: new Response(null, { status: 200 }) };
    });

    vi.mocked(client.POST).mockImplementation(async () => ({
      data: imageAttachment,
      response: new Response(null, { status: 201 }),
    }));

    await act(async () => {
      renderPanel("stock_instance", 42);
    });

    await screen.findByTestId("attachment-empty");

    const fileInput = document.querySelector(
      "input[type=file]",
    ) as HTMLInputElement;
    const file = new File(["content"], "photo.jpg", { type: "image/jpeg" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: (i: number) => (i === 0 ? file : null) },
      configurable: true,
    });

    await act(async () => {
      fireEvent.change(fileInput);
    });

    // After upload, gallery should show the image
    await screen.findByTestId("attachment-img-1");
  });
});

// ── Tests: delete ─────────────────────────────────────────────────────────────

describe("AttachmentPanel — delete", () => {
  it("clicking delete button opens confirm modal", async () => {
    mockGetAttachments([imageAttachment]);

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("attachment-delete-btn-1");

    await act(async () => {
      fireEvent.click(screen.getByTestId("attachment-delete-btn-1"));
    });

    await screen.findByTestId("confirm-delete-attachment-btn");
    expect(screen.getByText("Are you sure you want to delete this attachment?")).toBeDefined();
  });

  it("confirming delete calls DELETE /api/attachments/{id} and removes item from gallery", async () => {
    // First GET returns the attachment; after DELETE, second GET returns empty.
    let getCallCount = 0;
    vi.mocked(client.GET).mockImplementation(async () => {
      getCallCount++;
      const list = getCallCount === 1 ? [imageAttachment] : [];
      return { data: list, response: new Response(null, { status: 200 }) };
    });

    let deletePath: AnyResult = null;
    let deleteOpts: AnyResult = null;
    vi.mocked(client.DELETE).mockImplementation(async (path: AnyResult, opts: AnyResult) => {
      deletePath = path;
      deleteOpts = opts;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return { data: undefined, error: undefined, response: new Response(null, { status: 204 }) } as any;
    });

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("attachment-card-1");

    // Open confirm modal
    await act(async () => {
      fireEvent.click(screen.getByTestId("attachment-delete-btn-1"));
    });

    await screen.findByTestId("confirm-delete-attachment-btn");

    // Confirm
    await act(async () => {
      fireEvent.click(screen.getByTestId("confirm-delete-attachment-btn"));
    });

    await waitFor(() => {
      expect(deletePath).toBe("/api/attachments/{attachment_id}");
      expect(deleteOpts?.params?.path?.attachment_id).toBe(1);
    });

    // Gallery should now be empty
    await screen.findByTestId("attachment-empty");
  });
});

// ── Tests: caption edit ───────────────────────────────────────────────────────

describe("AttachmentPanel — caption edit", () => {
  it("clicking caption text shows inline TextInput", async () => {
    mockGetAttachments([imageAttachment]);

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("caption-text-1");

    await act(async () => {
      fireEvent.click(screen.getByTestId("caption-text-1"));
    });

    await screen.findByTestId("caption-input-1");
    expect(screen.getByTestId("caption-save-btn-1")).toBeDefined();
  });

  it("saving caption calls PATCH /api/attachments/{id} with the new title", async () => {
    let getCallCount = 0;
    vi.mocked(client.GET).mockImplementation(async () => {
      getCallCount++;
      return {
        data: [{ ...imageAttachment, title: getCallCount === 1 ? "Front view" : "New caption" }],
        response: new Response(null, { status: 200 }),
      };
    });

    let patchPath: AnyResult = null;
    let patchOpts: AnyResult = null;
    vi.mocked(client.PATCH).mockImplementation(async (path, opts) => {
      patchPath = path;
      patchOpts = opts;
      return { data: imageAttachment, response: new Response(null, { status: 200 }) };
    });

    await act(async () => {
      renderPanel();
    });

    await screen.findByTestId("caption-text-1");

    // Click to enter edit mode
    await act(async () => {
      fireEvent.click(screen.getByTestId("caption-text-1"));
    });

    await screen.findByTestId("caption-input-1");

    // Change the caption input
    const captionInput = screen.getByTestId("caption-input-1");
    const innerInput = (captionInput.querySelector("input") ?? captionInput) as HTMLInputElement;
    fireEvent.change(innerInput, { target: { value: "New caption" } });

    // Save
    await act(async () => {
      fireEvent.click(screen.getByTestId("caption-save-btn-1"));
    });

    await waitFor(() => {
      expect(patchPath).toBe("/api/attachments/{attachment_id}");
      expect(patchOpts?.params?.path?.attachment_id).toBe(1);
      expect(patchOpts?.body?.title).toBe("New caption");
    });
  });
});

// ── Tests: i18n catalog parity ────────────────────────────────────────────────

describe("attachments i18n — en+zh catalog parity", () => {
  /**
   * Recursively collect all leaf key paths from a nested object.
   */
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

  it("en and zh attachments namespace have identical key sets", () => {
    const enKeys = collectKeys(enAttachments).sort();
    const zhKeys = collectKeys(zhAttachments).sort();

    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const extraInZh = zhKeys.filter((k) => !enKeys.includes(k));

    expect(missingInZh, "Keys in en/attachments missing from zh/attachments").toEqual([]);
    expect(extraInZh, "Extra keys in zh/attachments not present in en/attachments").toEqual([]);
  });

  it("attachments.sectionTitle is 'Attachments' in en", () => {
    expect(i18n.t("sectionTitle", { ns: "attachments" })).toBe("Attachments");
  });

  it("attachments.sectionTitle is translated in zh", async () => {
    await i18n.changeLanguage("zh");
    const value = i18n.t("sectionTitle", { ns: "attachments" });
    expect(value).not.toBe("Attachments");
    expect(value.trim().length).toBeGreaterThan(0);
    await i18n.changeLanguage("en");
  });

  it("attachments.emptyState is present in en", () => {
    const value = i18n.t("emptyState", { ns: "attachments" });
    expect(value.trim().length).toBeGreaterThan(0);
    expect(value).not.toBe("emptyState");
  });

  it("attachments.deleteConfirm.title is present in en", () => {
    const value = i18n.t("deleteConfirm.title", { ns: "attachments" });
    expect(value.trim().length).toBeGreaterThan(0);
    expect(value).not.toBe("deleteConfirm.title");
  });
});
