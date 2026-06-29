/**
 * /notifications page — full notification inbox.
 *
 * Shows all notifications newest-first, with an unread-only toggle.
 * Each row localizes via message_code + params, links to the subject
 * (instance → /instances/:id, definition → /items/:id), and has a
 * per-row mark-read action.
 */
import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  SegmentedControl,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { PageShell } from "../components/PageShell";
import { ErrorState } from "../components/ErrorState";
import { client } from "../api/client";
import { formatDate } from "../i18n/format";
import type { components } from "../api/schema";

type Notification = components["schemas"]["NotificationResponse"];

/** Localize a notification from its message_code + params.
 *
 * For level-mode low-stock notifications the backend stores a ``level`` code
 * (e.g. ``"low"``) instead of numeric ``current``/``threshold``.  We resolve
 * that code to a localized label and feed it into the existing
 * ``reminder.low_stock`` / ``reminder.low_stock_repeat`` templates via the
 * ``current`` and ``threshold`` interpolation slots, so that both modes share
 * one template string and the wire/display split (M1.5) is respected.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function localizeMessage(n: Notification, t: TFunction<any, any>): string {
  const params = n.params ?? {};
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
  // Maintenance reminders: format next_due_date and select overdue vs normal key.
  if (n.message_code === "reminder.maintenance") {
    if (typeof formattedParams["next_due_date"] === "string") {
      formattedParams["next_due_date"] = formatDate(formattedParams["next_due_date"] as string);
    }
    const daysRemaining =
      typeof formattedParams["days_remaining"] === "number"
        ? (formattedParams["days_remaining"] as number)
        : 0;
    if (daysRemaining < 0) {
      formattedParams["days_overdue"] = Math.abs(daysRemaining);
      return t("reminder.maintenance_overdue", { ns: "notifications", ...formattedParams }) as string;
    }
  }
  return t(n.message_code, { ns: "notifications", ...formattedParams }) as string;
}

/** Resolve the subject link based on subject_type. */
function subjectLink(n: Notification): string {
  if (n.subject_type === "instance") return `/instances/${n.subject_id}`;
  return `/items/${n.subject_id}`;
}

export function Notifications() {
  const { t } = useTranslation("notifications");

  const [notifications, setNotifications] = useState<Notification[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [markingAll, setMarkingAll] = useState(false);
  const [markingId, setMarkingId] = useState<number | null>(null);

  const load = useCallback(async (unreadOnlyFlag: boolean) => {
    setLoading(true);
    setError(null);
    const { data, error: apiError } = await client.GET("/api/notifications", {
      params: { query: { unread_only: unreadOnlyFlag, limit: 200 } },
    });
    if (apiError || !Array.isArray(data)) {
      setError(t("loadError"));
      setLoading(false);
      return;
    }
    setNotifications(data);
    setLoading(false);
  }, [t]);

  useEffect(() => {
    void load(unreadOnly);
  }, [load, unreadOnly]);

  async function handleMarkRead(id: number) {
    setMarkingId(id);
    await client.POST("/api/notifications/{notification_id}/read", {
      params: { path: { notification_id: id } },
    });
    setMarkingId(null);
    void load(unreadOnly);
  }

  async function handleMarkAllRead() {
    setMarkingAll(true);
    await client.POST("/api/notifications/read-all");
    setMarkingAll(false);
    void load(unreadOnly);
  }

  const actions = (
    <Group gap="sm">
      <SegmentedControl
        size="xs"
        value={unreadOnly ? "unread" : "all"}
        onChange={(v) => setUnreadOnly(v === "unread")}
        data={[
          { label: t("filter.allNotifications"), value: "all" },
          { label: t("filter.unreadOnly"), value: "unread" },
        ]}
        data-testid="unread-filter-control"
      />
      <Button
        size="xs"
        variant="light"
        onClick={handleMarkAllRead}
        loading={markingAll}
        data-testid="page-mark-all-read-btn"
      >
        {t("bell.markAllRead")}
      </Button>
    </Group>
  );

  return (
    <PageShell title={t("title")} actions={actions}>
      {loading && (
        <Group justify="center" py="xl">
          <Loader size="sm" />
        </Group>
      )}

      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && notifications !== null && notifications.length === 0 && (
        <Text
          c="dimmed"
          size="sm"
          ta="center"
          py="xl"
          data-testid="notifications-empty"
        >
          {unreadOnly ? t("empty.unread") : t("empty.all")}
        </Text>
      )}

      {!loading && !error && notifications !== null && notifications.length > 0 && (
        <Stack gap="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("col.message")}</Table.Th>
                <Table.Th>{t("col.subject")}</Table.Th>
                <Table.Th>{t("col.date")}</Table.Th>
                <Table.Th>{t("col.status")}</Table.Th>
                <Table.Th></Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {notifications.map((n) => {
                const isUnread = n.read_at === null;
                const message = localizeMessage(n, t);
                return (
                  <Table.Tr
                    key={n.id}
                    data-testid={`notification-page-row-${n.id}`}
                    style={{
                      background: isUnread
                        ? "light-dark(var(--mantine-color-blue-0), var(--mantine-color-dark-6))"
                        : undefined,
                    }}
                  >
                    <Table.Td data-testid={`notification-page-message-${n.id}`}>
                      <Text size="sm" fw={isUnread ? 600 : 400}>
                        {message}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={subjectLink(n)}
                        size="sm"
                        data-testid={`notification-subject-link-${n.id}`}
                      >
                        {n.subject_type === "instance"
                          ? t("subject.viewInstance")
                          : t("subject.viewItem")}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed">
                        {formatDate(n.created_at)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {isUnread ? (
                        <Badge color="blue" size="sm" data-testid={`unread-badge-${n.id}`}>
                          {t("unread")}
                        </Badge>
                      ) : (
                        <Text size="xs" c="dimmed">
                          —
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {isUnread && (
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          loading={markingId === n.id}
                          onClick={() => { void handleMarkRead(n.id); }}
                          data-testid={`page-mark-read-btn-${n.id}`}
                        >
                          {t("markRead")}
                        </Button>
                      )}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </PageShell>
  );
}
