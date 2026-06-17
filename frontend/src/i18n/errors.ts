/**
 * Error localization for Omniventory (M1.5 Step 5).
 *
 * `mapApiError` converts a backend `ErrorResponse` (or any unknown value that
 * may arrive from a network failure / unexpected shape) into a localized,
 * user-facing string.
 *
 * Design:
 *  - The backend `ErrorResponse` shape (as typed by openapi-fetch) has
 *    `{ code: string, message: string, params?: Record<string, unknown> | null }`.
 *  - `code` is the stable FE↔BE contract key.  If the `errors` namespace
 *    carries a translation for it, that translation is used (with `params`
 *    interpolated so dynamic values like `{{id}}` expand correctly).
 *  - If the code is missing / unknown we fall back to `errors.generic`.
 *  - In DEV mode the raw `message` is appended to the generic fallback to
 *    assist debugging without leaking it to production users.
 *  - Non-object values and missing properties are handled defensively — this
 *    function must never throw.
 *
 * Live language:
 *  We call `i18n.t` at call time (not captured at module load) so the
 *  function always localizes in whichever language is currently active.
 *  Changing the language with `i18n.changeLanguage` is therefore reflected
 *  immediately on the next `mapApiError` call.
 */

import i18n from ".";

/**
 * Convert any API error value to a localized user-facing message.
 *
 * @param error  The `error` branch from an openapi-fetch result, or any
 *               unknown value (network failure, thrown exception, etc.).
 * @returns      A localized string safe to display to the user.
 */
export function mapApiError(error: unknown): string {
  // Defensive extraction — the value may be null, a string, or a non-standard
  // object from a network-level failure.
  if (!error || typeof error !== "object") {
    return i18n.t("generic", { ns: "errors" });
  }

  const err = error as Record<string, unknown>;
  const code = typeof err["code"] === "string" ? err["code"] : null;
  const params =
    err["params"] != null && typeof err["params"] === "object"
      ? (err["params"] as Record<string, unknown>)
      : undefined;
  const message = typeof err["message"] === "string" ? err["message"] : null;

  if (!code) {
    return _generic(message);
  }

  // Localise any entity-kind word before interpolation, so zh templates like
  // "在{{kind}}树中" receive "位置" rather than the raw English "location".
  // Falls back to the original value when the kind is not in common.entities.
  const localizedParams = params ? localizeParams(params) : undefined;

  // Guard against two miss-cases:
  //   1. Key genuinely absent → i18n.exists returns false.
  //   2. Code maps to a namespace *object* (e.g. "auth", "location") rather
  //      than a leaf string — i18n.exists returns true (objects ARE "found"),
  //      but i18n.t (with returnObjects:true) returns the object, not a string.
  //      With returnObjects:false (default), i18next instead emits a diagnostic
  //      like "key 'auth (en)' returned an object instead of string." — that
  //      must never reach the user.
  //
  // Two-step fix:
  //   a) i18n.exists rejects absent keys (missing leaf or missing namespace).
  //   b) returnObjects:true lets us detect object-type values (namespace hits)
  //      via typeof — only proceed if the raw result is a plain string.
  const i18nKey = `errors:${code}`;
  if (!i18n.exists(i18nKey)) {
    return _generic(message);
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rawValue = (i18n.t as any)(i18nKey, { ...(localizedParams ?? {}), returnObjects: true });
  if (typeof rawValue !== "string") {
    return _generic(message);
  }

  return rawValue;
}

/**
 * Localize any `kind` parameter (and future entity-kind params) so that zh
 * templates receive the translated word rather than the raw English backend value.
 *
 * Backend sends `params.kind` as "location" | "category" (English identifiers).
 * This maps them to `common:entities.<kind>` when a translation exists, and
 * leaves any other param values untouched.
 */
function localizeParams(
  params: Record<string, unknown>,
): Record<string, unknown> {
  const result = { ...params };
  if (typeof result["kind"] === "string") {
    const kindKey = `common:entities.${result["kind"]}`;
    if (i18n.exists(kindKey)) {
      result["kind"] = i18n.t(kindKey);
    }
  }
  return result;
}

/** Build the generic fallback message, appending raw message in DEV mode. */
function _generic(message: string | null): string {
  const base = i18n.t("generic", { ns: "errors" });
  if (import.meta.env.DEV && message) {
    return `${base} (${message})`;
  }
  return base;
}
