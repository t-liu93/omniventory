/**
 * /search — Global search results page (M5 Step 12).
 *
 * Reads ?q from the URL, calls GET /api/search?q=,
 * renders results grouped by entity type with per-type headings, counts
 * (from totals), and subject links:
 *   item_definitions → /items/:id
 *   stock_instances  → /instances/:id
 *   locations        → /locations  (no per-id detail route)
 *   categories       → /categories (no per-id detail route)
 *   tags             → /items      (closest sensible surface)
 *
 * States:
 *   Empty q      → prompt-to-search
 *   Loading      → spinner
 *   API error    → ErrorState
 *   No hits      → no-results message
 *   Has hits     → grouped result lists
 *
 * Capping: when totals.X > results.X.length, shows a "Showing N of M" badge.
 *
 * The page also exposes a search TextInput for refining the query (updates
 * the URL on submit), which is the primary entry point for mobile users
 * arriving via the nav drawer.
 */
import { useEffect, useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ActionIcon,
  Anchor,
  Badge,
  Group,
  Loader,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { Search as SearchIcon } from "react-feather";
import { PageShell } from "../components/PageShell";
import { ErrorState } from "../components/ErrorState";
import { client } from "../api/client";
import type { components } from "../api/schema";

type SearchResponse = components["schemas"]["SearchResponse"];

export function Search() {
  const { t } = useTranslation("search");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const q = (searchParams.get("q") ?? "").trim();

  // Local input state — tracks what the user is typing in the page-level search box.
  const [inputValue, setInputValue] = useState(q);

  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the page-level input in sync when q changes (e.g. browser back/forward).
  useEffect(() => {
    setInputValue(q);
  }, [q]);

  useEffect(() => {
    if (!q) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    async function load() {
      const { data: res, error: apiError } = await client.GET("/api/search", {
        params: { query: { q } },
      });
      if (cancelled) return;
      if (apiError || !res) {
        setError(t("loadError"));
        setLoading(false);
        return;
      }
      setData(res);
      setLoading(false);
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [q, t]);

  function submitSearch() {
    const trimmed = inputValue.trim();
    if (trimmed) {
      navigate(`/search?q=${encodeURIComponent(trimmed)}`);
    }
  }

  const hasResults =
    data !== null &&
    (data.item_definitions.length > 0 ||
      data.stock_instances.length > 0 ||
      data.locations.length > 0 ||
      data.categories.length > 0 ||
      data.tags.length > 0);

  return (
    <PageShell title={t("title")}>
      {/* Page-level search input (for mobile users + query refinement) */}
      <Group mb="md">
        <TextInput
          placeholder={t("placeholder")}
          value={inputValue}
          onChange={(e) => setInputValue(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitSearch();
          }}
          rightSection={
            <ActionIcon
              variant="subtle"
              size="sm"
              onClick={submitSearch}
              aria-label={t("placeholder")}
              data-testid="page-search-submit"
            >
              <SearchIcon size={14} />
            </ActionIcon>
          }
          style={{ flex: 1, maxWidth: 480 }}
          data-testid="page-search-input"
        />
      </Group>

      {/* Empty query → prompt */}
      {!q && (
        <Text
          c="dimmed"
          size="sm"
          ta="center"
          py="xl"
          data-testid="search-prompt"
        >
          {t("prompt")}
        </Text>
      )}

      {/* Loading */}
      {q && loading && (
        <Group justify="center" py="xl">
          <Loader size="sm" data-testid="search-loading" />
        </Group>
      )}

      {/* Error */}
      {q && !loading && error && <ErrorState message={error} />}

      {/* No results */}
      {q && !loading && !error && data !== null && !hasResults && (
        <Text
          c="dimmed"
          size="sm"
          ta="center"
          py="xl"
          data-testid="search-no-results"
        >
          {t("noResults", { q })}
        </Text>
      )}

      {/* Results grouped by type */}
      {q && !loading && !error && hasResults && (
        <Stack gap="xl" data-testid="search-results">
          {/* Item definitions */}
          {data!.item_definitions.length > 0 && (
            <Stack gap="sm" data-testid="group-item_definitions">
              <Group gap="xs">
                <Title order={4}>
                  {t("groups.item_definitions", {
                    count: data!.totals.item_definitions,
                  })}
                </Title>
                {data!.totals.item_definitions >
                  data!.item_definitions.length && (
                  <Badge
                    color="gray"
                    size="sm"
                    data-testid="cap-item_definitions"
                  >
                    {t("groupMore", {
                      shown: data!.item_definitions.length,
                      total: data!.totals.item_definitions,
                    })}
                  </Badge>
                )}
              </Group>
              <Stack gap={4}>
                {data!.item_definitions.map((hit) => (
                  <Anchor
                    key={hit.id}
                    component={Link}
                    to={`/items/${hit.id}`}
                    size="sm"
                    data-testid={`result-item-def-${hit.id}`}
                  >
                    {hit.name}
                  </Anchor>
                ))}
              </Stack>
            </Stack>
          )}

          {/* Stock instances */}
          {data!.stock_instances.length > 0 && (
            <Stack gap="sm" data-testid="group-stock_instances">
              <Group gap="xs">
                <Title order={4}>
                  {t("groups.stock_instances", {
                    count: data!.totals.stock_instances,
                  })}
                </Title>
                {data!.totals.stock_instances >
                  data!.stock_instances.length && (
                  <Badge
                    color="gray"
                    size="sm"
                    data-testid="cap-stock_instances"
                  >
                    {t("groupMore", {
                      shown: data!.stock_instances.length,
                      total: data!.totals.stock_instances,
                    })}
                  </Badge>
                )}
              </Group>
              <Stack gap={4}>
                {data!.stock_instances.map((hit) => (
                  <Anchor
                    key={hit.id}
                    component={Link}
                    to={`/instances/${hit.id}`}
                    size="sm"
                    data-testid={`result-instance-${hit.id}`}
                  >
                    {hit.definition_name}
                    {hit.serial ? ` (${hit.serial})` : ""}
                  </Anchor>
                ))}
              </Stack>
            </Stack>
          )}

          {/* Locations */}
          {data!.locations.length > 0 && (
            <Stack gap="sm" data-testid="group-locations">
              <Group gap="xs">
                <Title order={4}>
                  {t("groups.locations", { count: data!.totals.locations })}
                </Title>
                {data!.totals.locations > data!.locations.length && (
                  <Badge color="gray" size="sm" data-testid="cap-locations">
                    {t("groupMore", {
                      shown: data!.locations.length,
                      total: data!.totals.locations,
                    })}
                  </Badge>
                )}
              </Group>
              <Stack gap={4}>
                {data!.locations.map((hit) => (
                  <Anchor
                    key={hit.id}
                    component={Link}
                    to="/locations"
                    size="sm"
                    data-testid={`result-location-${hit.id}`}
                  >
                    {hit.name}
                  </Anchor>
                ))}
              </Stack>
            </Stack>
          )}

          {/* Categories */}
          {data!.categories.length > 0 && (
            <Stack gap="sm" data-testid="group-categories">
              <Group gap="xs">
                <Title order={4}>
                  {t("groups.categories", { count: data!.totals.categories })}
                </Title>
                {data!.totals.categories > data!.categories.length && (
                  <Badge color="gray" size="sm" data-testid="cap-categories">
                    {t("groupMore", {
                      shown: data!.categories.length,
                      total: data!.totals.categories,
                    })}
                  </Badge>
                )}
              </Group>
              <Stack gap={4}>
                {data!.categories.map((hit) => (
                  <Anchor
                    key={hit.id}
                    component={Link}
                    to="/categories"
                    size="sm"
                    data-testid={`result-category-${hit.id}`}
                  >
                    {hit.name}
                  </Anchor>
                ))}
              </Stack>
            </Stack>
          )}

          {/* Tags */}
          {data!.tags.length > 0 && (
            <Stack gap="sm" data-testid="group-tags">
              <Group gap="xs">
                <Title order={4}>
                  {t("groups.tags", { count: data!.totals.tags })}
                </Title>
                {data!.totals.tags > data!.tags.length && (
                  <Badge color="gray" size="sm" data-testid="cap-tags">
                    {t("groupMore", {
                      shown: data!.tags.length,
                      total: data!.totals.tags,
                    })}
                  </Badge>
                )}
              </Group>
              <Stack gap={4}>
                {data!.tags.map((hit) => (
                  <Anchor
                    key={hit.id}
                    component={Link}
                    to="/items"
                    size="sm"
                    data-testid={`result-tag-${hit.id}`}
                  >
                    {hit.name}
                  </Anchor>
                ))}
              </Stack>
            </Stack>
          )}
        </Stack>
      )}
    </PageShell>
  );
}
