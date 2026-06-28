/**
 * i18next initialization — singleton.
 *
 * Import this module (side-effectful) before rendering the React tree:
 *   import './i18n';
 *
 * Init is **synchronous** because all resources are bundled inline
 * (no http backend).  `initAsync: false` ensures i18next.t() is ready
 * immediately after the module loads.
 *
 * Detection chain (pre-login): localStorage → navigator → fallback 'en'.
 * The localStorage key is 'omniventory_lang'.
 *
 * `load: 'languageOnly'` collapses zh-CN / zh-TW → zh automatically
 * before any lookup, complementing normalizeLanguage().
 *
 * <html lang> is kept in sync via the 'languageChanged' event.
 */

import i18next from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";
import { normalizeLanguage } from "./languages";

// Locale bundles — en
import enCommon from "./locales/en/common.json";
import enAuth from "./locales/en/auth.json";
import enNav from "./locales/en/nav.json";
import enLocations from "./locales/en/locations.json";
import enCategories from "./locales/en/categories.json";
import enItems from "./locales/en/items.json";
import enInstances from "./locales/en/instances.json";
import enErrors from "./locales/en/errors.json";
import enDashboard from "./locales/en/dashboard.json";
import enStock from "./locales/en/stock.json";
import enExpiry from "./locales/en/expiry.json";
import enNotifications from "./locales/en/notifications.json";
import enConfiguration from "./locales/en/configuration.json";
import enAttachments from "./locales/en/attachments.json";
import enTags from "./locales/en/tags.json";
import enNotes from "./locales/en/notes.json";
import enCustomFields from "./locales/en/customFields.json";
import enBarcode from "./locales/en/barcode.json";
import enSearch from "./locales/en/search.json";
import enExport from "./locales/en/export.json";
import enRoles from "./locales/en/roles.json";
import enUsers from "./locales/en/users.json";
import enInvitations from "./locales/en/invitations.json";
import enAccount from "./locales/en/account.json";
import enResponsible from "./locales/en/responsible.json";

// Locale bundles — zh
import zhCommon from "./locales/zh/common.json";
import zhAuth from "./locales/zh/auth.json";
import zhNav from "./locales/zh/nav.json";
import zhLocations from "./locales/zh/locations.json";
import zhCategories from "./locales/zh/categories.json";
import zhItems from "./locales/zh/items.json";
import zhInstances from "./locales/zh/instances.json";
import zhErrors from "./locales/zh/errors.json";
import zhDashboard from "./locales/zh/dashboard.json";
import zhStock from "./locales/zh/stock.json";
import zhExpiry from "./locales/zh/expiry.json";
import zhNotifications from "./locales/zh/notifications.json";
import zhConfiguration from "./locales/zh/configuration.json";
import zhAttachments from "./locales/zh/attachments.json";
import zhTags from "./locales/zh/tags.json";
import zhNotes from "./locales/zh/notes.json";
import zhCustomFields from "./locales/zh/customFields.json";
import zhBarcode from "./locales/zh/barcode.json";
import zhSearch from "./locales/zh/search.json";
import zhExport from "./locales/zh/export.json";
import zhRoles from "./locales/zh/roles.json";
import zhUsers from "./locales/zh/users.json";
import zhInvitations from "./locales/zh/invitations.json";
import zhAccount from "./locales/zh/account.json";
import zhResponsible from "./locales/zh/responsible.json";

const NAMESPACES = [
  "common",
  "auth",
  "nav",
  "locations",
  "categories",
  "items",
  "instances",
  "errors",
  "dashboard",
  "stock",
  "expiry",
  "notifications",
  "configuration",
  "attachments",
  "tags",
  "notes",
  "customFields",
  "barcode",
  "search",
  "export",
  "roles",
  "users",
  "invitations",
  "account",
  "responsible",
] as const;

i18next
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    supportedLngs: ["en", "zh"],
    fallbackLng: "en",
    load: "languageOnly",
    ns: [...NAMESPACES],
    defaultNS: "common",
    resources: {
      en: {
        common: enCommon,
        auth: enAuth,
        nav: enNav,
        locations: enLocations,
        categories: enCategories,
        items: enItems,
        instances: enInstances,
        errors: enErrors,
        dashboard: enDashboard,
        stock: enStock,
        expiry: enExpiry,
        notifications: enNotifications,
        configuration: enConfiguration,
        attachments: enAttachments,
        tags: enTags,
        notes: enNotes,
        customFields: enCustomFields,
        barcode: enBarcode,
        search: enSearch,
        export: enExport,
        roles: enRoles,
        users: enUsers,
        invitations: enInvitations,
        account: enAccount,
        responsible: enResponsible,
      },
      zh: {
        common: zhCommon,
        auth: zhAuth,
        nav: zhNav,
        locations: zhLocations,
        categories: zhCategories,
        items: zhItems,
        instances: zhInstances,
        errors: zhErrors,
        dashboard: zhDashboard,
        stock: zhStock,
        expiry: zhExpiry,
        notifications: zhNotifications,
        configuration: zhConfiguration,
        attachments: zhAttachments,
        tags: zhTags,
        notes: zhNotes,
        customFields: zhCustomFields,
        barcode: zhBarcode,
        search: zhSearch,
        export: zhExport,
        roles: zhRoles,
        users: zhUsers,
        invitations: zhInvitations,
        account: zhAccount,
        responsible: zhResponsible,
      },
    },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "omniventory_lang",
      caches: ["localStorage"],
    },
    interpolation: {
      escapeValue: false,
    },
    // Synchronous init: resources are bundled, no async backend needed.
    initAsync: false,
  });

// Keep <html lang> in sync with the active language.
// normalizeLanguage() guarantees exactly 'en' or 'zh' regardless of whether
// i18next.language carries a full BCP-47 tag (e.g. 'zh-CN' when the detector
// reads a navigator locale — load:'languageOnly' only collapses resource
// resolution, not i18next.language itself).
i18next.on("languageChanged", (lng: string) => {
  document.documentElement.lang = normalizeLanguage(lng);
});

// Set it once on startup with the language resolved during init.
document.documentElement.lang = normalizeLanguage(i18next.language);

export default i18next;
