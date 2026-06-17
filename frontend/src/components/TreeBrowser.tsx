/**
 * TreeBrowser — shared tree browse/edit component for self-referential trees.
 *
 * Parameterised by resource so it can be reused identically for Locations and
 * Categories without copy-paste divergence (M1 §10 Step-5 blind-review point).
 *
 * Features:
 *  - Load the full nested tree via GET /{resource}/tree.
 *  - Expand / collapse nodes with Mantine's Tree component.
 *  - Select a node to show/edit it inline.
 *  - Create a child node under a selected (or root) parent.
 *  - Rename a node.
 *  - Reparent a node (change parent by entering a new parent id).
 *  - Delete a node — surfaces the server's 409 guard message when non-empty.
 *  - For locations: shows a badge when the node has item_instance_id set
 *    (container-as-item indicator).
 *  - For locations: the detail panel lists instances physically at that location,
 *    with move-to-another-location and delete actions (Fix 3 — M1 followup).
 *
 * Data access: exclusively via the typed openapi-fetch `client` (no hand-written fetch).
 */
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  Stack,
  Group,
  Text,
  Badge,
  Button,
  ActionIcon,
  TextInput,
  Select,
  Modal,
  Alert,
  Tree,
  useTree,
  Divider,
  Table,
} from "@mantine/core";
import type { TreeNodeData } from "@mantine/core";
import { Plus, Edit2, Trash2, AlertCircle, Move } from "react-feather";
import { useTranslation, Trans } from "react-i18next";
import { client } from "../api/client";
import { mapApiError } from "../i18n/errors";
import type { components } from "../api/schema";
import { LoadingState } from "./LoadingState";
import { ErrorState } from "./ErrorState";
import { EmptyState } from "./EmptyState";
import { formatQuantity } from "../utils";

// ── Resource-specific types ──────────────────────────────────────────────────

/** The two resources this component handles. */
export type TreeResource = "locations" | "categories";

type LocationTreeNode = components["schemas"]["LocationTreeNode"];
type CategoryTreeNode = components["schemas"]["CategoryTreeNode"];
type InstanceResponse = components["schemas"]["InstanceResponse"];

/** Union of both tree-node shapes (categories don't have item_instance_id). */
type AnyTreeNode = LocationTreeNode | CategoryTreeNode;

/**
 * Metadata we attach to each Mantine TreeNodeData.value so we can recover the
 * full node when a tree node is acted on.  We stash the whole node as JSON in
 * the value string (Mantine's value is a string key).
 */
function encodeNodeValue(node: AnyTreeNode): string {
  return String(node.id);
}

/**
 * Convert a backend tree-node (recursive) to Mantine's TreeNodeData shape.
 * The `value` is the node's id as a string; the `label` is the name.
 */
function toMantineTree(nodes: AnyTreeNode[]): TreeNodeData[] {
  return nodes.map((n) => ({
    value: encodeNodeValue(n),
    label: n.name,
    children:
      n.children && n.children.length > 0
        ? toMantineTree(n.children as AnyTreeNode[])
        : undefined,
  }));
}

// ── Internal state types ─────────────────────────────────────────────────────

type ModalState =
  | { kind: "none" }
  | { kind: "create"; parentId: number | null }
  | { kind: "rename"; nodeId: number; currentName: string }
  | { kind: "reparent"; nodeId: number; currentParentId: number | null }
  | { kind: "delete"; nodeId: number; nodeName: string }
  | { kind: "moveInstance"; instance: InstanceResponse }
  | { kind: "deleteInstance"; instance: InstanceResponse }
  | { kind: "linkContainerAsset"; locationId: number }
  | { kind: "unlinkContainerAsset"; locationId: number };

// ── Main component ───────────────────────────────────────────────────────────

interface TreeBrowserProps {
  resource: TreeResource;
}

export function TreeBrowser({ resource }: TreeBrowserProps) {
  const ns = resource; // "locations" or "categories"
  const { t } = useTranslation(ns);

  const [treeData, setTreeData] = useState<AnyTreeNode[]>([]);
  const [flatMap, setFlatMap] = useState<Map<number, AnyTreeNode>>(new Map());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "none" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Form state for modals
  const [formName, setFormName] = useState("");
  // For the reparent modal: "" means root (null parent), otherwise a stringified node id.
  const [formParentId, setFormParentId] = useState<string>("");
  // For the move-instance modal: the target location id as a string.
  const [moveTargetId, setMoveTargetId] = useState<string>("");
  // For the link-container-asset modal: the chosen instance id as a string.
  const [linkInstanceId, setLinkInstanceId] = useState<string>("");

  // ── Location instances (Fix 3) ─────────────────────────────────────────────
  // Instances at the currently selected location (locations only).
  const [locationInstances, setLocationInstances] = useState<InstanceResponse[]>([]);
  const [instancesLoading, setInstancesLoading] = useState(false);
  // definition_id → definition name for display.
  const [definitionNames, setDefinitionNames] = useState<Map<number, string>>(new Map());
  // Ref kept in sync with definitionNames so loadLocationInstances can read the
  // latest cache without being in its dependency array (avoids redundant refetches).
  const definitionNamesRef = useRef(definitionNames);
  useEffect(() => {
    definitionNamesRef.current = definitionNames;
  }, [definitionNames]);

  // ── All instances (for container-asset picker) ─────────────────────────────
  // Full list of all instances, loaded once for the link-container-asset modal.
  const [allInstances, setAllInstances] = useState<InstanceResponse[]>([]);
  const [allInstancesLoading, setAllInstancesLoading] = useState(false);

  const tree = useTree();

  // ── Flatten helper ─────────────────────────────────────────────────────────
  function buildFlatMap(nodes: AnyTreeNode[]): Map<number, AnyTreeNode> {
    const map = new Map<number, AnyTreeNode>();
    function walk(ns: AnyTreeNode[]) {
      for (const n of ns) {
        map.set(n.id, n);
        if (n.children?.length) walk(n.children as AnyTreeNode[]);
      }
    }
    walk(nodes);
    return map;
  }

  // ── Data loading ───────────────────────────────────────────────────────────
  const loadTree = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      if (resource === "locations") {
        const { data, error } = await client.GET("/api/locations/tree");
        if (error || !data) {
          setLoadError(t("tree.loadError"));
        } else {
          const nodes = data as LocationTreeNode[];
          setTreeData(nodes);
          setFlatMap(buildFlatMap(nodes));
        }
      } else {
        const { data, error } = await client.GET("/api/categories/tree");
        if (error || !data) {
          setLoadError(t("tree.loadError"));
        } else {
          const nodes = data as CategoryTreeNode[];
          setTreeData(nodes);
          setFlatMap(buildFlatMap(nodes));
        }
      }
    } finally {
      setLoading(false);
    }
  }, [resource, t]);

  useEffect(() => {
    loadTree();
  }, [loadTree]);

  // Expand all nodes whenever the tree data changes (after initial load or
  // after a CRUD operation reloads the tree).  This gives a fully-visible
  // tree by default, which is appropriate for a small location/category tree
  // and also makes the container-as-item badges immediately visible.
  useEffect(() => {
    if (treeData.length > 0) {
      tree.expandAllNodes();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeData]);

  // ── Load instances for the selected location (Fix 3) ──────────────────────
  const loadLocationInstances = useCallback(async (locationId: number) => {
    setInstancesLoading(true);
    try {
      const { data, error } = await client.GET("/api/instances", {
        params: { query: { location_id: locationId } },
      });
      if (error || !data) {
        setLocationInstances([]);
        return;
      }
      setLocationInstances(data);

      // Load definition names for any definition_id we haven't cached yet.
      // Read from the ref (always current) to avoid a stale-closure on the
      // state value while keeping this callback stable (empty dep array).
      const missingDefIds = [
        ...new Set(data.map((i) => i.definition_id)),
      ].filter((id) => !definitionNamesRef.current.has(id));

      if (missingDefIds.length > 0) {
        // Fetch definitions in parallel.
        const results = await Promise.all(
          missingDefIds.map((id) =>
            client.GET("/api/definitions/{definition_id}", {
              params: { path: { definition_id: id } },
            }),
          ),
        );
        setDefinitionNames((prev) => {
          const next = new Map(prev);
          results.forEach((r, idx) => {
            if (r.data) next.set(missingDefIds[idx], r.data.name);
          });
          return next;
        });
      }
    } finally {
      setInstancesLoading(false);
    }
  }, []);

  // Reload instances when the selected location changes.
  useEffect(() => {
    if (resource === "locations" && selectedId !== null) {
      void loadLocationInstances(selectedId);
    } else {
      setLocationInstances([]);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, resource]);

  // ── Load all instances (for the container-asset picker) ───────────────────
  /**
   * Fetches all instances (no location filter) and resolves definition names
   * for all of them.  Called when the link-container-asset modal opens.
   */
  const loadAllInstances = useCallback(async () => {
    setAllInstancesLoading(true);
    try {
      const { data, error } = await client.GET("/api/instances");
      if (error || !data) {
        setAllInstances([]);
        return;
      }
      setAllInstances(data);

      // Resolve any definition names we haven't cached yet.
      const missingDefIds = [
        ...new Set(data.map((i) => i.definition_id)),
      ].filter((id) => !definitionNamesRef.current.has(id));

      if (missingDefIds.length > 0) {
        const results = await Promise.all(
          missingDefIds.map((id) =>
            client.GET("/api/definitions/{definition_id}", {
              params: { path: { definition_id: id } },
            }),
          ),
        );
        setDefinitionNames((prev) => {
          const next = new Map(prev);
          results.forEach((r, idx) => {
            if (r.data) next.set(missingDefIds[idx], r.data.name);
          });
          return next;
        });
      }
    } finally {
      setAllInstancesLoading(false);
    }
  }, []);

  // ── Modal helpers ──────────────────────────────────────────────────────────
  function openCreate(parentId: number | null) {
    setFormName("");
    setActionError(null);
    setModal({ kind: "create", parentId });
  }

  function openRename(nodeId: number, currentName: string) {
    setFormName(currentName);
    setActionError(null);
    setModal({ kind: "rename", nodeId, currentName });
  }

  function openReparent(nodeId: number, currentParentId: number | null) {
    setFormParentId(currentParentId !== null ? String(currentParentId) : "");
    setActionError(null);
    setModal({ kind: "reparent", nodeId, currentParentId });
  }

  function openDelete(nodeId: number, nodeName: string) {
    setActionError(null);
    setModal({ kind: "delete", nodeId, nodeName });
  }

  function openMoveInstance(instance: InstanceResponse) {
    setMoveTargetId(instance.location_id !== null ? String(instance.location_id) : "");
    setActionError(null);
    setModal({ kind: "moveInstance", instance });
  }

  function openDeleteInstance(instance: InstanceResponse) {
    setActionError(null);
    setModal({ kind: "deleteInstance", instance });
  }

  function openLinkContainerAsset(locationId: number) {
    setLinkInstanceId("");
    setActionError(null);
    setModal({ kind: "linkContainerAsset", locationId });
    // Kick off the instance list load (non-blocking; spinner shows inside modal).
    void loadAllInstances();
  }

  function openUnlinkContainerAsset(locationId: number) {
    setActionError(null);
    setModal({ kind: "unlinkContainerAsset", locationId });
  }

  function closeModal() {
    setModal({ kind: "none" });
    setActionError(null);
  }

  // ── CRUD actions ───────────────────────────────────────────────────────────

  async function handleCreate(parentId: number | null) {
    if (!formName.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      if (resource === "locations") {
        const { error } = await client.POST("/api/locations", {
          body: { name: formName.trim(), parent_id: parentId },
        });
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      } else {
        const { error } = await client.POST("/api/categories", {
          body: { name: formName.trim(), parent_id: parentId },
        });
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      }
      closeModal();
      await loadTree();
    } finally {
      setBusy(false);
    }
  }

  async function handleRename(nodeId: number) {
    if (!formName.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      if (resource === "locations") {
        const { error } = await client.PATCH("/api/locations/{location_id}", {
          params: { path: { location_id: nodeId } },
          body: { name: formName.trim() },
        });
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      } else {
        const { error } = await client.PATCH(
          "/api/categories/{category_id}",
          {
            params: { path: { category_id: nodeId } },
            body: { name: formName.trim() },
          },
        );
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      }
      closeModal();
      await loadTree();
    } finally {
      setBusy(false);
    }
  }

  async function handleReparent(nodeId: number) {
    setBusy(true);
    setActionError(null);
    const newParentId = formParentId === "" ? null : Number(formParentId);
    // newParentId is null (root) or a valid numeric id from the picker.
    try {
      if (resource === "locations") {
        const { error } = await client.PATCH("/api/locations/{location_id}", {
          params: { path: { location_id: nodeId } },
          body: { parent_id: newParentId },
        });
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      } else {
        const { error } = await client.PATCH(
          "/api/categories/{category_id}",
          {
            params: { path: { category_id: nodeId } },
            body: { parent_id: newParentId },
          },
        );
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      }
      closeModal();
      await loadTree();
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(nodeId: number) {
    setBusy(true);
    setActionError(null);
    try {
      if (resource === "locations") {
        const { error } = await client.DELETE(
          "/api/locations/{location_id}",
          {
            params: { path: { location_id: nodeId } },
          },
        );
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      } else {
        const { error } = await client.DELETE(
          "/api/categories/{category_id}",
          {
            params: { path: { category_id: nodeId } },
          },
        );
        if (error) {
          setActionError(mapApiError(error));
          return;
        }
      }
      setSelectedId(null);
      closeModal();
      await loadTree();
    } finally {
      setBusy(false);
    }
  }

  // ── Instance move / delete actions (Fix 3) ────────────────────────────────

  async function handleMoveInstance(instance: InstanceResponse) {
    setBusy(true);
    setActionError(null);
    const newLocationId = moveTargetId === "" ? null : Number(moveTargetId);
    try {
      const { error } = await client.PATCH("/api/instances/{instance_id}", {
        params: { path: { instance_id: instance.id } },
        body: { location_id: newLocationId },
      });
      if (error) {
        setActionError(mapApiError(error));
        return;
      }
      closeModal();
      // Reload instances for the current location (instance left this location).
      if (selectedId !== null) await loadLocationInstances(selectedId);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteInstance(instance: InstanceResponse) {
    setBusy(true);
    setActionError(null);
    try {
      const { error } = await client.DELETE("/api/instances/{instance_id}", {
        params: { path: { instance_id: instance.id } },
      });
      if (error) {
        setActionError(mapApiError(error));
        return;
      }
      closeModal();
      if (selectedId !== null) await loadLocationInstances(selectedId);
    } finally {
      setBusy(false);
    }
  }

  // ── Container-asset link / unlink handlers ─────────────────────────────────

  async function handleLinkContainerAsset(locationId: number) {
    if (!linkInstanceId) return;
    setBusy(true);
    setActionError(null);
    try {
      const { error } = await client.PATCH(
        "/api/locations/{location_id}",
        {
          params: { path: { location_id: locationId } },
          body: { item_instance_id: Number(linkInstanceId) },
        },
      );
      if (error) {
        setActionError(mapApiError(error));
        return;
      }
      closeModal();
      await loadTree();
    } finally {
      setBusy(false);
    }
  }

  async function handleUnlinkContainerAsset(locationId: number) {
    setBusy(true);
    setActionError(null);
    try {
      const { error } = await client.PATCH("/api/locations/{location_id}", {
        params: { path: { location_id: locationId } },
        body: { item_instance_id: null },
      });
      if (error) {
        setActionError(mapApiError(error));
        return;
      }
      closeModal();
      await loadTree();
    } finally {
      setBusy(false);
    }
  }

  // ── Reparent helpers ───────────────────────────────────────────────────────

  /**
   * Collect the ID of the given node and all its descendants (recursive).
   * Used to filter out cycle-unsafe choices from the reparent picker.
   */
  function collectDescendantIds(nodeId: number): Set<number> {
    const ids = new Set<number>();
    function walk(id: number) {
      ids.add(id);
      for (const [nid, n] of flatMap) {
        if (n.parent_id === id) walk(nid);
      }
    }
    walk(nodeId);
    return ids;
  }

  /**
   * Build the Select option list for the reparent modal.
   * - First entry: root sentinel ("" → parent_id = null).
   * - Remaining entries: every node in flatMap EXCEPT the moving node and its
   *   descendants (cycle-safe), sorted by name.
   *
   * Memoised on flatMap + modal so it only recomputes when the picker opens.
   */
  const reparentOptions = useMemo(() => {
    const rootOption = { value: "", label: t("modals.reparent.rootOption") };
    if (modal.kind !== "reparent") return [rootOption];

    const excluded = collectDescendantIds(modal.nodeId);
    const locationResource = resource === "locations";
    const nodes = [...flatMap.values()]
      .filter((n) => !excluded.has(n.id))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => {
        const assetLabel =
          locationResource &&
          "container_asset_label" in n &&
          n.container_asset_label
            ? ` — ${n.container_asset_label}`
            : "";
        return { value: String(n.id), label: `${n.name}${assetLabel}` };
      });

    return [rootOption, ...nodes];
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flatMap, modal, t]);

  /**
   * Build the Select option list for the move-instance modal.
   * All locations are valid targets (including the current one), sorted by name.
   * A "None" option lets the user clear the location.
   * Only relevant when resource === "locations".
   */
  const moveLocationOptions = useMemo(() => {
    const noneOption = { value: "", label: t("modals.moveInstance.noneOption") };
    const locationResource = resource === "locations";
    const nodes = [...flatMap.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => {
        const assetLabel =
          locationResource &&
          "container_asset_label" in n &&
          n.container_asset_label
            ? ` — ${n.container_asset_label}`
            : "";
        return { value: String(n.id), label: `${n.name}${assetLabel}` };
      });
    return [noneOption, ...nodes];
  }, [flatMap, resource, t]);

  /**
   * Build a human-readable label for an instance:
   *   "<Definition Name> — SN: <serial>" or "<Definition Name> — qty: <quantity>"
   */
  function instanceLabel(inst: InstanceResponse): string {
    const defName = definitionNames.get(inst.definition_id) ?? `Def #${inst.definition_id}`;
    const detail = inst.serial ? `SN: ${inst.serial}` : `qty: ${formatQuantity(inst.quantity)}`;
    return `${defName} — ${detail}`;
  }

  /**
   * Select options for the link-container-asset modal.
   * Recomputes when allInstances or definitionNames change (i.e. after the
   * instances + definition names load completes).
   */
  const linkInstanceOptions = useMemo(
    () =>
      allInstances
        .slice()
        .sort((a, b) => instanceLabel(a).localeCompare(instanceLabel(b)))
        .map((inst) => ({ value: String(inst.id), label: instanceLabel(inst) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [allInstances, definitionNames],
  );

  // ── Selected node info ─────────────────────────────────────────────────────
  const selectedNode = selectedId !== null ? flatMap.get(selectedId) : null;
  const isLocation = resource === "locations";
  const selectedIsContainerAsItem =
    isLocation &&
    selectedNode !== null &&
    selectedNode !== undefined &&
    "item_instance_id" in selectedNode &&
    selectedNode.item_instance_id !== null;

  // The linked instance object (if any) for the detail panel display.
  // Search locationInstances first (always populated when a location is selected),
  // then fall back to allInstances (populated after the Link modal opens).
  const linkedInstance = selectedIsContainerAsItem
    ? (() => {
        const targetId = (selectedNode as LocationTreeNode).item_instance_id;
        return (
          locationInstances.find((i) => i.id === targetId) ??
          allInstances.find((i) => i.id === targetId)
        );
      })()
    : undefined;

  // ── Mantine Tree node renderer ─────────────────────────────────────────────
  // Memoize so the array reference is stable between renders — prevents the
  // Tree component's useEffect([data]) from re-calling initialize on every
  // render and causing an "update depth exceeded" loop.
  const mantineTreeData = useMemo(() => toMantineTree(treeData), [treeData]);

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;

  // Derived labels used in the toolbar button
  const addBtnLabel = selectedId !== null
    ? t("tree.addChild")
    : t("tree.addRoot");

  return (
    <Stack gap="md">
      {/* Top toolbar */}
      <Group justify="flex-end">
        <Button
          size="xs"
          leftSection={<Plus size={14} />}
          onClick={() => openCreate(selectedId)}
          data-testid="create-root-btn"
        >
          {addBtnLabel}
        </Button>
      </Group>

      {/* Tree — clicking blank space (not a node row or its buttons) clears selection */}
      {treeData.length === 0 ? (
        <EmptyState message={t("tree.empty")} />
      ) : (
        <div
          data-testid="tree-region"
          onClick={() => setSelectedId(null)}
          style={{ cursor: "default", minHeight: "200px", width: "100%" }}
        >
        <Tree
          data={mantineTreeData}
          tree={tree}
          selectOnClick
          expandOnClick
          renderNode={({ node, expanded, hasChildren, elementProps }) => {
            const nodeId = Number(node.value);
            const nodeData = flatMap.get(nodeId);
            const isContainerAsItem =
              isLocation &&
              nodeData &&
              "item_instance_id" in nodeData &&
              nodeData.item_instance_id !== null;
            const isSelected = selectedId === nodeId;

            return (
              <Group
                {...elementProps}
                gap={4}
                wrap="nowrap"
                style={{
                  cursor: "pointer",
                  borderRadius: 4,
                  padding: "2px 4px",
                  background: isSelected
                    ? "var(--mantine-color-teal-light)"
                    : undefined,
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  elementProps.onClick?.(e);
                  setSelectedId(isSelected ? null : nodeId);
                }}
              >
                {/* Expand caret */}
                <Text size="xs" c="dimmed" w={12} ta="center">
                  {hasChildren ? (expanded ? "▾" : "▸") : "·"}
                </Text>
                {/* Node label */}
                <Text size="sm" style={{ flex: 1 }}>
                  {node.label as string}
                </Text>
                {/* Container-as-item badge for locations */}
                {isContainerAsItem && (
                  <Badge
                    size="xs"
                    color="teal"
                    variant="light"
                    data-testid={`container-badge-${nodeId}`}
                  >
                    {(nodeData as LocationTreeNode).container_asset_label
                      ?? `Asset #${(nodeData as LocationTreeNode).item_instance_id}`}
                  </Badge>
                )}
                {/* Action icons (show on hover or selection) */}
                <Group gap={2} wrap="nowrap">
                  <ActionIcon
                    size="xs"
                    variant="subtle"
                    aria-label={t("tree.addChildUnder", { name: node.label as string })}
                    onClick={(e) => {
                      e.stopPropagation();
                      openCreate(nodeId);
                    }}
                  >
                    <Plus size={12} />
                  </ActionIcon>
                  <ActionIcon
                    size="xs"
                    variant="subtle"
                    aria-label={t("tree.rename", { name: node.label as string })}
                    onClick={(e) => {
                      e.stopPropagation();
                      openRename(nodeId, node.label as string);
                    }}
                  >
                    <Edit2 size={12} />
                  </ActionIcon>
                  <ActionIcon
                    size="xs"
                    variant="subtle"
                    color="red"
                    aria-label={t("tree.delete", { name: node.label as string })}
                    onClick={(e) => {
                      e.stopPropagation();
                      openDelete(nodeId, node.label as string);
                    }}
                  >
                    <Trash2 size={12} />
                  </ActionIcon>
                </Group>
              </Group>
            );
          }}
        />
        </div>
      )}

      {/* Selected node detail panel */}
      {selectedNode && (
        <Stack gap="xs" p="sm" style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 8 }}>
          <Group justify="space-between">
            <Text fw={600} size="sm">
              {selectedNode.name}
            </Text>
            <Group gap={4}>
              <Button
                size="xs"
                variant="light"
                onClick={() =>
                  openReparent(selectedNode.id, selectedNode.parent_id)
                }
              >
                {t("detail.reparentBtn")}
              </Button>
              <Button
                size="xs"
                variant="light"
                color="red"
                onClick={() => openDelete(selectedNode.id, selectedNode.name)}
              >
                {t("detail.deleteBtn")}
              </Button>
            </Group>
          </Group>
          {selectedNode.description && (
            <Text size="xs" c="dimmed">
              {selectedNode.description}
            </Text>
          )}
          {/* Container-asset link/unlink controls (locations only) */}
          {isLocation && (
            selectedIsContainerAsItem ? (
              <Group gap="xs" align="center" data-testid="container-asset-linked">
                <Badge color="teal" variant="light" size="sm">
                  {t("tree.containerAsset")} —{" "}
                  {linkedInstance
                    ? instanceLabel(linkedInstance)
                    : `Instance #${(selectedNode as LocationTreeNode).item_instance_id}`}
                </Badge>
                <Button
                  size="xs"
                  variant="subtle"
                  color="red"
                  onClick={() => openUnlinkContainerAsset(selectedNode.id)}
                  data-testid="unlink-container-btn"
                >
                  {t("tree.unlinkContainerBtn")}
                </Button>
              </Group>
            ) : (
              <Button
                size="xs"
                variant="light"
                color="teal"
                onClick={() => openLinkContainerAsset(selectedNode.id)}
                data-testid="link-container-btn"
              >
                {t("tree.linkContainerBtn")}
              </Button>
            )
          )}
          {/* Instances at this location (Fix 3 — locations only) */}
          {isLocation && (
            <>
              <Divider my="xs" />
              <Text size="xs" fw={500} c="dimmed" data-testid="instances-section-label">
                {t("tree.instancesSection")}
              </Text>
              {instancesLoading ? (
                <Text size="xs" c="dimmed">{t("tree.instancesLoading")}</Text>
              ) : locationInstances.length === 0 ? (
                <Text size="xs" c="dimmed" data-testid="instances-empty">
                  {t("tree.instancesEmpty")}
                </Text>
              ) : (
                <Table
                  withTableBorder={false}
                  withColumnBorders={false}
                  highlightOnHover
                  data-testid="instances-table"
                >
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>
                        <Text size="xs">{t("detail.instancesTableDefinition")}</Text>
                      </Table.Th>
                      <Table.Th>
                        <Text size="xs">{t("detail.instancesTableSerial")}</Text>
                      </Table.Th>
                      <Table.Th>
                        <Text size="xs">{t("detail.instancesTableQty")}</Text>
                      </Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {locationInstances.map((inst) => (
                      <Table.Tr key={inst.id} data-testid={`instance-row-${inst.id}`}>
                        <Table.Td>
                          <Text size="xs">
                            {definitionNames.get(inst.definition_id) ??
                              `#${inst.definition_id}`}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" c="dimmed">
                            {inst.serial ?? "—"}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs">{formatQuantity(inst.quantity)}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Group gap={2} wrap="nowrap" justify="flex-end">
                            <ActionIcon
                              size="xs"
                              variant="subtle"
                              aria-label={t("tree.moveInstance", { id: inst.id })}
                              onClick={() => openMoveInstance(inst)}
                              data-testid={`move-instance-${inst.id}`}
                            >
                              <Move size={12} />
                            </ActionIcon>
                            <ActionIcon
                              size="xs"
                              variant="subtle"
                              color="red"
                              aria-label={t("tree.deleteInstance", { id: inst.id })}
                              onClick={() => openDeleteInstance(inst)}
                              data-testid={`delete-instance-${inst.id}`}
                            >
                              <Trash2 size={12} />
                            </ActionIcon>
                          </Group>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </>
          )}
        </Stack>
      )}

      {/* ── Modals ─────────────────────────────────────────────────────────── */}

      {/* Create modal */}
      <Modal
        opened={modal.kind === "create"}
        onClose={closeModal}
        title={
          modal.kind === "create" && modal.parentId !== null
            ? t("modals.create.titleChild")
            : t("modals.create.titleRoot")
        }
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <TextInput
            label={t("modals.create.nameLabel")}
            value={formName}
            onChange={(e) => setFormName(e.currentTarget.value)}
            data-autofocus
            data-testid="name-input"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() =>
                handleCreate(modal.kind === "create" ? modal.parentId : null)
              }
              loading={busy}
              disabled={!formName.trim()}
            >
              {t("common:actions.create", "Create")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Rename modal */}
      <Modal
        opened={modal.kind === "rename"}
        onClose={closeModal}
        title={t("modals.rename.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <TextInput
            label={t("modals.rename.newNameLabel")}
            value={formName}
            onChange={(e) => setFormName(e.currentTarget.value)}
            data-autofocus
            data-testid="rename-input"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() =>
                modal.kind === "rename" && handleRename(modal.nodeId)
              }
              loading={busy}
              disabled={!formName.trim()}
            >
              {t("common:actions.save", "Save")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Reparent modal */}
      <Modal
        opened={modal.kind === "reparent"}
        onClose={closeModal}
        title={t("modals.reparent.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <Select
            label={t("modals.reparent.parentLabel")}
            data={reparentOptions}
            value={formParentId}
            onChange={(v) => setFormParentId(v ?? "")}
            allowDeselect={false}
            searchable
            data-testid="reparent-select"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() =>
                modal.kind === "reparent" && handleReparent(modal.nodeId)
              }
              loading={busy}
            >
              {t("common:actions.move", "Move")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Delete confirmation modal */}
      <Modal
        opened={modal.kind === "delete"}
        onClose={closeModal}
        title={t("modals.delete.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
              data-testid="delete-error"
            >
              {actionError}
            </Alert>
          )}
          {!actionError && (
            <Text size="sm">
              <Trans
                i18nKey="modals.delete.confirmation"
                ns={ns}
                values={{ name: modal.kind === "delete" ? modal.nodeName : "" }}
                components={{ bold: <b /> }}
              />
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!actionError && (
              <Button
                color="red"
                onClick={() =>
                  modal.kind === "delete" && handleDelete(modal.nodeId)
                }
                loading={busy}
                data-testid="confirm-delete-btn"
              >
                {t("common:actions.delete", "Delete")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {/* Move-instance modal (Fix 3) */}
      <Modal
        opened={modal.kind === "moveInstance"}
        onClose={closeModal}
        title={t("modals.moveInstance.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <Select
            label={t("modals.moveInstance.locationLabel")}
            data={moveLocationOptions}
            value={moveTargetId}
            onChange={(v) => setMoveTargetId(v ?? "")}
            allowDeselect={false}
            searchable
            data-testid="move-location-select"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() =>
                modal.kind === "moveInstance" && handleMoveInstance(modal.instance)
              }
              loading={busy}
              data-testid="confirm-move-btn"
            >
              {t("common:actions.move", "Move")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Delete-instance confirmation modal (Fix 3) */}
      <Modal
        opened={modal.kind === "deleteInstance"}
        onClose={closeModal}
        title={t("modals.deleteInstance.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <Text size="sm">
            {t("modals.deleteInstance.confirmation")}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              color="red"
              onClick={() =>
                modal.kind === "deleteInstance" &&
                handleDeleteInstance(modal.instance)
              }
              loading={busy}
              data-testid="confirm-delete-instance-btn"
            >
              {t("common:actions.delete", "Delete")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Link container asset modal */}
      <Modal
        opened={modal.kind === "linkContainerAsset"}
        onClose={closeModal}
        title={t("modals.linkContainerAsset.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert
              icon={<AlertCircle size={16} />}
              color="red"
              variant="light"
              data-testid="link-container-error"
            >
              {actionError}
            </Alert>
          )}
          {allInstancesLoading ? (
            <Text size="sm" c="dimmed">{t("modals.linkContainerAsset.loadingInstances")}</Text>
          ) : (
            <Select
              label={t("modals.linkContainerAsset.instanceLabel")}
              data={linkInstanceOptions}
              value={linkInstanceId}
              onChange={(v) => setLinkInstanceId(v ?? "")}
              allowDeselect={false}
              searchable
              placeholder={t("modals.linkContainerAsset.instancePlaceholder")}
              data-testid="link-instance-select"
            />
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            <Button
              onClick={() =>
                modal.kind === "linkContainerAsset" &&
                handleLinkContainerAsset(modal.locationId)
              }
              loading={busy}
              disabled={!linkInstanceId}
              data-testid="confirm-link-btn"
            >
              {t("common:actions.link", "Link")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Unlink container asset confirmation modal */}
      <Modal
        opened={modal.kind === "unlinkContainerAsset"}
        onClose={closeModal}
        title={t("modals.unlinkContainerAsset.title")}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          {!actionError && (
            <Text size="sm">
              {t("modals.unlinkContainerAsset.confirmation")}
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              {t("common:actions.cancel", "Cancel")}
            </Button>
            {!actionError && (
              <Button
                color="red"
                onClick={() =>
                  modal.kind === "unlinkContainerAsset" &&
                  handleUnlinkContainerAsset(modal.locationId)
                }
                loading={busy}
                data-testid="confirm-unlink-btn"
              >
                {t("common:actions.unlink", "Unlink")}
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
