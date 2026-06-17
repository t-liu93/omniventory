/**
 * UserButton — pinned to the bottom of the Navbar.
 *
 * Shows an Avatar (first letter of email), the email address, and a Menu
 * that contains:
 *   - Language switcher (inline EN / 中文 toggle)
 *   - Color-scheme toggle
 *   - Logout
 *
 * The email initial is derived client-side from the email prop.
 * All text via i18n (nav namespace).
 */
import {
  Avatar,
  Group,
  Text,
  Menu,
  UnstyledButton,
  Divider,
  useMantineColorScheme,
  useComputedColorScheme,
} from "@mantine/core";
import { Sun, Moon, LogOut } from "react-feather";
import { useTranslation } from "react-i18next";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { emailToInitial } from "./emailToInitial";

interface UserButtonProps {
  email: string;
  onLogout: () => void;
}

export function UserButton({ email, onLogout }: UserButtonProps) {
  const { t } = useTranslation("nav");
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme("dark");

  function toggleColorScheme() {
    setColorScheme(computed === "dark" ? "light" : "dark");
  }

  const initial = emailToInitial(email);

  return (
    <Menu position="top" withArrow offset={4} width={220}>
      <Menu.Target>
        <UnstyledButton
          aria-label={t("userMenu")}
          style={{
            display: "block",
            width: "100%",
            padding: "var(--mantine-spacing-xs)",
            borderRadius: "var(--mantine-radius-md)",
          }}
        >
          <Group wrap="nowrap" gap="xs">
            <Avatar color="teal" radius="xl" size="sm">
              {initial}
            </Avatar>
            <Text
              size="sm"
              fw={500}
              truncate="end"
              style={{ flex: 1, minWidth: 0 }}
            >
              {email}
            </Text>
          </Group>
        </UnstyledButton>
      </Menu.Target>

      <Menu.Dropdown>
        {/* Language switcher row */}
        <Menu.Label>{t("language")}</Menu.Label>
        <Menu.Item
          component="div"
          closeMenuOnClick={false}
          style={{ cursor: "default" }}
        >
          <LanguageSwitcher mode="authed" />
        </Menu.Item>

        <Divider />

        {/* Color-scheme toggle */}
        <Menu.Item
          leftSection={
            computed === "dark" ? <Sun size={14} /> : <Moon size={14} />
          }
          onClick={toggleColorScheme}
          aria-label={t("toggleColorScheme")}
        >
          {t("toggleColorScheme")}
        </Menu.Item>

        <Divider />

        {/* Logout */}
        <Menu.Item
          color="red"
          leftSection={<LogOut size={14} />}
          onClick={onLogout}
          aria-label={t("logout")}
        >
          {t("logout")}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
