"use client";

import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from "cytoscape";

export type GraphCanvasNode = {
  id: string;
  label: string;
  type: string;
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
};

type GraphCanvasProps = {
  nodes: GraphCanvasNode[];
  edges: GraphCanvasEdge[];
  selectedNodeId: string | null;
  relationFilter: string;
  onSelectNode: (nodeId: string | null) => void;
};

const RELATION_COLORS: Record<string, string> = {
  shares_entity: "#475569",
  improves: "#16a34a",
  contradicts: "#dc2626",
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

const COSE_LAYOUT: LayoutOptions = {
  name: "cose",
  fit: true,
  padding: 36,
  animate: false,
  randomize: false,
  nodeRepulsion: 9000,
  idealEdgeLength: 90,
  edgeElasticity: 120,
  nestingFactor: 1.1,
  gravity: 0.9,
  numIter: 600,
};

function filterEdges(edges: GraphCanvasEdge[], relationFilter: string): GraphCanvasEdge[] {
  if (relationFilter === "balanced" || relationFilter === "all") return edges;
  if (relationFilter === "semantic") {
    return edges.filter((edge) => edge.relation_type !== "mentions");
  }
  return edges.filter((edge) => edge.relation_type === relationFilter);
}

function buildVisibleGraph(
  nodes: GraphCanvasNode[],
  edges: GraphCanvasEdge[],
  relationFilter: string
): { nodes: GraphCanvasNode[]; edges: GraphCanvasEdge[] } {
  const cappedNodes = nodes.slice(0, MAX_RENDER_NODES);
  const nodeIds = new Set(cappedNodes.map((node) => node.id));
  const filteredEdges = filterEdges(edges, relationFilter).filter(
    (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)
  );

  if (filteredEdges.length === 0) {
    return { nodes: [], edges: [] };
  }

  const connectedIds = new Set<string>();
  for (const edge of filteredEdges) {
    connectedIds.add(edge.source);
    connectedIds.add(edge.target);
  }

  const visibleNodes = cappedNodes.filter((node) => connectedIds.has(node.id));
  return { nodes: visibleNodes, edges: filteredEdges };
}

export function GraphCanvas({
  nodes,
  edges,
  selectedNodeId,
  relationFilter,
  onSelectNode,
}: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelectNode);
  onSelectRef.current = onSelectNode;

  const { nodes: visibleNodes, edges: visibleEdges } = buildVisibleGraph(nodes, edges, relationFilter);
  const [layoutReady, setLayoutReady] = useState(false);
  const isEmpty = visibleNodes.length === 0;

  useEffect(() => {
    if (!containerRef.current || isEmpty) {
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
      setLayoutReady(false);
      return;
    }

    const truncateLabel = (raw: string, limit = 14): string => {
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
          layer: node.layer ?? "L1",
          mentionCount: node.mention_count,
        },
      })),
      ...visibleEdges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          relationType: edge.relation_type,
          confidence: edge.confidence,
        },
      })),
    ];

    const nodeSize = (mentionCount: number) => 18 + Math.min(14, Math.sqrt(mentionCount) * 4);

    if (!cyRef.current) {
      cyRef.current = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "background-color": "#4f46e5",
              color: "#334155",
              "font-size": 10,
              "text-wrap": "ellipsis",
              "text-max-width": "88",
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
              "background-color": "#1d4ed8",
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
        ],
        minZoom: 0.25,
        maxZoom: 2.5,
        wheelSensitivity: 0.15,
        motionBlur: false,
      });

      cyRef.current.on("tap", "node", (event) => {
        onSelectRef.current(event.target.id() as string);
      });
      cyRef.current.on("tap", (event) => {
        if (event.target === cyRef.current) {
          onSelectRef.current(null);
        }
      });
    } else {
      const cy = cyRef.current;
      cy.batch(() => {
        cy.elements().remove();
        cy.add(elements);
      });
    }

    const cy = cyRef.current;
    cy.nodes().forEach((node) => {
      const mentionCount = Number(node.data("mentionCount") || 1);
      const layer = String(node.data("layer") || "L1");
      const size = layer === "L0" ? 34 : nodeSize(mentionCount);
      node.style({ width: size, height: size });
    });
    cy.edges().forEach((edge) => {
      const relationType = String(edge.data("relationType") || "mentions");
      const confidence = Number(edge.data("confidence") || 0.6);
      const color = RELATION_COLORS[relationType] ?? RELATION_COLORS.mentions;
      edge.style({
        "line-color": color,
        "target-arrow-color": color,
        width: relationType === "mentions" ? 1 + confidence * 0.8 : 1.5 + confidence,
        opacity: relationType === "mentions" ? 0.45 : 0.75,
      });
    });

    setLayoutReady(false);
    const layout = cy.layout(COSE_LAYOUT);
    layout.one("layoutstop", () => {
      cy.fit(undefined, 36);
      setLayoutReady(true);
    });
    layout.run();
  }, [visibleNodes, visibleEdges, isEmpty]);

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

  if (isEmpty) {
    const hint =
      relationFilter === "semantic"
        ? "当前文档几乎没有语义关系边。请切换「均衡」查看高置信 co-mention，或重新导入文档。"
        : "没有满足质量阈值的连通关系。请切换关系类型，或删除旧文档后重新导入。";
    return (
      <div
        className="flex h-full min-h-[280px] w-full items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50/80 p-6 text-center text-sm text-slate-500"
      >
        <div>
          <p className="font-medium text-slate-600">暂无可视化子图</p>
          <p className="mt-2 max-w-sm text-xs leading-relaxed">{hint}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full min-h-[280px] w-full">
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
