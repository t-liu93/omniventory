/**
 * Dashboard — welcome overview and module entry-point cards.
 *
 * Shows three concept cards (one per core module) clearly labeled as
 * "coming soon" placeholders. No business data is fetched or displayed.
 * Cards that correspond to an existing route include a link; purely
 * future-milestone features show a dimmed badge only.
 *
 * Layout: SimpleGrid (single column on mobile, three columns on md+).
 */
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
} from "@mantine/core";
import { Clock, Archive, TrendingDown } from "react-feather";
import { PageShell } from "../components/PageShell";

// ── Individual concept card ───────────────────────────────────────────────────

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

        <ConceptCard
          icon={<TrendingDown size={22} strokeWidth={1.5} />}
          title={t("consumableCard.title")}
          description={t("consumableCard.description")}
          badgeLabel={t("consumableCard.badge")}
        />
      </SimpleGrid>
    </PageShell>
  );
}
