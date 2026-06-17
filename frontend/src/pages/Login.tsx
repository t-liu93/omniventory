/**
 * Login page.
 *
 * Posts credentials via the typed client → on success notifies the parent to
 * transition into the authenticated shell.  Session is cookie-based (HttpOnly);
 * nothing is stored in localStorage/sessionStorage.
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

interface LoginProps {
  onSuccess: () => void;
}

export function Login({ onSuccess }: LoginProps) {
  const { t } = useTranslation("auth");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const { error: apiError } = await client.POST("/api/auth/login", {
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
      <Paper
        w="100%"
        maw={400}
        p="xl"
        radius="md"
        withBorder
        shadow="sm"
      >
        <form onSubmit={handleSubmit}>
          <Stack gap="lg">
            <LanguageSwitcher mode="pre-login" />
            <Stack gap={4}>
              <Title order={2} ta="center">
                {t("login.title")}
              </Title>
              <Text c="dimmed" size="sm" ta="center">
                {t("login.subtitle")}
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
              label={t("login.emailLabel")}
              placeholder={t("login.emailPlaceholder")}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
              required
              autoComplete="email"
            />

            <PasswordInput
              label={t("login.passwordLabel")}
              placeholder={t("login.passwordPlaceholder")}
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              required
              autoComplete="current-password"
            />

            <Button type="submit" fullWidth loading={loading}>
              {t("login.submit")}
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
