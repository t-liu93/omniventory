/**
 * Typed API client.
 *
 * Wraps ``openapi-fetch`` with the generated ``paths`` type from
 * ``./schema.d.ts`` (GENERATED — do not edit that file directly; run
 * ``make codegen`` to regenerate after backend API changes).
 *
 * All API calls through this client are automatically typed for
 * path, parameters, request body, and response body.
 */
import createClient from "openapi-fetch";

import type { paths } from "./schema";

/**
 * The application API client.
 *
 * - ``baseUrl: '/api'`` — all paths in ``schema.d.ts`` are relative to this
 *   prefix (e.g. ``/api/auth/login``).
 * - ``credentials: 'include'`` — ensures the session cookie is sent with
 *   every request (required for the ``HttpOnly`` session-cookie auth).
 */
export const client = createClient<paths>({
  baseUrl: "/api",
  credentials: "include",
});
