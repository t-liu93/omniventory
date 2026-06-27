/**
 * Service-worker update poller + reload trigger.
 *
 * vite-plugin-pwa with registerType:'autoUpdate' handles SW registration (via
 * a bare script injected into index.html at build time) and injects
 * skipWaiting + clientsClaim into the generated sw.js.  When a new SW is
 * found, skipWaiting activates it immediately and clientsClaim makes it the
 * controller of all open tabs.
 *
 * However, clientsClaim does NOT reload existing documents — already-open tabs
 * continue running the old JS/HTML until a manual hard-refresh.  Two gaps must
 * be filled here:
 *
 * Gap 1 — Proactive update discovery.
 *   Browsers defer the SW update check to the next navigation and throttle it
 *   to at most once per 24 h in a long-lived tab.  Without an explicit call to
 *   registration.update() the user stays on the old build indefinitely.
 *   Fix: call registration.update() at three points:
 *     1. Immediately on first page load (after the SW controls the page).
 *     2. Every 60 minutes, for long-lived tabs.
 *     3. Whenever the user returns to the tab (visibilitychange / focus).
 *
 * Gap 2 — Reload the page once the new SW takes control.
 *   After skipWaiting + clientsClaim, the browser fires a 'controllerchange'
 *   event on navigator.serviceWorker.  We listen for that event and call
 *   window.location.reload() so the user sees the new content immediately.
 *   Guards:
 *     - `hadController`: only reload when a SW was *already* controlling this
 *       page at startup (i.e. this is a returning visit, not a first install).
 *       On the very first install, clientsClaim also fires controllerchange,
 *       but there is no stale content to replace — reloading would cause an
 *       unnecessary flash.
 *     - `refreshing`: a module-level flag ensures reload() is called at most
 *       once even if controllerchange fires multiple times.
 *
 * End-to-end flow: deploy new build → poller calls registration.update() →
 * new SW installs and skipWaiting activates it → clientsClaim → controller-
 * change fires → this handler calls reload() → user sees new content without
 * any manual hard-refresh.
 *
 * Using the raw navigator.serviceWorker API (rather than importing from
 * virtual:pwa-register) avoids pulling workbox-window into the main JS chunk.
 *
 * This function is a no-op in environments where serviceWorker is unavailable
 * (vitest/jsdom, non-HTTPS contexts, older browsers).
 */

/** How often to poll for a new service worker in a long-lived tab (ms). */
const UPDATE_INTERVAL_MS = 60 * 60 * 1000; // 60 minutes

/**
 * Wire up proactive SW update checks.
 * Call once at app startup (main.tsx) after rendering the React tree.
 */
export function initServiceWorker(): void {
  // Guard: jsdom (vitest), non-HTTPS, or browsers without SW support.
  if (!("serviceWorker" in navigator)) {
    return;
  }

  // Capture whether a SW was already controlling this page at startup.
  // On a first-ever install, clientsClaim() also fires 'controllerchange',
  // but we must NOT reload then — there is no stale content and doing so would
  // cause an unwanted flash.  Only reload on genuine updates (returning visits
  // where a SW was already in control before this page load).
  const hadController = navigator.serviceWorker.controller !== null;

  // Prevent reload() from being called more than once if controllerchange
  // fires multiple times (e.g. rapid successive updates).
  let refreshing = false;

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (refreshing || !hadController) return;
    refreshing = true;
    window.location.reload();
  });

  // navigator.serviceWorker.ready resolves with the registration once the SW
  // is installed and actively controlling this page.
  navigator.serviceWorker.ready
    .then((registration: ServiceWorkerRegistration) => {
      const checkForUpdate = () => {
        registration.update().catch(() => {});
      };

      // 1. Check immediately on load.
      checkForUpdate();

      // 2. Periodic background check while the tab stays open.
      setInterval(checkForUpdate, UPDATE_INTERVAL_MS);

      // 3. Re-check whenever the user returns to the tab.
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          checkForUpdate();
        }
      });
      window.addEventListener("focus", checkForUpdate);
    })
    .catch(() => {
      // SW not available or denied — silently ignore.
    });
}
