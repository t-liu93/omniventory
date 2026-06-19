/**
 * Dashboard — welcome overview and module entry-point cards.
 *
 * Layout: SimpleGrid (single column on mobile, three columns on md+).
 *
 * Card 1 (expiryCard): static placeholder — Best-before / Expiry (M3).
 * Card 2 (durableCard): static placeholder linking to /items.
 * Card 3 (lowStockCard): LIVE — fetches GET /api/low-stock and shows
 *   a count + short list.  Empty state when nothing is low.
 *   Links to /low-stock for the full list.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  SimpleGrid,
  Card,
  Text,
  Title,
  Badge,
  Stack,
  ThemeIcon,
  Group,
  Anchor,
  Loader,
  List,
} from "@mantine/core";
import { Clock, Archive, TrendingDown } from "react-feather";
import { PageShell } from "../components/PageShell";
import { client } from "../api/client";
import { formatQuantity } from "../i18n/format";
import type { components } from "../api/schema";

type LowStockItem = components["schemas"]["LowStockItem"];

// ── Static concept card ───────────────────────────────────────────────────────

interface ConceptCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  badgeLabel: string;
  /** Existing route to link to. Omit for purely future features. */
  linkTo?: string;
  linkLabel?: string;
}

function ConceptCard({
  icon,
  title,
  description,
  badgeLabel,
  linkTo,
  linkLabel,
}: ConceptCardProps) {
  return (
    <Card component="article">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <ThemeIcon size={44} radius="md" variant="light">
            {icon}
          </ThemeIcon>
          <Badge variant="light" color="gray" size="sm">
            {badgeLabel}
          </Badge>
        </Group>

        <Stack gap={6}>
          <Title order={3} size="h4">
            {title}
          </Title>
          <Text c="dimmed" size="sm" lh={1.5}>
            {description}
          </Text>
        </Stack>

        {linkTo && linkLabel && (
          <Anchor component={Link} to={linkTo} size="sm" mt="auto">
            {linkLabel}
          </Anchor>
        )}
      </Stack>
    </Card>
  );
}

// ── Live low-stock tile ───────────────────────────────────────────────────────

/**
 * The consumable / low-stock card:
 *   - Fetches GET /api/low-stock once on mount.
 *   - Shows a count + short list (up to 3 items inline; link to full view).
 *   - Empty state when nothing is low.
 *   - Never re-derives the low-stock rule client-side (backend owns the rule).
 */
function LowStockCard() {
  const { t } = useTranslation("dashboard");
  const { t: tStock } = useTranslation("stock");

  const [items, setItems] = useState<LowStockItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const result = await client.GET("/api/low-stock");
      if (cancelled) return;
      const data = result?.data;
      if (Array.isArray(data)) {
        setItems(data);
      } else {
        setFetchError(true);
      }
      setLoading(false);
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const previewItems = items?.slice(0, 3) ?? [];
  const count = items?.length ?? 0;

  return (
    <Card component="article" data-testid="low-stock-tile">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <ThemeIcon size={44} radius="md" variant="light" color="orange">
            <TrendingDown size={22} strokeWidth={1.5} />
          </ThemeIcon>
          {!loading && !fetchError && count > 0 && (
            <Badge
              variant="filled"
              color="orange"
              size="sm"
              data-testid="low-stock-count-badge"
            >
              {t("lowStockCard.countLabel", { count })}
            </Badge>
          )}
        </Group>

        <Stack gap={6}>
          <Title order={3} size="h4">
            {t("lowStockCard.title")}
          </Title>

          {loading && <Loader size="xs" />}

          {!loading && fetchError && (
            <Text
              c="dimmed"
              size="sm"
              lh={1.5}
              data-testid="low-stock-load-error"
            >
              {t("lowStockCard.loadError")}
            </Text>
          )}

          {!loading && !fetchError && count === 0 && (
            <Text
              c="dimmed"
              size="sm"
              lh={1.5}
              data-testid="low-stock-empty-state"
            >
              {t("lowStockCard.emptyState")}
            </Text>
          )}

          {!loading && !fetchError && count > 0 && (
            <List
              size="sm"
              spacing={4}
              data-testid="low-stock-list"
            >
              {previewItems.map((item) => (
                <List.Item key={item.definition_id} data-testid={`low-stock-item-${item.definition_id}`}>
                  <Text size="sm" span fw={500}>
                    {item.name}
                  </Text>
                  {item.mode === "exact" ? (
                    <Text size="sm" span c="dimmed">
                      {" "}
                      {formatQuantity(item.current)}
                      {" / "}
                      {formatQuantity(item.threshold)}
                    </Text>
                  ) : (
                    <Text size="sm" span c="orange">
                      {" "}
                      ({tStock("stockLevel.low")})
                    </Text>
                  )}
                </List.Item>
              ))}
            </List>
          )}
        </Stack>

        {!loading && !fetchError && count > 0 && (
          <Anchor
            component={Link}
            to="/low-stock"
            size="sm"
            mt="auto"
            data-testid="low-stock-view-link"
          >
            {t("lowStockCard.viewAll")}
          </Anchor>
        )}
      </Stack>
    </Card>
  );
}

// ── Dashboard page ────────────────────────────────────────────────────────────

export function Dashboard() {
  const { t: tNav } = useTranslation("nav");
  const { t } = useTranslation("dashboard");

  return (
    <PageShell title={tNav("dashboard")} subtitle={t("subtitle")}>
      <SimpleGrid cols={{ base: 1, md: 3 }} spacing="md">
        <ConceptCard
          icon={<Clock size={22} strokeWidth={1.5} />}
          title={t("expiryCard.title")}
          description={t("expiryCard.description")}
          badgeLabel={t("expiryCard.badge")}
        />

        <ConceptCard
          icon={<Archive size={22} strokeWidth={1.5} />}
          title={t("durableCard.title")}
          description={t("durableCard.description")}
          badgeLabel={t("durableCard.badge")}
          linkTo="/items"
          linkLabel={tNav("items")}
        />

        <LowStockCard />
      </SimpleGrid>
    </PageShell>
  );
}
