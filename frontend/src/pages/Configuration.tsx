/**
 * Configuration page — reminders + notification channels.
 *
 * Sections:
 *  1. Reminders (global defaults): best_before_lead_days, warranty_lead_days,
 *     low_stock_repeat_days, scan_time — via PATCH /api/settings.
 *  2. Channels:
 *     a. Email / SMTP — enabled, host, port, username, password (write-only),
 *        encryption (none/starttls/ssl), from_address, from_name; test-connection button.
 *     b. HTTP — enabled, webhook_url, auth_header (write-only),
 *        integration_token (write-only, with regenerate + copy + state-URL hint).
 *     b. MQTT — enabled, host, port, username, password (write-only),
 *        topic_prefix, use_tls, discovery_enabled, commands_enabled.
 *  4. Run scan now — POST /api/reminders/run, shows per-source counts.
 *
 * Secrets policy (§2 "Channel secrets write-only"):
 *  - Password/token fields are NEVER pre-filled from the API (the API only
 *    returns *_is_set booleans).
 *  - The UI shows "Set" / "Not set" status.
 *  - A new value is only sent when the user types in the field.
 *  - Explicit clear: separate "Clear" button that sends null/empty.
 *
 * Integration token:
 *  - Status shown from integration_token_is_set.
 *  - "Generate new token": client-side crypto.randomUUID(), displayed once for
 *    copy, written to settings via PATCH on "Save HTTP settings".
 *  - State endpoint URL hint: ${location.origin}/api/integrations/state?token=…
 *
 * Only changed fields are sent on each section save (partial PATCH).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Code,
  CopyButton,
  Divider,
  Group,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { AlertCircle } from "react-feather";
import { useTranslation } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import { notifySuccess } from "../components/notify";
import { PageShell } from "../components/PageShell";
import { LoadingState } from "../components/LoadingState";
import { ErrorState } from "../components/ErrorState";
import type { components } from "../api/schema";

// ── Schema types ──────────────────────────────────────────────────────────────

type SettingsResponse = components["schemas"]["SettingsResponse"];
type LlmTestResult = components["schemas"]["LlmTestResult"];

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Validate "HH:MM" time format. */
function isValidScanTime(value: string): boolean {
  return /^\d{2}:\d{2}$/.test(value);
}

/** Parse a comma-separated string of integers. Returns null if invalid. */
function parseRepeatDays(raw: string): number[] | null {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  const parts = trimmed.split(",").map((s) => s.trim());
  const nums: number[] = [];
  for (const p of parts) {
    const n = parseInt(p, 10);
    if (isNaN(n) || n < 1 || String(n) !== p) return null;
    nums.push(n);
  }
  return nums;
}

/** Generate a strong random token using the Web Crypto API. */
function generateToken(): string {
  return crypto.randomUUID().replace(/-/g, "") + crypto.randomUUID().replace(/-/g, "");
}

/**
 * Parse a LLM test-stage detail string that may be a pure error code
 * ("llm.auth_failed") or a composite code + model reply
 * ("llm.not_multimodal: 'I see a cat.'", as emitted by the backend §4.3).
 * Returns the leading code token and any trailing model reply (free text).
 * Pure-code strings (no ": " separator) yield an empty reply.
 */
function parseStageDetail(detail: string): { code: string; reply: string } {
  const sep = detail.indexOf(": ");
  if (sep === -1) return { code: detail, reply: "" };
  return { code: detail.slice(0, sep).trim(), reply: detail.slice(sep + 2).trim() };
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

/** Write-only secret field: shows "Set / Not set" badge; input only when user wants to change. */
function SecretField({
  label,
  isSet,
  newValue,
  placeholder,
  clearLabel,
  onNewValueChange,
  onClear,
  testIdPrefix,
  disabled,
}: {
  label: string;
  isSet: boolean;
  newValue: string;
  placeholder: string;
  clearLabel: string;
  onNewValueChange: (v: string) => void;
  onClear: () => void;
  testIdPrefix: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation("configuration");
  return (
    <Stack gap={4}>
      <Group gap={8} align="center">
        <Text size="sm" fw={500}>
          {label}
        </Text>
        <Badge
          size="xs"
          color={isSet ? "teal" : "gray"}
          variant="light"
          data-testid={`${testIdPrefix}-status`}
        >
          {isSet ? t("secret.set") : t("secret.notSet")}
        </Badge>
      </Group>
      <Group gap={8} align="flex-end">
        <TextInput
          style={{ flex: 1 }}
          type="password"
          placeholder={placeholder}
          value={newValue}
          onChange={(e) => onNewValueChange(e.currentTarget.value)}
          data-testid={`${testIdPrefix}-input`}
          disabled={disabled}
        />
        <Button
          size="xs"
          variant="subtle"
          color="red"
          onClick={onClear}
          data-testid={`${testIdPrefix}-clear-btn`}
          disabled={disabled}
        >
          {clearLabel}
        </Button>
      </Group>
    </Stack>
  );
}

/**
 * MQTT is shelved pending a future rewrite (see M4 walkthrough). The whole
 * MQTT section in the Configuration UI is disabled while this flag is true;
 * flip to false (and drop the "unsupported" note) when MQTT is reworked.
 * Annotated `: boolean` so the constant doesn't trip no-unnecessary-condition.
 */
const MQTT_TEMPORARILY_DISABLED: boolean = true;

// ── Configuration page ────────────────────────────────────────────────────────

export function Configuration() {
  const { t } = useTranslation("configuration");
  const { t: tLlm } = useTranslation("llm");

  // ── Loading state ──
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [settings, setSettings] = useState<SettingsResponse | null>(null);

  // ── Reminders (global) form ──
  const [bbLeadDays, setBbLeadDays] = useState<string>("");
  const [wLeadDays, setWLeadDays] = useState<string>("");
  const [maintenanceLeadDays, setMaintenanceLeadDays] = useState<string>("");
  const [repeatDaysRaw, setRepeatDaysRaw] = useState<string>("");
  const [scanTime, setScanTime] = useState<string>("");
  const [remindersBusy, setRemindersBusy] = useState(false);
  const [remindersError, setRemindersError] = useState<string | null>(null);

  // ── Shopping list form ──
  const [autoAddLowStock, setAutoAddLowStock] = useState(true);
  const [shoppingListBusy, setShoppingListBusy] = useState(false);
  const [shoppingListError, setShoppingListError] = useState<string | null>(null);

  // ── Email channel form ──
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [emailHost, setEmailHost] = useState<string>("");
  const [emailPort, setEmailPort] = useState<string>("");
  const [emailUsername, setEmailUsername] = useState<string>("");
  const [emailNewPassword, setEmailNewPassword] = useState<string>("");
  const [emailClearPassword, setEmailClearPassword] = useState(false);
  const [emailPasswordIsSet, setEmailPasswordIsSet] = useState(false);
  const [emailEncryption, setEmailEncryption] = useState<string>("none");
  const [emailFromAddress, setEmailFromAddress] = useState<string>("");
  const [emailFromName, setEmailFromName] = useState<string>("");
  const [emailBusy, setEmailBusy] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [emailTestBusy, setEmailTestBusy] = useState(false);
  const [emailTestResult, setEmailTestResult] = useState<{ ok: boolean; detail: string | null; recipient: string } | null>(null);

  // ── HTTP channel form ──
  const [httpEnabled, setHttpEnabled] = useState(false);
  const [httpWebhookUrl, setHttpWebhookUrl] = useState<string>("");
  const [httpNewAuthHeader, setHttpNewAuthHeader] = useState<string>("");
  const [httpClearAuthHeader, setHttpClearAuthHeader] = useState(false);
  const [httpAuthHeaderIsSet, setHttpAuthHeaderIsSet] = useState(false);
  const [httpTokenIsSet, setHttpTokenIsSet] = useState(false);
  const [httpNewToken, setHttpNewToken] = useState<string | null>(null); // generated but not yet saved
  const [httpBusy, setHttpBusy] = useState(false);
  const [httpError, setHttpError] = useState<string | null>(null);

  // ── MQTT channel form ──
  const [mqttEnabled, setMqttEnabled] = useState(false);
  const [mqttHost, setMqttHost] = useState<string>("");
  const [mqttPort, setMqttPort] = useState<string>("");
  const [mqttUsername, setMqttUsername] = useState<string>("");
  const [mqttNewPassword, setMqttNewPassword] = useState<string>("");
  const [mqttClearPassword, setMqttClearPassword] = useState(false);
  const [mqttPasswordIsSet, setMqttPasswordIsSet] = useState(false);
  const [mqttTopicPrefix, setMqttTopicPrefix] = useState<string>("");
  const [mqttUseTls, setMqttUseTls] = useState(false);
  const [mqttDiscoveryEnabled, setMqttDiscoveryEnabled] = useState(false);
  const [mqttCommandsEnabled, setMqttCommandsEnabled] = useState(false);
  const [mqttBusy, setMqttBusy] = useState(false);
  const [mqttError, setMqttError] = useState<string | null>(null);
  const [mqttTestBusy, setMqttTestBusy] = useState(false);
  const [mqttTestResult, setMqttTestResult] = useState<{ ok: boolean; detail: string | null; topic: string } | null>(null);

  // ── LLM provider form ──
  const [llmEnabled, setLlmEnabled] = useState(false);
  const [llmBaseUrl, setLlmBaseUrl] = useState<string>("");
  const [llmModel, setLlmModel] = useState<string>("");
  const [llmNewApiKey, setLlmNewApiKey] = useState<string>("");
  const [llmClearApiKey, setLlmClearApiKey] = useState(false);
  const [llmApiKeyIsSet, setLlmApiKeyIsSet] = useState(false);
  const [llmBusy, setLlmBusy] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [llmTestBusy, setLlmTestBusy] = useState(false);
  const [llmTestResult, setLlmTestResult] = useState<LlmTestResult | null>(null);

  // ── Run scan ──
  const [scanBusy, setScanBusy] = useState(false);
  const [scanResult, setScanResult] = useState<{ best_before: number; warranty: number; low_stock: number; maintenance: number } | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);

  // ── Load data ─────────────────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const settingsRes = await client.GET("/api/settings");

      if (settingsRes.error || !settingsRes.data) {
        setLoadError(t("loadError"));
        return;
      }

      const s = settingsRes.data;
      setSettings(s);

      // Populate reminders (global) form
      setBbLeadDays(String(s.reminders.best_before_lead_days));
      setWLeadDays(String(s.reminders.warranty_lead_days));
      setMaintenanceLeadDays(String(s.reminders.maintenance_lead_days));
      setRepeatDaysRaw(s.reminders.low_stock_repeat_days.join(","));
      setScanTime(s.reminders.scan_time);

      // Populate shopping list form
      setAutoAddLowStock(s.shopping_list.auto_add_low_stock);

      // Populate email form
      const em = s.channels.email;
      setEmailEnabled(em.enabled);
      setEmailHost(em.host ?? "");
      setEmailPort(em.port != null ? String(em.port) : "");
      setEmailUsername(em.username ?? "");
      setEmailNewPassword("");
      setEmailClearPassword(false);
      setEmailPasswordIsSet(em.password_is_set);
      setEmailEncryption(em.encryption);
      setEmailFromAddress(em.from_address ?? "");
      setEmailFromName(em.from_name ?? "");
      setEmailTestResult(null);

      // Populate HTTP form
      const http = s.channels.http;
      setHttpEnabled(http.enabled);
      setHttpWebhookUrl(http.webhook_url ?? "");
      setHttpNewAuthHeader("");
      setHttpClearAuthHeader(false);
      setHttpAuthHeaderIsSet(http.auth_header_is_set);
      setHttpTokenIsSet(http.integration_token_is_set);
      setHttpNewToken(null);

      // Populate MQTT form
      const mq = s.channels.mqtt;
      setMqttEnabled(mq.enabled);
      setMqttHost(mq.host ?? "");
      setMqttPort(mq.port != null ? String(mq.port) : "");
      setMqttUsername(mq.username ?? "");
      setMqttNewPassword("");
      setMqttClearPassword(false);
      setMqttPasswordIsSet(mq.password_is_set);
      setMqttTopicPrefix(mq.topic_prefix ?? "");
      setMqttUseTls(mq.use_tls);
      setMqttDiscoveryEnabled(mq.discovery_enabled);
      setMqttCommandsEnabled(mq.commands_enabled);
      setMqttTestResult(null);

      // Populate LLM form (defensive: llm block may be absent in test fixtures)
      const llm = s.llm;
      setLlmEnabled(llm?.enabled ?? false);
      setLlmBaseUrl(llm?.base_url ?? "");
      setLlmModel(llm?.model ?? "");
      setLlmNewApiKey("");
      setLlmClearApiKey(false);
      setLlmApiKeyIsSet(llm?.api_key_is_set ?? false);
      setLlmTestResult(null);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // ── Save handlers ─────────────────────────────────────────────────────────

  async function handleSaveReminders() {
    setRemindersBusy(true);
    setRemindersError(null);
    try {
      const repeatDays = parseRepeatDays(repeatDaysRaw);
      if (repeatDays === null) {
        setRemindersError(t("reminders.lowStockRepeatDaysDescription"));
        return;
      }
      if (scanTime && !isValidScanTime(scanTime)) {
        setRemindersError(t("reminders.scanTimeDescription"));
        return;
      }
      const { error } = await client.PATCH("/api/settings", {
        body: {
          reminders: {
            best_before_lead_days: bbLeadDays !== "" ? Number(bbLeadDays) : undefined,
            warranty_lead_days: wLeadDays !== "" ? Number(wLeadDays) : undefined,
            maintenance_lead_days: maintenanceLeadDays !== "" ? Number(maintenanceLeadDays) : undefined,
            low_stock_repeat_days: repeatDays,
            scan_time: scanTime || undefined,
          },
        },
      });
      if (error) {
        setRemindersError(mapApiError(error));
        return;
      }
      notifySuccess(t("reminders.savedReminders"));
      await loadAll();
    } finally {
      setRemindersBusy(false);
    }
  }

  async function handleSaveShoppingList() {
    setShoppingListBusy(true);
    setShoppingListError(null);
    try {
      const { error } = await client.PATCH("/api/settings", {
        body: {
          shopping_list: {
            auto_add_low_stock: autoAddLowStock,
          },
        },
      });
      if (error) {
        setShoppingListError(mapApiError(error));
        return;
      }
      notifySuccess(t("shoppingList.savedShoppingList"));
      await loadAll();
    } finally {
      setShoppingListBusy(false);
    }
  }

  async function handleSaveEmail() {
    setEmailBusy(true);
    setEmailError(null);
    try {
      // Build the password field: clear wins > new value > omit
      let password: string | null | undefined = undefined;
      if (emailClearPassword) {
        password = null;
      } else if (emailNewPassword) {
        password = emailNewPassword;
      }

      const emailUpdate: Record<string, unknown> = {
        enabled: emailEnabled,
        host: emailHost || null,
        port: emailPort !== "" ? Number(emailPort) : null,
        username: emailUsername || null,
        encryption: emailEncryption,
        from_address: emailFromAddress || null,
        from_name: emailFromName || null,
      };
      if (password !== undefined) {
        emailUpdate["password"] = password;
      }

      const { error } = await client.PATCH("/api/settings", {
        body: {
          channels: {
            email: emailUpdate as components["schemas"]["EmailChannelUpdate"],
          },
        },
      });
      if (error) {
        setEmailError(mapApiError(error));
        return;
      }
      notifySuccess(t("email.saved"));
      await loadAll();
    } finally {
      setEmailBusy(false);
    }
  }

  async function handleTestEmail() {
    setEmailTestBusy(true);
    setEmailTestResult(null);
    try {
      const { data, error } = await client.POST("/api/settings/email/test");
      if (error || !data) {
        // Network-level failure (no diagnostic payload). Store the raw reason in
        // `detail`; the render wraps it once with t("email.testFailed", ...).
        setEmailTestResult({ ok: false, detail: t("email.testUnknownError"), recipient: "" });
        return;
      }
      if (data.ok) {
        notifySuccess(t("email.testSuccess", { email: data.recipient }));
      }
      setEmailTestResult(data);
    } finally {
      setEmailTestBusy(false);
    }
  }

  async function handleSaveHttp() {
    setHttpBusy(true);
    setHttpError(null);
    try {
      // Build auth_header: clear wins > new value > omit
      let authHeader: string | null | undefined = undefined;
      if (httpClearAuthHeader) {
        authHeader = null;
      } else if (httpNewAuthHeader) {
        authHeader = httpNewAuthHeader;
      }

      // Build integration_token: pending generated token wins, else omit
      let integrationToken: string | null | undefined = undefined;
      if (httpNewToken !== null) {
        integrationToken = httpNewToken;
      }

      const httpUpdate: Record<string, unknown> = {
        enabled: httpEnabled,
        webhook_url: httpWebhookUrl || null,
      };
      if (authHeader !== undefined) {
        httpUpdate["auth_header"] = authHeader;
      }
      if (integrationToken !== undefined) {
        httpUpdate["integration_token"] = integrationToken;
      }

      const { error } = await client.PATCH("/api/settings", {
        body: {
          channels: {
            http: httpUpdate as components["schemas"]["HttpChannelUpdate"],
          },
        },
      });
      if (error) {
        setHttpError(mapApiError(error));
        return;
      }
      notifySuccess(t("http.saved"));
      await loadAll();
    } finally {
      setHttpBusy(false);
    }
  }

  async function handleSaveMqtt() {
    setMqttBusy(true);
    setMqttError(null);
    try {
      // Build password: clear wins > new value > omit
      let password: string | null | undefined = undefined;
      if (mqttClearPassword) {
        password = null;
      } else if (mqttNewPassword) {
        password = mqttNewPassword;
      }

      const mqttUpdate: Record<string, unknown> = {
        enabled: mqttEnabled,
        host: mqttHost || null,
        port: mqttPort !== "" ? Number(mqttPort) : null,
        username: mqttUsername || null,
        use_tls: mqttUseTls,
        topic_prefix: mqttTopicPrefix || null,
        discovery_enabled: mqttDiscoveryEnabled,
        commands_enabled: mqttCommandsEnabled,
      };
      if (password !== undefined) {
        mqttUpdate["password"] = password;
      }

      const { error } = await client.PATCH("/api/settings", {
        body: {
          channels: {
            mqtt: mqttUpdate as components["schemas"]["MqttChannelUpdate"],
          },
        },
      });
      if (error) {
        setMqttError(mapApiError(error));
        return;
      }
      notifySuccess(t("mqtt.saved"));
      await loadAll();
    } finally {
      setMqttBusy(false);
    }
  }

  async function handleTestMqtt() {
    setMqttTestBusy(true);
    setMqttTestResult(null);
    try {
      const { data, error } = await client.POST("/api/settings/mqtt/test");
      if (error || !data) {
        // Network-level failure (no diagnostic payload). Store the raw reason in
        // `detail`; the render wraps it once with t("mqtt.testFailed", ...).
        setMqttTestResult({ ok: false, detail: t("mqtt.testUnknownError"), topic: "" });
        return;
      }
      if (data.ok) {
        notifySuccess(t("mqtt.testSuccess", { topic: data.topic }));
      }
      setMqttTestResult(data);
    } finally {
      setMqttTestBusy(false);
    }
  }

  async function handleSaveLlm() {
    setLlmBusy(true);
    setLlmError(null);
    try {
      // Build api_key: clear wins > new value > omit
      // "" = clear, non-empty string = set, undefined = omit (keep)
      let apiKey: string | undefined = undefined;
      if (llmClearApiKey) {
        apiKey = "";
      } else if (llmNewApiKey) {
        apiKey = llmNewApiKey;
      }

      const llmUpdate: Record<string, unknown> = {
        enabled: llmEnabled,
        base_url: llmBaseUrl || null,
        model: llmModel || null,
      };
      if (apiKey !== undefined) {
        llmUpdate["api_key"] = apiKey;
      }

      const { error } = await client.PATCH("/api/settings", {
        body: {
          llm: llmUpdate as components["schemas"]["LlmConfigUpdate"],
        },
      });
      if (error) {
        setLlmError(mapApiError(error));
        return;
      }
      notifySuccess(tLlm("saved"));
      await loadAll();
    } finally {
      setLlmBusy(false);
    }
  }

  async function handleTestLlm() {
    setLlmTestBusy(true);
    setLlmTestResult(null);
    setLlmError(null);
    try {
      const { data, error } = await client.POST("/api/settings/llm/test");
      if (error || !data) {
        setLlmError(mapApiError(error));
        return;
      }
      setLlmTestResult(data);
    } finally {
      setLlmTestBusy(false);
    }
  }

  async function handleRunScan() {
    setScanBusy(true);
    setScanResult(null);
    setScanError(null);
    try {
      const { data, error } = await client.POST("/api/reminders/run");
      if (error || !data) {
        setScanError(t("scan.error"));
        return;
      }
      setScanResult(data);
    } finally {
      setScanBusy(false);
    }
  }

  function handleGenerateToken() {
    const token = generateToken();
    setHttpNewToken(token);
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;
  if (!settings) return <ErrorState message={t("loadError")} />;

  const stateEndpointUrl = `${window.location.origin}/api/integrations/state`;

  return (
    <PageShell title={t("page.title")} subtitle={t("page.subtitle")}>
      <Stack gap="xl">

        {/* ── Reminders (global defaults) ──────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{t("section.reminders")}</Title>
            <Divider />

            {remindersError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="reminders-error">
                {remindersError}
              </Alert>
            )}

            <NumberInput
              label={t("reminders.bestBeforeLeadDaysLabel")}
              description={t("reminders.bestBeforeLeadDaysDescription")}
              value={bbLeadDays === "" ? "" : Number(bbLeadDays)}
              onChange={(v) => setBbLeadDays(v === "" ? "" : String(Math.round(Number(v))))}
              min={0}
              allowDecimal={false}
              suffix=" days"
              data-testid="reminders-bb-lead-input"
            />
            <NumberInput
              label={t("reminders.warrantyLeadDaysLabel")}
              description={t("reminders.warrantyLeadDaysDescription")}
              value={wLeadDays === "" ? "" : Number(wLeadDays)}
              onChange={(v) => setWLeadDays(v === "" ? "" : String(Math.round(Number(v))))}
              min={0}
              allowDecimal={false}
              suffix=" days"
              data-testid="reminders-warranty-lead-input"
            />
            <NumberInput
              label={t("reminders.maintenanceLeadDaysLabel")}
              description={t("reminders.maintenanceLeadDaysDescription")}
              value={maintenanceLeadDays === "" ? "" : Number(maintenanceLeadDays)}
              onChange={(v) => setMaintenanceLeadDays(v === "" ? "" : String(Math.round(Number(v))))}
              min={0}
              allowDecimal={false}
              suffix=" days"
              data-testid="reminders-maintenance-lead-input"
            />
            <TextInput
              label={t("reminders.lowStockRepeatDaysLabel")}
              description={t("reminders.lowStockRepeatDaysDescription")}
              placeholder={t("reminders.lowStockRepeatDaysPlaceholder")}
              value={repeatDaysRaw}
              onChange={(e) => setRepeatDaysRaw(e.currentTarget.value)}
              data-testid="reminders-repeat-days-input"
            />
            <TextInput
              label={t("reminders.scanTimeLabel")}
              description={t("reminders.scanTimeDescription")}
              placeholder={t("reminders.scanTimePlaceholder")}
              value={scanTime}
              onChange={(e) => setScanTime(e.currentTarget.value)}
              data-testid="reminders-scan-time-input"
            />

            <Group justify="flex-end">
              <Button
                onClick={() => void handleSaveReminders()}
                loading={remindersBusy}
                data-testid="save-reminders-btn"
              >
                {t("reminders.saveReminders")}
              </Button>
            </Group>
          </Stack>
        </Paper>

        {/* ── Shopping list ─────────────────────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{t("section.shoppingList")}</Title>
            <Divider />

            {shoppingListError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="shopping-list-error">
                {shoppingListError}
              </Alert>
            )}

            <Switch
              label={t("shoppingList.autoAddLowStockLabel")}
              description={t("shoppingList.autoAddLowStockDescription")}
              checked={autoAddLowStock}
              onChange={(e) => setAutoAddLowStock(e.currentTarget.checked)}
              data-testid="shopping-list-auto-add-switch"
            />

            <Group justify="flex-end">
              <Button
                onClick={() => void handleSaveShoppingList()}
                loading={shoppingListBusy}
                data-testid="save-shopping-list-btn"
              >
                {t("shoppingList.saveShoppingList")}
              </Button>
            </Group>
          </Stack>
        </Paper>

        {/* ── Email / SMTP ──────────────────────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{t("section.email")}</Title>
            <Divider />

            {emailError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="email-error">
                {emailError}
              </Alert>
            )}

            <Switch
              label={t("email.enabledLabel")}
              checked={emailEnabled}
              onChange={(e) => setEmailEnabled(e.currentTarget.checked)}
              data-testid="email-enabled-switch"
            />
            <TextInput
              label={t("email.hostLabel")}
              value={emailHost}
              onChange={(e) => setEmailHost(e.currentTarget.value)}
              data-testid="email-host-input"
            />
            <NumberInput
              label={t("email.portLabel")}
              value={emailPort === "" ? "" : Number(emailPort)}
              onChange={(v) => setEmailPort(v === "" ? "" : String(Number(v)))}
              min={1}
              max={65535}
              allowDecimal={false}
              data-testid="email-port-input"
            />
            <TextInput
              label={t("email.usernameLabel")}
              value={emailUsername}
              onChange={(e) => setEmailUsername(e.currentTarget.value)}
              data-testid="email-username-input"
            />
            <SecretField
              label={t("email.passwordLabel")}
              isSet={emailPasswordIsSet}
              newValue={emailNewPassword}
              placeholder={t("email.passwordNewPlaceholder")}
              clearLabel={t("email.clearPassword")}
              onNewValueChange={(v) => {
                setEmailNewPassword(v);
                if (emailClearPassword) setEmailClearPassword(false);
              }}
              onClear={() => {
                setEmailClearPassword(true);
                setEmailNewPassword("");
              }}
              testIdPrefix="email-password"
            />
            <Select
              label={t("email.encryptionLabel")}
              value={emailEncryption}
              onChange={(v) => setEmailEncryption(v ?? "none")}
              data={[
                { value: "none", label: t("email.encryptionNone") },
                { value: "starttls", label: t("email.encryptionStarttls") },
                { value: "ssl", label: t("email.encryptionSsl") },
              ]}
              data-testid="email-encryption-select"
            />
            <TextInput
              label={t("email.fromAddressLabel")}
              value={emailFromAddress}
              onChange={(e) => setEmailFromAddress(e.currentTarget.value)}
              data-testid="email-from-address-input"
            />
            <TextInput
              label={t("email.fromNameLabel")}
              value={emailFromName}
              onChange={(e) => setEmailFromName(e.currentTarget.value)}
              data-testid="email-from-name-input"
            />
            <Text size="xs" c="dimmed">
              {t("email.recipientsNote")}
            </Text>

            {emailTestResult && !emailTestResult.ok && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="email-test-result">
                {t("email.testFailed", { detail: emailTestResult.detail ?? "" })}
              </Alert>
            )}

            <Text size="xs" c="dimmed">
              {t("email.testHint")}
            </Text>

            <Group justify="flex-end">
              <Button
                variant="outline"
                onClick={() => void handleTestEmail()}
                loading={emailTestBusy}
                data-testid="test-email-btn"
              >
                {t("email.testButton")}
              </Button>
              <Button
                onClick={() => void handleSaveEmail()}
                loading={emailBusy}
                data-testid="save-email-btn"
              >
                {t("email.save")}
              </Button>
            </Group>
          </Stack>
        </Paper>

        {/* ── HTTP channel ──────────────────────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{t("section.http")}</Title>
            <Divider />

            {httpError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="http-error">
                {httpError}
              </Alert>
            )}

            <Switch
              label={t("http.enabledLabel")}
              checked={httpEnabled}
              onChange={(e) => setHttpEnabled(e.currentTarget.checked)}
              data-testid="http-enabled-switch"
            />
            <TextInput
              label={t("http.webhookUrlLabel")}
              description={t("http.webhookUrlDescription")}
              value={httpWebhookUrl}
              onChange={(e) => setHttpWebhookUrl(e.currentTarget.value)}
              data-testid="http-webhook-url-input"
            />

            {/* Auth header (write-only) */}
            <SecretField
              label={t("http.authHeaderLabel")}
              isSet={httpAuthHeaderIsSet}
              newValue={httpNewAuthHeader}
              placeholder={t("http.authHeaderNewPlaceholder")}
              clearLabel={t("http.clearAuthHeader")}
              onNewValueChange={(v) => {
                setHttpNewAuthHeader(v);
                if (httpClearAuthHeader) setHttpClearAuthHeader(false);
              }}
              onClear={() => {
                setHttpClearAuthHeader(true);
                setHttpNewAuthHeader("");
              }}
              testIdPrefix="http-auth-header"
            />

            {/* Integration token */}
            <Stack gap={4}>
              <Group gap={8} align="center">
                <Text size="sm" fw={500}>
                  {t("http.integrationTokenLabel")}
                </Text>
                <Badge
                  size="xs"
                  color={httpTokenIsSet || httpNewToken !== null ? "teal" : "gray"}
                  variant="light"
                  data-testid="integration-token-status"
                >
                  {httpTokenIsSet || httpNewToken !== null ? t("secret.set") : t("secret.notSet")}
                </Badge>
              </Group>
              <Text size="xs" c="dimmed">
                {t("http.integrationTokenDescription")}
              </Text>

              {httpNewToken !== null && (
                <Alert color="blue" variant="light" data-testid="new-token-alert">
                  <Stack gap={4}>
                    <Text size="xs" fw={500}>
                      {t("http.integrationTokenGenerated")}
                    </Text>
                    <Group gap={8} align="center">
                      <Code data-testid="new-token-value" style={{ wordBreak: "break-all", flex: 1 }}>
                        {httpNewToken}
                      </Code>
                      <CopyButton value={httpNewToken}>
                        {({ copied, copy }) => (
                          <Tooltip label={copied ? t("http.integrationTokenCopied") : t("http.copyToken")}>
                            <Button
                              size="xs"
                              variant={copied ? "filled" : "outline"}
                              color={copied ? "teal" : "blue"}
                              onClick={copy}
                              data-testid="copy-token-btn"
                            >
                              {t("http.copyToken")}
                            </Button>
                          </Tooltip>
                        )}
                      </CopyButton>
                    </Group>
                  </Stack>
                </Alert>
              )}

              <Button
                size="xs"
                variant="outline"
                onClick={handleGenerateToken}
                data-testid="generate-token-btn"
              >
                {t("http.regenerateToken")}
              </Button>
            </Stack>

            {/* State endpoint URL hint */}
            <Stack gap={4}>
              <Text size="sm" fw={500}>
                {t("http.stateEndpointLabel")}
              </Text>
              <Text size="xs" c="dimmed">
                {t("http.stateEndpointDescription")}
              </Text>
              <Code data-testid="state-endpoint-url">
                {stateEndpointUrl}?token=…
              </Code>
            </Stack>

            <Group justify="flex-end">
              <Button
                onClick={() => void handleSaveHttp()}
                loading={httpBusy}
                data-testid="save-http-btn"
              >
                {t("http.save")}
              </Button>
            </Group>
          </Stack>
        </Paper>

        {/* ── MQTT channel ──────────────────────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{t("section.mqtt")}</Title>
            <Divider />

            {mqttError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="mqtt-error">
                {mqttError}
              </Alert>
            )}

            <Switch
              label={t("mqtt.enabledLabel")}
              checked={mqttEnabled}
              onChange={(e) => setMqttEnabled(e.currentTarget.checked)}
              data-testid="mqtt-enabled-switch"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <Text size="sm" c="dimmed" data-testid="mqtt-unsupported-note">
              {t("mqtt.unsupportedNote")}
            </Text>
            <TextInput
              label={t("mqtt.hostLabel")}
              value={mqttHost}
              onChange={(e) => setMqttHost(e.currentTarget.value)}
              data-testid="mqtt-host-input"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <NumberInput
              label={t("mqtt.portLabel")}
              value={mqttPort === "" ? "" : Number(mqttPort)}
              onChange={(v) => setMqttPort(v === "" ? "" : String(Number(v)))}
              min={1}
              max={65535}
              allowDecimal={false}
              data-testid="mqtt-port-input"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <TextInput
              label={t("mqtt.usernameLabel")}
              value={mqttUsername}
              onChange={(e) => setMqttUsername(e.currentTarget.value)}
              data-testid="mqtt-username-input"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <SecretField
              label={t("mqtt.passwordLabel")}
              isSet={mqttPasswordIsSet}
              newValue={mqttNewPassword}
              placeholder={t("mqtt.passwordNewPlaceholder")}
              clearLabel={t("mqtt.clearPassword")}
              onNewValueChange={(v) => {
                setMqttNewPassword(v);
                if (mqttClearPassword) setMqttClearPassword(false);
              }}
              onClear={() => {
                setMqttClearPassword(true);
                setMqttNewPassword("");
              }}
              testIdPrefix="mqtt-password"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <TextInput
              label={t("mqtt.topicPrefixLabel")}
              value={mqttTopicPrefix}
              onChange={(e) => setMqttTopicPrefix(e.currentTarget.value)}
              data-testid="mqtt-topic-prefix-input"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <Checkbox
              label={t("mqtt.useTlsLabel")}
              checked={mqttUseTls}
              onChange={(e) => setMqttUseTls(e.currentTarget.checked)}
              data-testid="mqtt-use-tls-checkbox"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <Checkbox
              label={t("mqtt.discoveryEnabledLabel")}
              checked={mqttDiscoveryEnabled}
              onChange={(e) => setMqttDiscoveryEnabled(e.currentTarget.checked)}
              data-testid="mqtt-discovery-checkbox"
              disabled={MQTT_TEMPORARILY_DISABLED}
            />
            <Stack gap={4}>
              <Checkbox
                label={t("mqtt.commandsEnabledLabel")}
                checked={mqttCommandsEnabled}
                onChange={(e) => setMqttCommandsEnabled(e.currentTarget.checked)}
                data-testid="mqtt-commands-checkbox"
                disabled={MQTT_TEMPORARILY_DISABLED}
              />
              <Text size="xs" c="dimmed">
                {t("mqtt.commandsEnabledDescription")}
              </Text>
            </Stack>

            {mqttTestResult && !mqttTestResult.ok && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="mqtt-test-result">
                {t("mqtt.testFailed", { detail: mqttTestResult.detail ?? "" })}
              </Alert>
            )}

            <Text size="xs" c="dimmed">
              {t("mqtt.testHint")}
            </Text>

            <Group justify="flex-end">
              <Button
                variant="outline"
                onClick={() => void handleTestMqtt()}
                loading={mqttTestBusy}
                data-testid="test-mqtt-btn"
                disabled={MQTT_TEMPORARILY_DISABLED}
              >
                {t("mqtt.testButton")}
              </Button>
              <Button
                onClick={() => void handleSaveMqtt()}
                loading={mqttBusy}
                data-testid="save-mqtt-btn"
                disabled={MQTT_TEMPORARILY_DISABLED}
              >
                {t("mqtt.save")}
              </Button>
            </Group>
          </Stack>
        </Paper>

        {/* ── LLM provider ──────────────────────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{tLlm("section")}</Title>
            <Divider />

            {llmError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="llm-error">
                {llmError}
              </Alert>
            )}

            <Switch
              label={tLlm("enabledLabel")}
              checked={llmEnabled}
              onChange={(e) => setLlmEnabled(e.currentTarget.checked)}
              data-testid="llm-enabled-switch"
            />
            <TextInput
              label={tLlm("baseUrlLabel")}
              value={llmBaseUrl}
              onChange={(e) => setLlmBaseUrl(e.currentTarget.value)}
              data-testid="llm-base-url-input"
            />
            <TextInput
              label={tLlm("modelLabel")}
              value={llmModel}
              onChange={(e) => setLlmModel(e.currentTarget.value)}
              data-testid="llm-model-input"
            />
            <SecretField
              label={tLlm("apiKeyLabel")}
              isSet={llmApiKeyIsSet}
              newValue={llmNewApiKey}
              placeholder={tLlm("apiKeyPlaceholder")}
              clearLabel={tLlm("clearApiKey")}
              onNewValueChange={(v) => {
                setLlmNewApiKey(v);
                if (llmClearApiKey) setLlmClearApiKey(false);
              }}
              onClear={() => {
                setLlmClearApiKey(true);
                setLlmNewApiKey("");
              }}
              testIdPrefix="llm-api-key"
            />

            {/* Staged test results — rendered inline, failure never blocks */}
            {llmTestResult && (
              <Stack gap="xs" data-testid="llm-test-results">
                {[
                  { id: "connectivity", label: tLlm("testStage.connectivity"), stage: llmTestResult.connectivity },
                  { id: "model_answers", label: tLlm("testStage.modelAnswers"), stage: llmTestResult.model_answers },
                  { id: "multimodal", label: tLlm("testStage.multimodal"), stage: llmTestResult.multimodal },
                ].map(({ id, label, stage }) => {
                  const badgeColor = stage.status === "pass" ? "teal" : stage.status === "fail" ? "red" : "gray";
                  const statusLabel =
                    stage.status === "pass"
                      ? tLlm("testStatus.pass")
                      : stage.status === "fail"
                        ? tLlm("testStatus.fail")
                        : tLlm("testStatus.skipped");
                  // detail may be a pure code ("llm.auth_failed") or a composite
                  // "code: <reply>" string emitted by the backend §4.3 for the
                  // multimodal stage — split so the code is localized and the
                  // model's reply is shown as secondary supplementary text.
                  const stageDetail = stage.detail != null ? parseStageDetail(stage.detail) : null;
                  return (
                    <Stack key={id} gap={2} data-testid={`llm-test-stage-${id}`}>
                      <Group gap={8} align="center">
                        <Text size="sm">{label}</Text>
                        <Badge color={badgeColor} variant="light" data-testid={`llm-test-badge-${id}`}>
                          {statusLabel}
                        </Badge>
                      </Group>
                      {stageDetail && (
                        <>
                          <Text
                            size="xs"
                            c={stage.status === "fail" ? "red" : "dimmed"}
                            data-testid={`llm-test-detail-${id}`}
                          >
                            {mapApiError({ code: stageDetail.code })}
                          </Text>
                          {stageDetail.reply && (
                            <Text size="xs" c="dimmed" data-testid={`llm-test-detail-${id}-reply`}>
                              {stageDetail.reply}
                            </Text>
                          )}
                        </>
                      )}
                    </Stack>
                  );
                })}
              </Stack>
            )}

            <Text size="xs" c="dimmed">
              {tLlm("testHint")}
            </Text>

            <Group justify="flex-end">
              <Button
                variant="outline"
                onClick={() => void handleTestLlm()}
                loading={llmTestBusy}
                data-testid="test-llm-btn"
              >
                {tLlm("testButton")}
              </Button>
              <Button
                onClick={() => void handleSaveLlm()}
                loading={llmBusy}
                data-testid="save-llm-btn"
              >
                {tLlm("save")}
              </Button>
            </Group>
          </Stack>
        </Paper>

        {/* ── Run scan now ──────────────────────────────────────────────── */}
        <Paper withBorder p="md">
          <Stack gap="sm">
            <Title order={4}>{t("section.runScan")}</Title>
            <Divider />

            {scanError && (
              <Alert icon={<AlertCircle size={16} />} color="red" variant="light" data-testid="scan-error">
                {scanError}
              </Alert>
            )}

            {scanResult && (
              <Alert color="teal" variant="light" data-testid="scan-result">
                {t("scan.result", {
                  best_before: scanResult.best_before,
                  warranty: scanResult.warranty,
                  low_stock: scanResult.low_stock,
                  maintenance: scanResult.maintenance,
                })}
              </Alert>
            )}

            <Group>
              <Button
                onClick={() => void handleRunScan()}
                loading={scanBusy}
                data-testid="run-scan-btn"
              >
                {t("scan.runNow")}
              </Button>
            </Group>
          </Stack>
        </Paper>

      </Stack>
    </PageShell>
  );
}
