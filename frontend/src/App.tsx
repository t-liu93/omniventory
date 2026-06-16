/**
 * App root.
 *
 * Auth-gate approach (lean; M0 design preserved):
 *   1. On mount, call GET /api/auth/setup-status.
 *      - setup_required: true  → show Setup page (first-run onboarding).
 *   2. If setup is not required, call GET /api/auth/me.
 *      - 200  → show the authenticated AppShell (with routing inside).
 *      - 401  → show the Login page.
 *   3. Loading state while resolving.
 *
 * After setup success → transition to Login (anon state; user must log in).
 * Session is 100% cookie-based.  Nothing auth-related is stored in
 * localStorage or sessionStorage.
 *
 * Routing (added in M1 Step 5):
 *   - BrowserRouter is mounted INSIDE the authenticated shell — the auth gate
 *     above is the OUTER shell; routing is INNER (per M1 §2 locked decision).
 *   - Routes: / (Dashboard), /locations, /categories.
 *   - Definition/instance routes (/items, /items/:id, /instances/:id) land in Step 6.
 */
import { useEffect, useState } from "react";
import { LoadingOverlay, Box } from "@mantine/core";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { Login } from "./pages/Login";
import { Setup } from "./pages/Setup";
import { Dashboard } from "./pages/Dashboard";
import { Locations } from "./pages/Locations";
import { Categories } from "./pages/Categories";
import { client } from "./api/client";

type AuthState = "loading" | "setup" | "authed" | "anon";

function App() {
  const [authState, setAuthState] = useState<AuthState>("loading");

  useEffect(() => {
    async function checkState() {
      // Step 1: check if first-run setup is required.
      const { data: setupData, error: setupError } = await client.GET(
        "/api/auth/setup-status",
      );
      if (setupError || !setupData) {
        // If setup-status fails for any reason, fall through to auth check.
        // (Shouldn't happen in normal operation.)
      } else if (setupData.setup_required) {
        setAuthState("setup");
        return;
      }

      // Step 2: check if user is already authenticated.
      const { error: meError } = await client.GET("/api/auth/me");
      setAuthState(meError ? "anon" : "authed");
    }

    checkState().catch(() => setAuthState("anon"));
  }, []);

  if (authState === "loading") {
    return (
      <Box pos="relative" h="100dvh">
        <LoadingOverlay visible />
      </Box>
    );
  }

  if (authState === "setup") {
    // After setup, go to login (do NOT auto-login).
    return <Setup onSuccess={() => setAuthState("anon")} />;
  }

  if (authState === "anon") {
    return <Login onSuccess={() => setAuthState("authed")} />;
  }

  // Authenticated: mount BrowserRouter INSIDE the auth gate (§2 locked decision).
  return (
    <BrowserRouter>
      <AppShell onLogout={() => setAuthState("anon")}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/locations" element={<Locations />} />
          <Route path="/categories" element={<Categories />} />
          {/* Step 6 will add: /items, /items/:id, /instances/:id */}
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}

export default App;
