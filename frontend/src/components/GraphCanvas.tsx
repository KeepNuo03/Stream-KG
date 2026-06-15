"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from "cytoscape";
// @ts-expect-error cytoscape-fcose has no bundled types
import fcose from "cytoscape-fcose";

cytoscape.use(fcose);

export type GraphCanvasNode = {
  id: string;
  label: string;
  type: string;
  entity_type?: string;
  layer?: "L0" | "L1";
  parent_doc_id?: string | null;
  mention_count: number;
};

export type GraphCanvasEdge = {
  id: string;
  source: string;
  target: string;
  source_label?: string;
  target_label?: string;
  relation_type: string;
  confidence: number;
  evidence?: string;
};

type GraphCanvasProps = {
  nodes: GraphCanvasNode[];
  edges: GraphCanvasEdge[];
  selectedNodeId: string | null;
  relationFilters: string[];
  onSelectNode: (nodeId: string | null) => void;
  onSelectEdge?: (edge: GraphCanvasEdge | null) => void;
  onFocusNode?: (nodeId: string) => void;
};

export const ENTITY_TYPE_COLORS: Record<string, string> = {
  person: "#ca8a04",
  organization: "#dc2626",
  paper: "#4338ca",
  method: "#2563eb",
  concept: "#0891b2",
  dataset: "#16a34a",
  metric: "#9333ea",
  task: "#db2777",
  location: "#64748b",
  time: "#94a3b8",
  tool: "#ea580c",
  role: "#92400e",
  document: "#dbeafe",
};

export const RELATION_COLORS: Record<string, string> = {
  shares_entity: "#475569",
  improves: "#16a34a",
  contradicts: "#dc2626",
  conflict: "#dc2626",
  extends: "#2563eb",
  proposes: "#0ea5e9",
  uses: "#0d9488",
  evaluates_on: "#f59e0b",
  affiliated_with: "#f97316",
  authors: "#14b8a6",
  co_occurs: "#64748b",
  part_of: "#6366f1",
  surveys: "#7c3aed",
  mentions: "#94a3b8",
};

const MAX_RENDER_NODES = 36;
const LAYOUT_STORAGE_KEY = "stream-kg-layout-v1";

const FCOSE_LAYOUT = {
  name: "fcose",
  quality: "default",
  animate: false,
  fit: true,
  padding: 36,
  nodeSeparation: 80,
  packComponents: true,
  randomize: false,
} as LayoutOptions;

function loadLayoutCache(): Map<string, { x: number; y: number }> {
  if (typeof window === "undefined") return new Map();
  try {
    const raw = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as Record<string, { x: number; y: number }>;
    return new Map(Object.entries(parsed));
  } catch {
    return new Map();
  }
}

function saveLayoutCache(cache: Map<string, { x: number; y: number }>) {
  if (typeof window === "undefined") return;
  const obj = Object.fromEntries(cache.entries());
  window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(obj));
}

function filterEdges(edges: GraphCanvasEdge[], relationFilters: string[]): GraphCanvasEdge[] {
  if (relationFilters.length === 0) return edges;
  if (relationFilters.includes("all") || relationFilters.includes("balanced")) return edges;
  if (relationFilters.includes("semantic")) {
    return edges.filter((edge) => edge.relation_type !== "mentions");
  }
  const matched = edges.filter((edge) => relationFilters.includes(edge.relation_type));
  // 筛选结果为空时回退到全部边，避免整图消失
  return matched.length > 0 ? matched : edges;
}

function buildVisibleGraph(
  nodes: GraphCanvasNode[],
  edges: GraphCanvasEdge[],
  relationFilters: string[]
): { nodes: GraphCanvasNode[]; edges: GraphCanvasEdge[] } {
  const cappedNodes = nodes.slice(0, MAX_RENDER_NODES);
  const nodeIds = new Set(cappedNodes.map((node) => node.id));
  const filteredEdges = filterEdges(edges, relationFilters).filter(
    (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)
  );

  if (filteredEdges.length === 0) {
    return { nodes: cappedNodes.slice(0, 12), edges: [] };
  }

  const connectedIds = new Set<string>();
  for (const edge of filteredEdges) {
    connectedIds.add(edge.source);
    connectedIds.add(edge.target);
  }

  const visibleNodes = cappedNodes.filter((node) => connectedIds.has(node.id));
  return { nodes: visibleNodes, edges: filteredEdges };
}

function topologyKey(nodes: GraphCanvasNode[], edges: GraphCanvasEdge[]): string {
  const nodePart = nodes.map((n) => n.id).sort().join(",");
  const edgePart = edges.map((e) => e.id).sort().join(",");
  return `${nodePart}|${edgePart}`;
}

export function GraphCanvas({
  nodes,
  edges,
  selectedNodeId,
  relationFilters,
  onSelectNode,
  onSelectEdge,
  onFocusNode,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const layoutCacheRef = useRef<Map<string, { x: number; y: number }>>(loadLayoutCache());
  const lastTopologyRef = useRef<string>("");
  const onSelectRef = useRef(onSelectNode);
  const onSelectEdgeRef = useRef(onSelectEdge);
  const onFocusRef = useRef(onFocusNode);
  onSelectRef.current = onSelectNode;
  onSelectEdgeRef.current = onSelectEdge;
  onFocusRef.current = onFocusNode;

  const { nodes: visibleNodes, edges: visibleEdges } = useMemo(
    () => buildVisibleGraph(nodes, edges, relationFilters),
    [nodes, edges, relationFilters]
  );
  const topologyKeyValue = useMemo(
    () => topologyKey(visibleNodes, visibleEdges),
    [visibleNodes, visibleEdges]
  );

  const [layoutReady, setLayoutReady] = useState(false);
  const isEmpty = visibleNodes.length === 0;

  useEffect(() => {
    if (!containerRef.current) return;

    const truncateLabel = (raw: string, limit = 18): string => {
      const text = (raw || "").trim();
      if (!text) return "?";
      if (text.length <= limit) return text;
      return text.slice(0, limit - 1) + "…";
    };

    const elements: ElementDefinition[] = [
      ...visibleNodes.map((node) => ({
        data: {
          id: node.id,
          label: truncateLabel(node.label),
          fullLabel: node.label,
          type: node.type,
          entityType: node.entity_type || node.type,
          layer: node.layer ?? "L1",
          mentionCount: node.mention_count,
        },
      })),
      ...visibleEdges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          sourceLabel: edge.source_label || "",
          targetLabel: edge.target_label || "",
          relationType: edge.relation_type,
          confidence: edge.confidence,
          evidence: edge.evidence || "",
        },
      })),
    ];

    const nodeSize = (mentionCount: number) => 18 + Math.min(14, Math.sqrt(mentionCount) * 4);

    if (!cyRef.current) {
      cyRef.current = cytoscape({
        container: containerRef.current,
        elements: [],
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              color: "#334155",
              "font-size": 10,
              "text-wrap": "ellipsis",
              "text-max-width": "96",
              "text-valign": "bottom",
              "text-margin-y": 6,
              width: 22,
              height: 22,
              "border-width": 1,
              "border-color": "#c7d2fe",
            },
          },
          {
            selector: 'node[layer = "L0"]',
            style: {
              shape: "round-rectangle",
              "background-color": "#dbeafe",
              "border-color": "#60a5fa",
              "border-width": 1.5,
              color: "#1e3a8a",
            },
          },
          {
            selector: 'node[layer = "L1"]',
            style: {
              shape: "ellipse",
            },
          },
          {
            selector: "node:selected",
            style: {
              "border-color": "#1e3a8a",
              width: 28,
              height: 28,
              label: "data(fullLabel)",
              "font-size": 11,
              "text-max-width": "200",
            },
          },
          {
            selector: "edge",
            style: {
              width: 1.5,
              "curve-style": "bezier",
              "line-color": "#cbd5e1",
              opacity: 0.7,
              "target-arrow-shape": "triangle",
              "target-arrow-color": "#cbd5e1",
              "arrow-scale": 0.6,
            },
          },
          {
            selector: 'edge[relationType = "conflict"]',
            style: {
              width: 3,
              "line-color": "#dc2626",
              "target-arrow-color": "#dc2626",
              opacity: 1,
            },
          },
          {
            selector: 'edge[relationType = "mentions"]',
            style: {
              "line-style": "dashed",
            },
          },
        ],
        minZoom: 0.25,
        maxZoom: 2.5,
        wheelSensitivity: 0.15,
        motionBlur: false,
      });

      cyRef.current.on("tap", "node", (event) => {
        onSelectRef.current(event.target.id() as string);
      });
      cyRef.current.on("dbltap", "node", (event) => {
        const nodeId = event.target.id() as string;
        onFocusRef.current?.(nodeId);
      });
      cyRef.current.on("tap", "edge", (event) => {
        const edge = event.target;
        const payload: GraphCanvasEdge = {
          id: edge.id(),
          source: edge.data("source"),
          target: edge.data("target"),
          source_label: edge.data("sourceLabel") || undefined,
          target_label: edge.data("targetLabel") || undefined,
          relation_type: edge.data("relationType"),
          confidence: Number(edge.data("confidence") || 0),
          evidence: edge.data("evidence") || "",
        };
        onSelectEdgeRef.current?.(payload);
      });
      cyRef.current.on("tap", (event) => {
        if (event.target === cyRef.current) {
          onSelectRef.current(null);
          onSelectEdgeRef.current?.(null);
        }
      });
      cyRef.current.on("dragfree", "node", (event) => {
        const node = event.target;
        layoutCacheRef.current.set(node.id(), { x: node.position("x"), y: node.position("y") });
        saveLayoutCache(layoutCacheRef.current);
      });
    }

    const cy = cyRef.current;
    if (isEmpty) {
      cy.batch(() => cy.elements().remove());
      setLayoutReady(true);
      return;
    }

    const topologyChanged = topologyKeyValue !== lastTopologyRef.current;
    lastTopologyRef.current = topologyKeyValue;

    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });

    cy.nodes().forEach((node) => {
      const mentionCount = Number(node.data("mentionCount") || 1);
      const layer = String(node.data("layer") || "L1");
      const entityType = String(node.data("entityType") || "concept");
      const size = layer === "L0" ? 34 : nodeSize(mentionCount);
      const color = layer === "L0" ? ENTITY_TYPE_COLORS.document : ENTITY_TYPE_COLORS[entityType] || "#4f46e5";
      node.style({ width: size, height: size, "background-color": color });
      const cached = layoutCacheRef.current.get(node.id());
      if (cached) {
        node.position(cached);
      }
    });

    cy.edges().forEach((edge) => {
      const relationType = String(edge.data("relationType") || "mentions");
      const confidence = Number(edge.data("confidence") || 0.6);
      const color = RELATION_COLORS[relationType] ?? RELATION_COLORS.mentions;
      edge.style({
        "line-color": color,
        "target-arrow-color": color,
        width: relationType === "mentions" ? 1 + confidence * 0.8 : relationType === "conflict" ? 3 : 1.5 + confidence,
        opacity: relationType === "mentions" ? 0.45 : 0.85,
      });
    });

    if (!topologyChanged) {
      cy.fit(undefined, 36);
      setLayoutReady(true);
      return;
    }

    setLayoutReady(false);
    const layout = cy.layout({
      ...FCOSE_LAYOUT,
    } as LayoutOptions);
    layout.one("layoutstop", () => {
      cy.nodes().forEach((node) => {
        layoutCacheRef.current.set(node.id(), { x: node.position("x"), y: node.position("y") });
      });
      saveLayoutCache(layoutCacheRef.current);
      cy.fit(undefined, 36);
      setLayoutReady(true);
    });
    layout.run();
  }, [topologyKeyValue, visibleNodes, visibleEdges, isEmpty]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selectedNodeId && cy.getElementById(selectedNodeId).nonempty()) {
      cy.getElementById(selectedNodeId).select();
    }
  }, [selectedNodeId]);

  useEffect(() => {
    return () => {
      cyRef.current?.destroy();
      cyRef.current = null;
    };
  }, []);

  const hint =
    relationFilters.includes("semantic")
      ? "当前文档几乎没有语义关系边。请切换关系筛选，或重新导入文档。"
      : "没有满足质量阈值的连通关系。请切换关系类型，或选择文档查看 ego 子图。";

  return (
    <div className="relative h-full min-h-[280px] w-full">
      {isEmpty && (
        <div className="absolute inset-0 z-20 flex items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50/85 p-6 text-center text-sm text-slate-500">
          <div>
            <p className="font-medium text-slate-600">暂无可视化子图</p>
            <p className="mt-2 max-w-sm text-xs leading-relaxed">{hint}</p>
          </div>
        </div>
      )}
      {!layoutReady && (
        <div className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-white/70 text-xs text-slate-500">
          布局计算中…
        </div>
      )}
      <div
        ref={containerRef}
        className="h-full min-h-[280px] w-full rounded-xl border border-slate-200 bg-white"
      />
    </div>
  );
}
