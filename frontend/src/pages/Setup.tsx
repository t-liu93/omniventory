/**
 * First-run setup page.
 *
 * Shown when GET /api/auth/setup-status returns { setup_required: true },
 * meaning no users exist yet.  The user fills in an email + password to
 * create the first (admin) account.  On success, the parent is notified to
 * transition to the Login page — we do NOT auto-login (by design).
 *
 * Auth: unauthenticated endpoint; no session cookie required or set here.
 */
import { useState } from "react";
import {
  Center,
  Paper,
  Stack,
  Title,
  Text,
  TextInput,
  PasswordInput,
  Button,
  Alert,
} from "@mantine/core";
import { AlertCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { LanguageSwitcher } from "../components/LanguageSwitcher";

interface SetupProps {
  /** Called after the first admin is created; parent transitions to Login. */
  onSuccess: () => void;
}

export function Setup({ onSuccess }: SetupProps) {
  const { t } = useTranslation("auth");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const { error: apiError } = await client.POST("/api/auth/setup", {
      body: { email, password },
    });

    setLoading(false);

    if (apiError) {
      setError(mapApiError(apiError));
      return;
    }

    onSuccess();
  }

  return (
    <Center h="100dvh" p="md">
      <Paper w="100%" maw={400} p="xl" radius="md" withBorder shadow="sm">
        <form onSubmit={handleSubmit}>
          <Stack gap="lg">
            <LanguageSwitcher mode="pre-login" />
            <Stack gap={4}>
              <Title order={2} ta="center">
                {t("setup.title")}
              </Title>
              <Text c="dimmed" size="sm" ta="center">
                {t("setup.subtitle")}
              </Text>
            </Stack>

            {error && (
              <Alert
                icon={<AlertCircle size={16} />}
                color="red"
                variant="light"
                role="alert"
              >
                {error}
              </Alert>
            )}

            <TextInput
              label={t("setup.emailLabel")}
              placeholder={t("setup.emailPlaceholder")}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              required
              autoComplete="email"
            />

            <PasswordInput
              label={t("setup.passwordLabel")}
              placeholder={t("setup.passwordPlaceholder")}
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              required
              autoComplete="new-password"
            />

            <Button type="submit" fullWidth loading={loading}>
              {t("setup.submit")}
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
