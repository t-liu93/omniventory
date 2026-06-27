/**
 * Export utility functions — kept in a separate module so that
 * ExportMenu.tsx satisfies the react-refresh/only-export-components lint rule.
 */

export type ExportEntity = "item_definitions" | "stock_instances" | "locations";
export type ExportFormat = "csv" | "json";

/**
 * Build the download URL for the given entity and format.
 *
 * Pure function — no side effects, fully testable without a DOM.
 *
 * @example exportUrl("item_definitions", "csv")
 *          // → "/api/export/item_definitions?format=csv"
 */
export function exportUrl(entity: ExportEntity, format: ExportFormat): string {
  return `/api/export/${entity}?format=${format}`;
}

/**
 * Trigger a browser file download for `url` by appending a temporary <a>
 * element with the `download` attribute to the document body, clicking it
 * programmatically, then immediately removing it.
 *
 * Because the request is same-origin the browser attaches the session cookie
 * automatically, so no Authorization header is required.
 */
export function triggerDownload(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.setAttribute("download", "");
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
