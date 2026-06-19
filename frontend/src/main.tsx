/**
 * Application entry point.
 *
 * Wraps the React tree in:
 *   - i18n singleton (initializes synchronously on import; must come first)
 *   - MantineProvider (theme tokens from theme.ts)
 *   - ColorSchemeScript (prevents flash-of-wrong-theme on load)
 *   - I18nextProvider (provides the i18n instance to the React tree)
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider, ColorSchemeScript } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { I18nextProvider } from "react-i18next";

// i18n must be initialized before the React tree renders.
// The module is synchronous (bundled resources, initAsync: false).
import i18n from "./i18n";

// Mantine core styles — must come before component styles
import "@mantine/core/styles.css";
// Dates styles — date picker component styles
import "@mantine/dates/styles.css";
// Notifications styles — must come after core styles
import "@mantine/notifications/styles.css";

import { theme } from "./theme";
import App from "./App";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    {/*
     * ColorSchemeScript must render in <head> in production, but here inside
     * <body> it still sets the data-mantine-color-scheme attribute before
     * hydration to avoid a flash.  For Vite SPA we inject it just before the
     * React tree; the effect is the same.
     */}
    <ColorSchemeScript defaultColorScheme="auto" />
    <I18nextProvider i18n={i18n}>
      <MantineProvider theme={theme} defaultColorScheme="auto">
        <Notifications position="top-center" />
        <App />
      </MantineProvider>
    </I18nextProvider>
  </StrictMode>,
);
