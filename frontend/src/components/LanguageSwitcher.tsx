/**
 * LanguageSwitcher — compact EN / 中文 toggle.
 *
 * Two modes:
 *
 *  "pre-login" (used on Login and Setup pages):
 *    Toggle writes to i18n (which caches to 'omniventory_lang' localStorage via
 *    the detector's caches:['localStorage'] config).  No PATCH — there is no
 *    session yet.
 *
 *  "authed" (used in AppShell header):
 *    Toggle calls i18n.changeLanguage + writes localStorage AND calls
 *    PATCH /api/auth/me so the preference follows the account to other devices.
 *    PATCH failure is non-fatal: the local language change always applies; the
 *    error is surfaced via mapApiError in an inline Alert that auto-dismisses on
 *    the next switch.
 *
 * No new dependencies — uses the existing Mantine Button.Group + Button + Alert.
 */
import { useState } from "react";
import { Button, Group, Alert } from "@mantine/core";
import { AlertCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { SUPPORTED_LANGUAGES, type LanguageCode } from "../i18n/languages";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";

interface LanguageSwitcherProps {
  /**
   * "pre-login": localStorage-only persistence (no session → no PATCH).
   * "authed":    localStorage + PATCH /api/auth/me.
   */
  mode: "pre-login" | "authed";
}

export function LanguageSwitcher({ mode }: LanguageSwitcherProps) {
  const { t, i18n: i18nInstance } = useTranslation("nav");
  const [patchError, setPatchError] = useState<string | null>(null);

  const currentLang = (
    SUPPORTED_LANGUAGES.includes(i18nInstance.language as LanguageCode)
      ? i18nInstance.language
      : "en"
  ) as LanguageCode;

  async function handleSwitch(lang: LanguageCode) {
    if (lang === currentLang) return;

    // Always apply locally first (non-blocking, never reverted).
    // i18next's caches:['localStorage'] config writes 'omniventory_lang' on changeLanguage.
    await i18n.changeLanguage(lang);

    // Clear any previous PATCH error when switching.
    setPatchError(null);

    if (mode === "authed") {
      const { error: apiError } = await client.PATCH("/api/auth/me", {
        body: { preferred_language: lang },
      });
      if (apiError) {
        // Non-fatal: local change already applied above; surface error inline.
        setPatchError(mapApiError(apiError));
      }
    }
  }

  const labels: Record<LanguageCode, string> = {
    en: t("languageEn"),
    zh: t("languageZh"),
  };

  return (
    <Group gap={4}>
      <Button.Group aria-label={t("changeLanguage")}>
        {SUPPORTED_LANGUAGES.map((lang) => (
          <Button
            key={lang}
            size="xs"
            variant={lang === currentLang ? "filled" : "default"}
            onClick={() => void handleSwitch(lang)}
          >
            {labels[lang]}
          </Button>
        ))}
      </Button.Group>

      {patchError && (
        <Alert
          icon={<AlertCircle size={14} />}
          color="orange"
          variant="light"
          p={4}
          style={{ fontSize: "var(--mantine-font-size-xs)" }}
        >
          {patchError}
        </Alert>
      )}
    </Group>
  );
}
