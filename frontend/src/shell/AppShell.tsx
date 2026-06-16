/**
 * Responsive application shell.
 *
 * Desktop: persistent sidebar (navbar) + header.
 * Mobile: header with burger icon → opens a Drawer for navigation.
 *
 * This is the ONE shell definition for the whole app.  Every later page
 * mounts inside <AppShell> via the {children} slot.
 *
 * Color-scheme toggle and logout action live in the header.
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
  useMantineColorScheme,
  useComputedColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { NavLink as RouterNavLink, useLocation } from "react-router-dom";
import { Sun, Moon, LogOut, Layout, MapPin, Tag } from "react-feather";
import { client } from "../api/client";

interface AppShellProps {
  children: React.ReactNode;
  onLogout: () => void;
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
        style={{ cursor: "pointer" }}
      />
    </RouterNavLink>
  );
}

/** Sidebar / nav content — real links to all M1 top-level routes. */
function NavContent({ onClose }: { onClose?: () => void }) {
  return (
    <Stack gap={4} p="xs">
      <NavItem
        to="/"
        label="Dashboard"
        icon={<Layout size={16} />}
        onClick={onClose}
      />
      <NavItem
        to="/locations"
        label="Locations"
        icon={<MapPin size={16} />}
        onClick={onClose}
      />
      <NavItem
        to="/categories"
        label="Categories"
        icon={<Tag size={16} />}
        onClick={onClose}
      />
    </Stack>
  );
}

/** Header content: app name, dark-mode toggle, logout. */
function HeaderContent({
  burgerOpened,
  onBurgerToggle,
  onLogout,
}: {
  burgerOpened: boolean;
  onBurgerToggle: () => void;
  onLogout: () => void;
}) {
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
          aria-label="Toggle navigation"
        />
        <Text fw={700} size="lg">
          Omniventory
        </Text>
      </Group>

      {/* Right: color-scheme toggle + logout */}
      <Group gap="xs">
        <ActionIcon
          variant="default"
          size="lg"
          onClick={toggleColorScheme}
          aria-label="Toggle color scheme"
        >
          {computed === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </ActionIcon>
        <ActionIcon
          variant="default"
          size="lg"
          onClick={onLogout}
          aria-label="Logout"
        >
          <LogOut size={16} />
        </ActionIcon>
      </Group>
    </Group>
  );
}

export function AppShell({ children, onLogout }: AppShellProps) {
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
        title="Navigation"
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

        <MantineAppShell.Navbar>
          <NavContent />
        </MantineAppShell.Navbar>

        <MantineAppShell.Main>{children}</MantineAppShell.Main>
      </MantineAppShell>
    </>
  );
}
