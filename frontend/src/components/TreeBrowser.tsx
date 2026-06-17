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
import { client } from "../api/client";
import type { components } from "../api/schema";
import { LoadingState } from "./LoadingState";
import { ErrorState } from "./ErrorState";
import { EmptyState } from "./EmptyState";

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
  | { kind: "deleteInstance"; instance: InstanceResponse };

// ── Main component ───────────────────────────────────────────────────────────

interface TreeBrowserProps {
  resource: TreeResource;
  /** Singular display label (e.g. "Location"). */
  label: string;
  /** Plural display label (e.g. "Locations"). Defaults to label + "s". */
  labelPlural?: string;
}

export function TreeBrowser({ resource, label, labelPlural }: TreeBrowserProps) {
  const plural = labelPlural ?? `${label}s`;
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
          setLoadError("Failed to load location tree.");
        } else {
          const nodes = data as LocationTreeNode[];
          setTreeData(nodes);
          setFlatMap(buildFlatMap(nodes));
        }
      } else {
        const { data, error } = await client.GET("/api/categories/tree");
        if (error || !data) {
          setLoadError("Failed to load category tree.");
        } else {
          const nodes = data as CategoryTreeNode[];
          setTreeData(nodes);
          setFlatMap(buildFlatMap(nodes));
        }
      }
    } finally {
      setLoading(false);
    }
  }, [resource]);

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
          setActionError("Failed to create location.");
          return;
        }
      } else {
        const { error } = await client.POST("/api/categories", {
          body: { name: formName.trim(), parent_id: parentId },
        });
        if (error) {
          setActionError("Failed to create category.");
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
          setActionError("Failed to rename location.");
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
          setActionError("Failed to rename category.");
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
          const msg =
            typeof error === "object" && error !== null && "detail" in error
              ? String((error as { detail: unknown }).detail)
              : "Failed to reparent location.";
          setActionError(msg);
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
          const msg =
            typeof error === "object" && error !== null && "detail" in error
              ? String((error as { detail: unknown }).detail)
              : "Failed to reparent category.";
          setActionError(msg);
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
        const { error, response } = await client.DELETE(
          "/api/locations/{location_id}",
          {
            params: { path: { location_id: nodeId } },
          },
        );
        if (error) {
          if (response.status === 409) {
            const msg =
              typeof error === "object" && error !== null && "detail" in error
                ? String((error as { detail: unknown }).detail)
                : "Cannot delete: location is not empty.";
            setActionError(msg);
          } else {
            setActionError("Failed to delete location.");
          }
          return;
        }
      } else {
        const { error, response } = await client.DELETE(
          "/api/categories/{category_id}",
          {
            params: { path: { category_id: nodeId } },
          },
        );
        if (error) {
          if (response.status === 409) {
            const msg =
              typeof error === "object" && error !== null && "detail" in error
                ? String((error as { detail: unknown }).detail)
                : "Cannot delete: category is not empty.";
            setActionError(msg);
          } else {
            setActionError("Failed to delete category.");
          }
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
        const msg =
          typeof error === "object" && error !== null && "detail" in error
            ? String((error as { detail: unknown }).detail)
            : "Failed to move instance.";
        setActionError(msg);
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
        setActionError("Failed to delete instance.");
        return;
      }
      closeModal();
      if (selectedId !== null) await loadLocationInstances(selectedId);
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
    const rootOption = { value: "", label: "— (root / no parent)" };
    if (modal.kind !== "reparent") return [rootOption];

    const excluded = collectDescendantIds(modal.nodeId);
    const nodes = [...flatMap.values()]
      .filter((n) => !excluded.has(n.id))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => ({ value: String(n.id), label: n.name }));

    return [rootOption, ...nodes];
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flatMap, modal]);

  /**
   * Build the Select option list for the move-instance modal.
   * All locations are valid targets (including the current one), sorted by name.
   * A "None" option lets the user clear the location.
   * Only relevant when resource === "locations".
   */
  const moveLocationOptions = useMemo(() => {
    const noneOption = { value: "", label: "— None (no location) —" };
    const nodes = [...flatMap.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => ({ value: String(n.id), label: n.name }));
    return [noneOption, ...nodes];
  }, [flatMap]);

  // ── Selected node info ─────────────────────────────────────────────────────
  const selectedNode = selectedId !== null ? flatMap.get(selectedId) : null;
  const isLocation = resource === "locations";
  const selectedIsContainerAsItem =
    isLocation &&
    selectedNode !== null &&
    selectedNode !== undefined &&
    "item_instance_id" in selectedNode &&
    selectedNode.item_instance_id !== null;

  // ── Mantine Tree node renderer ─────────────────────────────────────────────
  // Memoize so the array reference is stable between renders — prevents the
  // Tree component's useEffect([data]) from re-calling initialize on every
  // render and causing an "update depth exceeded" loop.
  const mantineTreeData = useMemo(() => toMantineTree(treeData), [treeData]);

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) return <LoadingState />;
  if (loadError) return <ErrorState message={loadError} />;

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
          {selectedId !== null
            ? `Add child ${label.toLowerCase()}`
            : `Add ${label.toLowerCase()}`}
        </Button>
      </Group>

      {/* Tree — clicking blank space (not a node row or its buttons) clears selection */}
      {treeData.length === 0 ? (
        <EmptyState message={`No ${plural.toLowerCase()} yet. Create one above.`} />
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
                    Asset #{(nodeData as LocationTreeNode).item_instance_id}
                  </Badge>
                )}
                {/* Action icons (show on hover or selection) */}
                <Group gap={2} wrap="nowrap">
                  <ActionIcon
                    size="xs"
                    variant="subtle"
                    aria-label={`Add child ${label.toLowerCase()} under ${node.label as string}`}
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
                    aria-label={`Rename ${node.label as string}`}
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
                    aria-label={`Delete ${node.label as string}`}
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
                Reparent
              </Button>
              <Button
                size="xs"
                variant="light"
                color="red"
                onClick={() => openDelete(selectedNode.id, selectedNode.name)}
              >
                Delete
              </Button>
            </Group>
          </Group>
          {selectedNode.description && (
            <Text size="xs" c="dimmed">
              {selectedNode.description}
            </Text>
          )}
          {selectedIsContainerAsItem && (
            <Badge color="teal" variant="light" size="sm">
              Container asset — Instance #
              {(selectedNode as LocationTreeNode).item_instance_id}
            </Badge>
          )}
          {/* Instances at this location (Fix 3 — locations only) */}
          {isLocation && (
            <>
              <Divider my="xs" />
              <Text size="xs" fw={500} c="dimmed" data-testid="instances-section-label">
                Items at this location
              </Text>
              {instancesLoading ? (
                <Text size="xs" c="dimmed">Loading…</Text>
              ) : locationInstances.length === 0 ? (
                <Text size="xs" c="dimmed" data-testid="instances-empty">
                  No items here. This location can be deleted.
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
                        <Text size="xs">Definition</Text>
                      </Table.Th>
                      <Table.Th>
                        <Text size="xs">Serial</Text>
                      </Table.Th>
                      <Table.Th>
                        <Text size="xs">Qty</Text>
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
                          <Text size="xs">{inst.quantity}</Text>
                        </Table.Td>
                        <Table.Td>
                          <Group gap={2} wrap="nowrap" justify="flex-end">
                            <ActionIcon
                              size="xs"
                              variant="subtle"
                              aria-label={`Move instance ${inst.id}`}
                              onClick={() => openMoveInstance(inst)}
                              data-testid={`move-instance-${inst.id}`}
                            >
                              <Move size={12} />
                            </ActionIcon>
                            <ActionIcon
                              size="xs"
                              variant="subtle"
                              color="red"
                              aria-label={`Delete instance ${inst.id}`}
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
            ? `Add child ${label.toLowerCase()}`
            : `Add ${label.toLowerCase()}`
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
            label="Name"
            value={formName}
            onChange={(e) => setFormName(e.currentTarget.value)}
            data-autofocus
            data-testid="name-input"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                handleCreate(modal.kind === "create" ? modal.parentId : null)
              }
              loading={busy}
              disabled={!formName.trim()}
            >
              Create
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Rename modal */}
      <Modal
        opened={modal.kind === "rename"}
        onClose={closeModal}
        title={`Rename ${label.toLowerCase()}`}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <TextInput
            label="New name"
            value={formName}
            onChange={(e) => setFormName(e.currentTarget.value)}
            data-autofocus
            data-testid="rename-input"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                modal.kind === "rename" && handleRename(modal.nodeId)
              }
              loading={busy}
              disabled={!formName.trim()}
            >
              Save
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Reparent modal */}
      <Modal
        opened={modal.kind === "reparent"}
        onClose={closeModal}
        title={`Move ${label.toLowerCase()}`}
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <Select
            label={`New parent ${label.toLowerCase()} (choose "root" to move to top level)`}
            data={reparentOptions}
            value={formParentId}
            onChange={(v) => setFormParentId(v ?? "")}
            allowDeselect={false}
            searchable
            data-testid="reparent-select"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                modal.kind === "reparent" && handleReparent(modal.nodeId)
              }
              loading={busy}
            >
              Move
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Delete confirmation modal */}
      <Modal
        opened={modal.kind === "delete"}
        onClose={closeModal}
        title={`Delete ${label.toLowerCase()}`}
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
              Delete{" "}
              <b>
                {modal.kind === "delete" ? modal.nodeName : ""}
              </b>
              ? This is blocked if the {label.toLowerCase()} is not empty.
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              Cancel
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
                Delete
              </Button>
            )}
          </Group>
        </Stack>
      </Modal>

      {/* Move-instance modal (Fix 3) */}
      <Modal
        opened={modal.kind === "moveInstance"}
        onClose={closeModal}
        title="Move item to another location"
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <Select
            label="New location"
            data={moveLocationOptions}
            value={moveTargetId}
            onChange={(v) => setMoveTargetId(v ?? "")}
            allowDeselect={false}
            searchable
            data-testid="move-location-select"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                modal.kind === "moveInstance" && handleMoveInstance(modal.instance)
              }
              loading={busy}
              data-testid="confirm-move-btn"
            >
              Move
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Delete-instance confirmation modal (Fix 3) */}
      <Modal
        opened={modal.kind === "deleteInstance"}
        onClose={closeModal}
        title="Delete item"
        size="sm"
      >
        <Stack gap="sm">
          {actionError && (
            <Alert icon={<AlertCircle size={16} />} color="red" variant="light">
              {actionError}
            </Alert>
          )}
          <Text size="sm">
            Permanently delete this item instance? This cannot be undone.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeModal} disabled={busy}>
              Cancel
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
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
