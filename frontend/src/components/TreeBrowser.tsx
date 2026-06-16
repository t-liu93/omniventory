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
 *
 * Data access: exclusively via the typed openapi-fetch `client` (no hand-written fetch).
 */
import { useState, useCallback, useMemo, useEffect } from "react";
import {
  Stack,
  Group,
  Text,
  Badge,
  Button,
  ActionIcon,
  TextInput,
  NumberInput,
  Modal,
  Alert,
  Tree,
  useTree,
} from "@mantine/core";
import type { TreeNodeData } from "@mantine/core";
import { Plus, Edit2, Trash2, AlertCircle } from "react-feather";
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
  | { kind: "delete"; nodeId: number; nodeName: string };

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
  const [formParentId, setFormParentId] = useState<number | "">("");

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
    setFormParentId(currentParentId ?? "");
    setActionError(null);
    setModal({ kind: "reparent", nodeId, currentParentId });
  }

  function openDelete(nodeId: number, nodeName: string) {
    setActionError(null);
    setModal({ kind: "delete", nodeId, nodeName });
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

      {/* Tree */}
      {treeData.length === 0 ? (
        <EmptyState message={`No ${plural.toLowerCase()} yet. Create one above.`} />
      ) : (
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
          <NumberInput
            label="New parent ID (leave empty for root)"
            value={formParentId}
            onChange={(v) => setFormParentId(v === "" ? "" : Number(v))}
            min={1}
            allowDecimal={false}
            data-testid="reparent-input"
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
    </Stack>
  );
}
