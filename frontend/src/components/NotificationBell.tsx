/**
 * NotificationBell — header bell icon with unread badge and dropdown inbox.
 *
 * Polls GET /api/notifications/unread-count on a fixed interval (30s) and
 * after any mark-read action.  Click opens a Popover listing recent
 * notifications, each localized via message_code + params from the
 * `notifications` namespace.  "Mark all read" and "View all" links at bottom.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Divider,
  Group,
  Indicator,
  Loader,
  Popover,
  Stack,
  Text,
  Anchor,
} from "@mantine/core";
import { Bell } from "react-feather";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { client } from "../api/client";
import { formatDate } from "../i18n/format";
import type { components } from "../api/schema";

type Notification = components["schemas"]["NotificationResponse"];

const POLL_INTERVAL_MS = 30_000;
const DROPDOWN_LIMIT = 10;

/**
 * Localize a notification using its message_code and params.
 * Params come already parsed as a dict from the API.
 *
 * For level-mode low-stock notifications the backend stores a ``level`` code
 * (e.g. ``"low"``) instead of numeric ``current``/``threshold``.  We resolve
 * that code to a localized label and feed it into the existing
 * ``reminder.low_stock`` / ``reminder.low_stock_repeat`` templates via the
 * ``current`` and ``threshold`` interpolation slots, so that both modes share
 * one template string and the wire/display split (M1.5) is respected.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function localizeNotification(n: Notification, t: TFunction<any, any>): string {
  const params = n.params ?? {};
  // Format date fields in params for display
  const formattedParams: Record<string, unknown> = { ...params };
  if (typeof formattedParams["date"] === "string") {
    formattedParams["date"] = formatDate(formattedParams["date"] as string);
  }
  // Level-mode low-stock: substitute the localized level label for current/threshold.
  if (formattedParams["mode"] === "level") {
    const levelCode = (formattedParams["level"] as string | undefined) ?? "";
    const levelLabel = levelCode
      ? (t(`level.${levelCode}`, { ns: "notifications" }) as string)
      : levelCode;
    formattedParams["current"] = levelLabel;
    formattedParams["threshold"] = levelLabel;
  }
  return t(n.message_code, { ns: "notifications", ...formattedParams }) as string;
}

/** Relative time helper — returns a short localized human-readable string. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function relativeTime(isoString: string, t: TFunction<any, any>): string {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return t("time.justNow", { ns: "notifications" }) as string;
  if (mins < 60) return t("time.minutesAgo", { ns: "notifications", count: mins }) as string;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return t("time.hoursAgo", { ns: "notifications", count: hours }) as string;
  const days = Math.floor(hours / 24);
  return t("time.daysAgo", { ns: "notifications", count: days }) as string;
}

interface NotificationRowProps {
  notification: Notification;
  onMarkRead: (id: number) => void;
}

function NotificationRow({ notification: n, onMarkRead }: NotificationRowProps) {
  const { t } = useTranslation("notifications");
  const message = localizeNotification(n, t);
  const isUnread = n.read_at === null;

  return (
    <Box
      data-testid={`notification-row-${n.id}`}
      py="xs"
      px="sm"
      style={{
        background: isUnread
          ? "light-dark(var(--mantine-color-blue-0), var(--mantine-color-dark-6))"
          : undefined,
        borderRadius: "var(--mantine-radius-sm)",
        cursor: "default",
      }}
    >
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="xs">
        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
          <Text
            size="sm"
            fw={isUnread ? 600 : 400}
            data-testid={`notification-message-${n.id}`}
            style={{ wordBreak: "break-word" }}
          >
            {message}
          </Text>
          <Text size="xs" c="dimmed">
            {relativeTime(n.created_at, t)}
          </Text>
        </Stack>
        {isUnread && (
          <Button
            size="compact-xs"
            variant="subtle"
            onClick={() => onMarkRead(n.id)}
            data-testid={`mark-read-btn-${n.id}`}
          >
            {t("markRead")}
          </Button>
        )}
      </Group>
    </Box>
  );
}

interface NotificationBellProps {
  /** Called after any state change so parent can re-render if needed. */
  onCountChange?: (count: number) => void;
}

export function NotificationBell({ onCountChange }: NotificationBellProps) {
  const { t } = useTranslation("notifications");
  const navigate = useNavigate();

  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState<Notification[] | null>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [loadingList, setLoadingList] = useState(false);
  const [markingAll, setMarkingAll] = useState(false);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /** Fetch unread count; updates badge silently. */
  const fetchCount = useCallback(async () => {
    try {
      const result = await client.GET("/api/notifications/unread-count");
      if (result?.data) {
        setUnreadCount(result.data.count);
        onCountChange?.(result.data.count);
      }
    } catch {
      // Silently ignore errors — badge simply stays at its current value.
    }
  }, [onCountChange]);

  /** Fetch recent notifications for the dropdown. */
  const fetchList = useCallback(async () => {
    setLoadingList(true);
    try {
      const result = await client.GET("/api/notifications", {
        params: { query: { limit: DROPDOWN_LIMIT } },
      });
      if (result?.data) {
        setNotifications(result.data);
      }
    } catch {
      // Silently ignore — dropdown stays empty.
    } finally {
      setLoadingList(false);
    }
  }, []);

  // Start polling on mount; clean up on unmount.
  useEffect(() => {
    void fetchCount();
    intervalRef.current = setInterval(() => {
      void fetchCount();
    }, POLL_INTERVAL_MS);
    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchCount]);

  // Load the list when the popover opens.
  useEffect(() => {
    if (popoverOpen) {
      void fetchList();
    }
  }, [popoverOpen, fetchList]);

  async function handleMarkRead(id: number) {
    await client.POST("/api/notifications/{notification_id}/read", {
      params: { path: { notification_id: id } },
    });
    await Promise.all([fetchCount(), fetchList()]);
  }

  async function handleMarkAllRead() {
    setMarkingAll(true);
    await client.POST("/api/notifications/read-all");
    setMarkingAll(false);
    await Promise.all([fetchCount(), fetchList()]);
  }

  function handleViewAll() {
    setPopoverOpen(false);
    navigate("/notifications");
  }

  return (
    <Popover
      opened={popoverOpen}
      onChange={setPopoverOpen}
      width={340}
      position="bottom-end"
      withArrow
      shadow="md"
    >
      <Popover.Target>
        <Indicator
          disabled={unreadCount === 0}
          label={unreadCount > 99 ? "99+" : String(unreadCount)}
          size={16}
          color="red"
          data-testid="notification-indicator"
        >
          <ActionIcon
            variant="default"
            size="lg"
            onClick={() => setPopoverOpen((o) => !o)}
            aria-label={t("bell.ariaLabel")}
            data-testid="notification-bell-btn"
          >
            <Bell size={16} />
          </ActionIcon>
        </Indicator>
      </Popover.Target>

      <Popover.Dropdown p={0}>
        {/* Header */}
        <Group px="sm" py="xs" justify="space-between">
          <Group gap="xs">
            <Text fw={600} size="sm">
              {t("title")}
            </Text>
            {unreadCount > 0 && (
              <Badge size="xs" color="red" data-testid="unread-badge">
                {unreadCount}
              </Badge>
            )}
          </Group>
          <Button
            size="compact-xs"
            variant="subtle"
            onClick={handleMarkAllRead}
            loading={markingAll}
            data-testid="mark-all-read-btn"
          >
            {t("bell.markAllRead")}
          </Button>
        </Group>

        <Divider />

        {/* Body */}
        <Box
          style={{ maxHeight: 360, overflowY: "auto" }}
          data-testid="notification-dropdown-list"
        >
          {loadingList && (
            <Group justify="center" py="md">
              <Loader size="xs" />
            </Group>
          )}

          {!loadingList && notifications !== null && notifications.length === 0 && (
            <Text size="sm" c="dimmed" ta="center" py="md" data-testid="bell-empty">
              {t("bell.noNotifications")}
            </Text>
          )}

          {!loadingList && notifications !== null && notifications.length > 0 && (
            <Stack gap={0} p="xs">
              {notifications.map((n) => (
                <NotificationRow
                  key={n.id}
                  notification={n}
                  onMarkRead={(id) => { void handleMarkRead(id); }}
                />
              ))}
            </Stack>
          )}
        </Box>

        <Divider />

        {/* Footer */}
        <Group px="sm" py="xs" justify="center">
          <Anchor
            size="sm"
            component="button"
            onClick={handleViewAll}
            data-testid="view-all-link"
          >
            {t("bell.viewAll")}
          </Anchor>
        </Group>
      </Popover.Dropdown>
    </Popover>
  );
}
