/**
 * Users administration page — admin-only (MANAGE_USERS).
 *
 * Features:
 *  - Users table: email, role (Select), status toggle (Button), delete (confirm modal),
 *    reset password (generates a one-time link in a modal).
 *  - Invite user: modal with email + role → POST /api/invitations → shows accept_url
 *    with copy button and emailed status.
 *  - Pending invitations: list from GET /api/invitations with per-row revoke.
 *
 * Error surfacing (M6.md §9 Step 9):
 *  - user.last_admin: shown as an Alert inside the delete confirm modal (inline,
 *    so the modal stays open and the error is obvious).
 *  - user.email_exists: shown as an Alert inside the invite modal.
 *  - Other table-action errors: shown as a page-level Alert above the table so
 *    the user can dismiss and retry.
 *
 * All dates use formatDate (M1.5 src/i18n/format).
 * No created_at column — UserSummary does not carry it (M6.md §4.10).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  NativeSelect,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  AlertCircle,
  UserPlus,
  Users as UsersIcon,
} from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess } from "../components/notify";
import { PageShell } from "../components/PageShell";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import { formatDate } from "../i18n/format";
import type { components } from "../api/schema";

type UserSummary = components["schemas"]["UserSummary"];
type PendingInvitationResponse = components["schemas"]["PendingInvitationResponse"];
type InvitationResponse = components["schemas"]["InvitationResponse"];
type PasswordResetIssueResponse = components["schemas"]["PasswordResetIssueResponse"];

/**
 * Clipboard copy that degrades gracefully in non-secure contexts.
 *
 * In plain-HTTP self-hosted deployments (http://<LAN-IP>), navigator.clipboard
 * is defined on the Navigator interface but its value is `undefined`. A direct
 * `navigator.clipboard.writeText(...)` call throws a synchronous TypeError
 * before any `void` or `.catch()` can intercept it.  This helper checks for
 * the object first and swallows any async rejection (e.g. document not focused,
 * permission denied).  If clipboard is unavailable the link text is still
 * visible/selectable in the DOM for manual copying.
 */
function safeCopy(text: string): void {
  if (!navigator.clipboard) {
    return;
  }
  navigator.clipboard.writeText(text).catch(() => {
    // Write rejected (permission denied, document not focused) — degrade silently.
  });
}

export function Users() {
  const { t } = useTranslation("users");
  const { t: tInv } = useTranslation("invitations");
  const { t: tRoles } = useTranslation("roles");
  const { t: tCommon } = useTranslation("common");

  // ── Users list ──────────────────────────────────────────────────────────────
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [usersLoadError, setUsersLoadError] = useState<string | null>(null);

  // ── Pending invitations ─────────────────────────────────────────────────────
  const [pendingInvites, setPendingInvites] = useState<PendingInvitationResponse[]>([]);
  const [invitesLoading, setInvitesLoading] = useState(true);

  // ── Page-level action error (role change / active toggle / revoke errors) ───
  const [actionError, setActionError] = useState<string | null>(null);

  // ── Delete user modal ───────────────────────────────────────────────────────
  const [deleteTarget, setDeleteTarget] = useState<UserSummary | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteModalOpened, { open: openDeleteModal, close: closeDeleteModal }] =
    useDisclosure(false);

  // ── Reset password modal ────────────────────────────────────────────────────
  const [resetTarget, setResetTarget] = useState<UserSummary | null>(null);
  const [resetLoading, setResetLoading] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetResult, setResetResult] = useState<PasswordResetIssueResponse | null>(null);
  const [resetModalOpened, { open: openResetModal, close: closeResetModal }] =
    useDisclosure(false);

  // ── Invite modal ────────────────────────────────────────────────────────────
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<string | null>("member");
  const [inviteLoading, setInviteLoading] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteResult, setInviteResult] = useState<InvitationResponse | null>(null);
  const [inviteModalOpened, { open: openInviteModal, close: closeInviteModal }] =
    useDisclosure(false);

  // ── Data loaders ────────────────────────────────────────────────────────────

  const loadUsers = useCallback(async (isInitial = false) => {
    if (isInitial) setUsersLoading(true);
    const { data, error } = await client.GET("/api/users");
    if (error || !data) {
      setUsersLoadError(mapApiError(error));
    } else {
      setUsers(data);
      setUsersLoadError(null);
    }
    if (isInitial) setUsersLoading(false);
  }, []);

  const loadInvites = useCallback(async () => {
    setInvitesLoading(true);
    const { data } = await client.GET("/api/invitations");
    if (data) {
      setPendingInvites(data);
    }
    setInvitesLoading(false);
  }, []);

  useEffect(() => {
    void loadUsers(true);
    void loadInvites();
  }, [loadUsers, loadInvites]);

  // ── Table action handlers ───────────────────────────────────────────────────

  async function handleRoleChange(userId: number, newRole: string) {
    setActionError(null);
    const { error } = await client.PATCH("/api/users/{user_id}", {
      params: { path: { user_id: userId } },
      body: { role: newRole },
    });
    if (error) {
      setActionError(mapApiError(error));
    } else {
      await loadUsers();
    }
  }

  async function handleActiveToggle(userId: number, currentIsActive: boolean) {
    setActionError(null);
    const { error } = await client.PATCH("/api/users/{user_id}", {
      params: { path: { user_id: userId } },
      body: { is_active: !currentIsActive },
    });
    if (error) {
      setActionError(mapApiError(error));
    } else {
      await loadUsers();
    }
  }

  function openDelete(user: UserSummary) {
    setDeleteTarget(user);
    setDeleteError(null);
    openDeleteModal();
  }

  async function handleDeleteConfirm() {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    setDeleteError(null);
    const { error } = await client.DELETE("/api/users/{user_id}", {
      params: { path: { user_id: deleteTarget.id } },
    });
    setDeleteLoading(false);
    if (error) {
      // Surface last_admin (and other delete errors) inline in the modal so the
      // user sees it without the modal closing.
      setDeleteError(mapApiError(error));
    } else {
      closeDeleteModal();
      setDeleteTarget(null);
      notifySuccess(t("delete.success"));
      await loadUsers();
    }
  }

  function openReset(user: UserSummary) {
    setResetTarget(user);
    setResetError(null);
    setResetResult(null);
    openResetModal();
  }

  async function handleResetPassword() {
    if (!resetTarget) return;
    setResetLoading(true);
    setResetError(null);
    const { data, error } = await client.POST("/api/users/{user_id}/reset-password", {
      params: { path: { user_id: resetTarget.id } },
    });
    setResetLoading(false);
    if (error) {
      setResetError(mapApiError(error));
    } else if (data) {
      setResetResult(data);
    }
  }

  function handleCloseReset() {
    closeResetModal();
    setResetResult(null);
    setResetError(null);
  }

  // ── Invite handlers ─────────────────────────────────────────────────────────

  async function handleInviteSubmit() {
    if (!inviteEmail.trim() || !inviteRole) return;
    setInviteLoading(true);
    setInviteError(null);
    const { data, error } = await client.POST("/api/invitations", {
      body: { email: inviteEmail.trim(), role: inviteRole },
    });
    setInviteLoading(false);
    if (error) {
      // user.email_exists and other invite errors surface inline.
      setInviteError(mapApiError(error));
    } else if (data) {
      setInviteResult(data);
      await loadInvites();
    }
  }

  function handleCloseInviteModal() {
    closeInviteModal();
    setInviteEmail("");
    setInviteRole("member");
    setInviteError(null);
    setInviteResult(null);
  }

  async function handleRevoke(inviteId: number) {
    setActionError(null);
    const { error } = await client.DELETE("/api/invitations/{invite_id}", {
      params: { path: { invite_id: inviteId } },
    });
    if (error) {
      setActionError(mapApiError(error));
    } else {
      await loadInvites();
    }
  }

  // ── Role data for Select components ────────────────────────────────────────

  const roleData = [
    { value: "admin", label: tRoles("admin") },
    { value: "member", label: tRoles("member") },
    { value: "viewer", label: tRoles("viewer") },
  ];

  // ── Render ──────────────────────────────────────────────────────────────────

  if (usersLoading) return <LoadingState />;
  if (usersLoadError) return <ErrorState message={usersLoadError} />;

  return (
    <PageShell
      title={t("page.title")}
      subtitle={t("page.subtitle")}
      actions={
        <Button
          data-testid="invite-btn"
          leftSection={<UserPlus size={16} />}
          onClick={openInviteModal}
        >
          {t("actions.invite")}
        </Button>
      }
    >
      <Stack gap="lg">
        {/* Page-level action error (role change / active toggle / revoke) */}
        {actionError && (
          <Alert
            data-testid="action-error"
            icon={<AlertCircle size={16} />}
            color="red"
            withCloseButton
            onClose={() => setActionError(null)}
          >
            {actionError}
          </Alert>
        )}

        {/* ── Users table ── */}
        <Paper withBorder>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("table.email")}</Table.Th>
                <Table.Th>{t("table.role")}</Table.Th>
                <Table.Th>{t("table.active")}</Table.Th>
                <Table.Th>{t("table.actions")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.length === 0 ? (
                <Table.Tr>
                  <Table.Td colSpan={4}>
                    <Text c="dimmed" ta="center" py="md">
                      {t("empty")}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ) : (
                users.map((user) => (
                  <Table.Tr key={user.id} data-testid={`user-row-${user.id}`}>
                    {/* Email */}
                    <Table.Td>{user.email}</Table.Td>

                    {/* Role — NativeSelect fires PATCH on change.
                        NativeSelect renders a <select> element for reliable
                        test interaction via fireEvent.change. */}
                    <Table.Td>
                      <NativeSelect
                        data-testid={`role-select-${user.id}`}
                        value={user.role}
                        data={roleData}
                        onChange={(e) => {
                          void handleRoleChange(user.id, e.currentTarget.value);
                        }}
                        size="xs"
                      />
                    </Table.Td>

                    {/* Active toggle button — fires PATCH on click */}
                    <Table.Td>
                      <Button
                        data-testid={`active-toggle-${user.id}`}
                        variant={user.is_active ? "light" : "outline"}
                        color={user.is_active ? "teal" : "gray"}
                        size="xs"
                        onClick={() => void handleActiveToggle(user.id, user.is_active)}
                      >
                        {user.is_active ? t("actions.deactivate") : t("actions.activate")}
                      </Button>
                    </Table.Td>

                    {/* Per-row actions */}
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Button
                          data-testid={`reset-btn-${user.id}`}
                          variant="subtle"
                          size="xs"
                          onClick={() => openReset(user)}
                        >
                          {t("actions.resetPassword")}
                        </Button>
                        <Button
                          data-testid={`delete-btn-${user.id}`}
                          variant="subtle"
                          color="red"
                          size="xs"
                          onClick={() => openDelete(user)}
                        >
                          {t("actions.delete")}
                        </Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))
              )}
            </Table.Tbody>
          </Table>
        </Paper>

        {/* ── Pending invitations section ── */}
        <Stack gap="sm">
          <Title order={3}>{tInv("section.title")}</Title>
          {invitesLoading ? (
            <LoadingState />
          ) : pendingInvites.length === 0 ? (
            <Text c="dimmed" size="sm">
              {tInv("section.empty")}
            </Text>
          ) : (
            <Paper withBorder>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{tInv("table.email")}</Table.Th>
                    <Table.Th>{tInv("table.role")}</Table.Th>
                    <Table.Th>{tInv("table.expires")}</Table.Th>
                    <Table.Th>{tInv("table.actions")}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {pendingInvites.map((inv) => (
                    <Table.Tr
                      key={inv.id}
                      data-testid={`pending-inv-row-${inv.id}`}
                    >
                      <Table.Td>{inv.email}</Table.Td>
                      <Table.Td>
                        <Badge variant="outline">
                          {tRoles(inv.role)}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{formatDate(inv.expires_at)}</Table.Td>
                      <Table.Td>
                        <Button
                          data-testid={`revoke-inv-${inv.id}`}
                          variant="subtle"
                          color="red"
                          size="xs"
                          onClick={() => void handleRevoke(inv.id)}
                        >
                          {tInv("actions.revoke")}
                        </Button>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>
          )}
        </Stack>
      </Stack>

      {/* ── Delete confirmation modal ── */}
      <Modal
        opened={deleteModalOpened}
        onClose={closeDeleteModal}
        title={t("delete.title")}
      >
        <Stack gap="md">
          {/* Inline error for last_admin and other delete failures */}
          {deleteError && (
            <Alert
              data-testid="delete-error"
              icon={<AlertCircle size={16} />}
              color="red"
            >
              {deleteError}
            </Alert>
          )}
          <Text>
            {t("delete.confirm", { email: deleteTarget?.email ?? "" })}
          </Text>
          <Group justify="flex-end">
            <Button variant="subtle" onClick={closeDeleteModal}>
              {t("delete.cancel")}
            </Button>
            <Button
              data-testid="delete-confirm-btn"
              color="red"
              loading={deleteLoading}
              onClick={() => void handleDeleteConfirm()}
            >
              {t("delete.confirm_btn")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ── Reset password modal ── */}
      <Modal
        opened={resetModalOpened}
        onClose={handleCloseReset}
        title={t("resetPassword.title")}
      >
        <Stack gap="md">
          {resetError && (
            <Alert icon={<AlertCircle size={16} />} color="red">
              {resetError}
            </Alert>
          )}
          {!resetResult ? (
            <>
              <Text size="sm">
                {t("resetPassword.prompt", { email: resetTarget?.email ?? "" })}
              </Text>
              <Group justify="flex-end">
                <Button variant="subtle" onClick={handleCloseReset}>
                  {tCommon("actions.cancel")}
                </Button>
                <Button
                  data-testid="reset-issue-btn"
                  loading={resetLoading}
                  onClick={() => void handleResetPassword()}
                >
                  {t("actions.generateLink")}
                </Button>
              </Group>
            </>
          ) : (
            <Stack gap="sm">
              <Text size="sm" fw={500}>
                {t("resetPassword.resetLink")}
              </Text>
              <Text
                data-testid="reset-url-display"
                size="xs"
                ff="monospace"
                style={{ wordBreak: "break-all" }}
              >
                {resetResult.reset_url}
              </Text>
              <Button
                data-testid="reset-copy-btn"
                variant="outline"
                size="xs"
                onClick={() => safeCopy(resetResult.reset_url)}
              >
                {t("resetPassword.copyLink")}
              </Button>
              {resetResult.emailed ? (
                <Text data-testid="reset-emailed" size="sm" c="teal">
                  {t("resetPassword.emailSent")}
                </Text>
              ) : (
                <Text data-testid="reset-not-emailed" size="sm" c="dimmed">
                  {t("resetPassword.emailNotSent")}
                </Text>
              )}
              <Group justify="flex-end">
                <Button variant="subtle" onClick={handleCloseReset}>
                  {t("resetPassword.done")}
                </Button>
              </Group>
            </Stack>
          )}
        </Stack>
      </Modal>

      {/* ── Invite modal ── */}
      <Modal
        opened={inviteModalOpened}
        onClose={handleCloseInviteModal}
        title={t("invite.title")}
      >
        <Stack gap="md">
          {!inviteResult ? (
            <>
              {/* user.email_exists surfaces here */}
              {inviteError && (
                <Alert
                  data-testid="invite-email-exists-error"
                  icon={<AlertCircle size={16} />}
                  color="red"
                >
                  {inviteError}
                </Alert>
              )}
              <TextInput
                data-testid="invite-email-input"
                label={t("invite.emailLabel")}
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.currentTarget.value)}
                type="email"
                required
              />
              <div data-testid="invite-role-select">
                <Select
                  label={t("invite.roleLabel")}
                  value={inviteRole}
                  data={roleData}
                  onChange={setInviteRole}
                  allowDeselect={false}
                                  />
              </div>
              <Group justify="flex-end">
                <Button variant="subtle" onClick={handleCloseInviteModal}>
                  {tCommon("actions.cancel")}
                </Button>
                <Button
                  data-testid="invite-submit-btn"
                  loading={inviteLoading}
                  onClick={() => void handleInviteSubmit()}
                >
                  {t("invite.submit")}
                </Button>
              </Group>
            </>
          ) : (
            /* Invite result: show accept_url + copy + emailed status */
            <Stack gap="sm" data-testid="invite-result">
              <Text fw={500}>{t("invite.successTitle")}</Text>
              <Text size="sm">{t("invite.acceptLink")}</Text>
              <Text
                data-testid="invite-accept-url"
                size="xs"
                ff="monospace"
                style={{ wordBreak: "break-all" }}
              >
                {inviteResult.accept_url}
              </Text>
              <Button
                data-testid="invite-copy-btn"
                variant="outline"
                size="xs"
                onClick={() => safeCopy(inviteResult.accept_url)}
              >
                {t("invite.copyLink")}
              </Button>
              {inviteResult.emailed ? (
                <Text data-testid="invite-emailed" size="sm" c="teal">
                  {t("invite.emailSent")}
                </Text>
              ) : (
                <Text data-testid="invite-not-emailed" size="sm" c="dimmed">
                  {t("invite.emailNotSent")}
                </Text>
              )}
              <Group justify="flex-end">
                <Button variant="subtle" onClick={handleCloseInviteModal}>
                  {t("invite.done")}
                </Button>
              </Group>
            </Stack>
          )}
        </Stack>
      </Modal>
    </PageShell>
  );
}

/**
 * UsersIcon re-export — used by AppShell NavContent for the nav item icon.
 * Keeping it co-located so the nav import is a single line.
 */
export { UsersIcon };
