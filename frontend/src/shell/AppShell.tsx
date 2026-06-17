/**
 * Responsive application shell.
 *
 * Desktop: persistent sidebar (navbar) + header.
 * Mobile: header with burger icon → opens a Drawer for navigation.
 *
 * This is the ONE shell definition for the whole app.  Every later page
 * mounts inside <AppShell> via the {children} slot.
 *
 * Shell layout (Step 2 — Mantine Navbar / User-info design):
 *   Navbar top    — Brand area: Package icon in ThemeIcon + "Omniventory" + Divider
 *   Navbar middle — Nav links: Dashboard / Locations / Categories / Items
 *   Navbar bottom — UserButton: Avatar + email + Menu (language, dark-mode, logout)
 *
 * Header (light): brand on mobile + burger + right-side quick area
 *   (color-scheme toggle + language switcher also accessible in UserButton menu).
 *
 * AppShell.Main background:
 *   light → var(--mantine-color-gray-0)
 *   dark  → var(--mantine-color-dark-8)
 *   Wired via light-dark() so cards/tables float above the page bg.
 *
 * Nav links use react-router-dom's NavLink so they get the active style
 * automatically based on the current URL.  Routing and <Routes> live inside
 * the {children} slot rendered by AppShell.Main.
 */
import {
  AppShell as MantineAppShell,
  Burger,
  Drawer,
  Group,
  ActionIcon,
  Text,
  Stack,
  NavLink,
  ThemeIcon,
  Divider,
  useMantineColorScheme,
  useComputedColorScheme,
  Box,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { NavLink as RouterNavLink, useLocation } from "react-router-dom";
import {
  Sun,
  Moon,
  LogOut,
  Layout,
  MapPin,
  Tag,
  Package,
} from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { UserButton } from "../components/UserButton";
import type { components } from "../api/schema";

type UserData = components["schemas"]["UserResponse"];

interface AppShellProps {
  children: React.ReactNode;
  onLogout: () => void;
  /** User data passed from App.tsx (from the me call — no extra request). */
  user?: UserData | null;
}

/**
 * A single nav item that bridges Mantine's NavLink styling with
 * react-router-dom's NavLink active detection.
 *
 * Mantine's NavLink renders an <a> by default; react-router-dom's NavLink
 * also renders an <a>.  To avoid nested <a> elements, we render the Mantine
 * NavLink with component="div" (removes the anchor), and wrap the whole
 * thing in a react-router-dom NavLink anchor for actual navigation.
 */
function NavItem({
  to,
  label,
  icon,
  onClick,
}: {
  to: string;
  label: string;
  icon: React.ReactNode;
  onClick?: () => void;
}) {
  const location = useLocation();
  const isActive =
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);

  return (
    // The router NavLink provides the <a> and handles keyboard/click navigation.
    <RouterNavLink to={to} style={{ textDecoration: "none" }} onClick={onClick}>
      {/* Mantine NavLink renders as a div here — no nested <a>. */}
      <NavLink
        component="div"
        label={label}
        leftSection={icon}
        active={isActive}
        variant="filled"
        style={{ borderRadius: "var(--mantine-radius-md)", cursor: "pointer" }}
      />
    </RouterNavLink>
  );
}

/** Brand area at the top of the Navbar. */
function NavBrand() {
  const { t } = useTranslation("nav");
  return (
    <Box pb="xs">
      <Group gap="xs" px="xs" py="sm">
        <ThemeIcon variant="light" color="teal" size="md" radius="md">
          <Package size={16} />
        </ThemeIcon>
        <Text fw={700} size="md">
          {t("appName")}
        </Text>
      </Group>
      <Divider />
    </Box>
  );
}

/** Sidebar / nav content — real links to all M1 top-level routes. */
function NavContent({ onClose }: { onClose?: () => void }) {
  const { t } = useTranslation("nav");
  return (
    <Stack gap={2} p="xs">
      <NavItem
        to="/"
        label={t("dashboard")}
        icon={<Layout size={16} />}
        onClick={onClose}
      />
      <NavItem
        to="/locations"
        label={t("locations")}
        icon={<MapPin size={16} />}
        onClick={onClose}
      />
      <NavItem
        to="/categories"
        label={t("categories")}
        icon={<Tag size={16} />}
        onClick={onClose}
      />
      <NavItem
        to="/items"
        label={t("items")}
        icon={<Package size={16} />}
        onClick={onClose}
      />
    </Stack>
  );
}

/** Header content: brand (mobile) + burger + right-side quick area. */
function HeaderContent({
  burgerOpened,
  onBurgerToggle,
  onLogout,
}: {
  burgerOpened: boolean;
  onBurgerToggle: () => void;
  onLogout: () => void;
}) {
  const { t } = useTranslation("nav");
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme("dark");

  function toggleColorScheme() {
    setColorScheme(computed === "dark" ? "light" : "dark");
  }

  return (
    <Group h="100%" px="md" justify="space-between">
      {/* Left: burger (mobile only) + app name */}
      <Group>
        <Burger
          opened={burgerOpened}
          onClick={onBurgerToggle}
          hiddenFrom="sm"
          size="sm"
          aria-label={t("toggleNavigation")}
        />
        <Text fw={700} size="lg">
          {t("appName")}
        </Text>
      </Group>

      {/* Right: language switcher + color-scheme toggle + logout (quick access) */}
      <Group gap="xs">
        <LanguageSwitcher mode="authed" />
        <ActionIcon
          variant="default"
          size="lg"
          onClick={toggleColorScheme}
          aria-label={t("toggleColorScheme")}
        >
          {computed === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </ActionIcon>
        <ActionIcon
          variant="default"
          size="lg"
          onClick={onLogout}
          aria-label={t("logout")}
        >
          <LogOut size={16} />
        </ActionIcon>
      </Group>
    </Group>
  );
}

export function AppShell({ children, onLogout, user }: AppShellProps) {
  const { t } = useTranslation("nav");
  const [drawerOpened, { toggle: toggleDrawer, close: closeDrawer }] =
    useDisclosure(false);

  async function handleLogout() {
    await client.POST("/api/auth/logout");
    onLogout();
  }

  return (
    <>
      {/* Mobile drawer (visible on sm and below) */}
      <Drawer
        opened={drawerOpened}
        onClose={closeDrawer}
        size="xs"
        padding="md"
        title={t("navigation")}
        hiddenFrom="sm"
        zIndex={1000}
      >
        <NavContent onClose={closeDrawer} />
      </Drawer>

      {/* Mantine AppShell: navbar hidden on mobile (handled by Drawer instead) */}
      <MantineAppShell
        header={{ height: 56 }}
        navbar={{
          width: 220,
          breakpoint: "sm",
          collapsed: { mobile: true },
        }}
        padding="md"
      >
        <MantineAppShell.Header>
          <HeaderContent
            burgerOpened={drawerOpened}
            onBurgerToggle={toggleDrawer}
            onLogout={handleLogout}
          />
        </MantineAppShell.Header>

        <MantineAppShell.Navbar
          style={{ display: "flex", flexDirection: "column" }}
        >
          {/* Brand area: icon + name + divider */}
          <NavBrand />

          {/* Nav links — fill remaining vertical space */}
          <Box style={{ flex: 1 }}>
            <NavContent />
          </Box>

          {/* UserButton pinned to the bottom */}
          {user && (
            <Box
              p="xs"
              style={{
                borderTop:
                  "1px solid var(--mantine-color-default-border)",
              }}
            >
              <UserButton email={user.email} onLogout={handleLogout} />
            </Box>
          )}
        </MantineAppShell.Navbar>

        {/* App background: gray-0 (light) / dark-8 (dark) so cards/tables float */}
        <MantineAppShell.Main
          style={{
            background:
              "light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-8))",
          }}
        >
          {children}
        </MantineAppShell.Main>
      </MantineAppShell>
    </>
  );
}
