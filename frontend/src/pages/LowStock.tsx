/**
 * LowStock page — full list of all currently low-stock definitions.
 *
 * Fetches from GET /api/low-stock and renders a table with:
 *   - exact mode: definition name + current / threshold quantities
 *   - level mode: definition name + "Low" indicator
 *
 * Empty state shown when nothing is low.
 * No pagination needed for M2 (list is bounded by definitions count).
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Anchor,
  Table,
  Text,
  Badge,
  Stack,
  Group,
  Loader,
} from "@mantine/core";
import { PageShell } from "../components/PageShell";
import { ErrorState } from "../components/ErrorState";
import { client } from "../api/client";
import { formatQuantity } from "../i18n/format";
import type { components } from "../api/schema";

type LowStockItem = components["schemas"]["LowStockItem"];

export function LowStock() {
  const { t } = useTranslation("dashboard");

  const [items, setItems] = useState<LowStockItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      const { data, error: apiError } = await client.GET("/api/low-stock");
      if (cancelled) return;
      if (apiError || !Array.isArray(data)) {
        setError(t("lowStockCard.loadError"));
        setLoading(false);
        return;
      }
      setItems(data);
      setLoading(false);
    }

    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <PageShell title={t("lowStockCard.title")}>
      {loading && (
        <Group justify="center" py="xl">
          <Loader size="sm" />
        </Group>
      )}

      {!loading && error && (
        <ErrorState message={error} />
      )}

      {!loading && !error && items !== null && items.length === 0 && (
        <Text
          c="dimmed"
          size="sm"
          ta="center"
          py="xl"
          data-testid="low-stock-empty"
        >
          {t("lowStockCard.emptyState")}
        </Text>
      )}

      {!loading && !error && items !== null && items.length > 0 && (
        <Stack gap="md">
          <Text c="dimmed" size="sm" data-testid="low-stock-count">
            {t("lowStockCard.countLabel", { count: items.length })}
          </Text>

          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("lowStockCard.nameLabel")}</Table.Th>
                <Table.Th>{t("lowStockCard.currentLabel")}</Table.Th>
                <Table.Th>{t("lowStockCard.thresholdLabel")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((item) => (
                <Table.Tr
                  key={item.definition_id}
                  data-testid={`low-stock-row-${item.definition_id}`}
                >
                  <Table.Td>
                    <Anchor component={Link} to={`/items/${item.definition_id}`} size="sm" fw={500}>
                      {item.name}
                    </Anchor>
                  </Table.Td>
                  <Table.Td data-testid={`low-stock-current-${item.definition_id}`}>
                    {item.mode === "exact" ? (
                      <Text size="sm">{formatQuantity(item.current)}</Text>
                    ) : (
                      <Badge color="orange" size="sm" data-testid={`low-stock-level-${item.definition_id}`}>
                        {t("lowStockCard.levelLow")}
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td data-testid={`low-stock-threshold-${item.definition_id}`}>
                    {item.threshold !== null ? (
                      <Text size="sm">{formatQuantity(item.threshold)}</Text>
                    ) : (
                      <Text size="sm" c="dimmed">—</Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </PageShell>
  );
}
