import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      /** Precache the app shell (entry points + assets). */
      workbox: {
        globPatterns: ["**/*.{js,css,html,png,svg,ico}"],
        // Purge stale Workbox precaches when a new service worker activates.
        // skipWaiting and clientsClaim are already injected by vite-plugin-pwa
        // when registerType is 'autoUpdate', so we do not repeat them here.
        cleanupOutdatedCaches: true,
      },
      manifest: {
        name: "Omniventory",
        short_name: "Omniventory",
        description: "Self-hosted inventory management",
        /**
         * Theme color matches our primary teal (#0d9488 = Mantine teal[8]).
         * Also set in index.html <meta name="theme-color">.
         */
        theme_color: "#0d9488",
        background_color: "#ffffff",
        display: "standalone",
        icons: [
          {
            src: "/icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },
    }),
  ],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/__tests__/setup.ts"],
  },
});
