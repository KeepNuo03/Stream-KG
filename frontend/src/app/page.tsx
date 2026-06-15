"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Group as PanelGroup,
  Panel,
  Separator as PanelSeparator,
  useDefaultLayout,
  type LayoutStorage,
} from "react-resizable-panels";
import {
  ArrowUp,
  Link as LinkIcon,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
  Upload,
} from "lucide-react";

import { GraphCanvas } from "@/components/GraphCanvas";
import { MarkdownMessage } from "@/components/MarkdownMessage";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";

type DocumentItem = {
  doc_id: string;
  title: string;
  doc_type: "pdf" | "web";
  status: "pending" | "processing" | "ready" | "failed";
  page_count?: number | null;
  chunk_count: number;
  error_message?: string | null;
};

type Citation = {
  citation_id: string;
  doc_id: string;
  chunk_id: string;
  doc_title: string;
  snippet: string;
  page_num: number | null;
  section_title: string | null;
};
type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
};
type ChunkDetail = {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  doc_type: string;
  content: string;
  page_num: number | null;
  section_title: string | null;
  char_start: number | null;
  char_end: number | null;
  chunk_type: string | null;
};
type RetrievalMeta = {
  mode: string;
  chunk_count: number;
  raw_chunk_count: number;
  top_score: number | null;
  lexical_overlap: number | null;
  retrieval_reason: string | null;
  retrieval_source: string | null;
  locked_doc_title: string | null;
  reranker_mode: string | null;
};
type BatchDeleteFailedItem = { doc_id: string; code: string; message: string };
type BatchDeleteResponse = { requested: number; deleted: number; failed: BatchDeleteFailedItem[] };
type GraphNode = { id: string; label: string; type: string; doc_count: number; mention_count: number };
type GraphEdge = {
  id: string;
  source: string;
  target: string;
  source_label?: string;
  target_label?: string;
  relation_type: string;
  confidence: number;
};
type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: { node_count: number; edge_count: number };
  placeholder: boolean;
};
type EntityDetail = {
  entity_id: string;
  label: string;
  entity_type: string;
  aliases: string[];
  doc_ids: string[];
  mention_count: number;
  relations: Array<{
    edge_id: string;
    direction: "in" | "out";
    relation_type: string;
    target_entity_id?: string;
    source_entity_id?: string;
    confidence: number;
  }>;
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const relationTypeLabel: Record<string, string> = {
  mentions: "共现",
  improves: "改进",
  extends: "扩展",
  contradicts: "对立",
  surveys: "综述",
  related: "相关",
};

function localizeRelation(type: string): string {
  return relationTypeLabel[type] ?? type;
}

function ResizeHandle() {
  return (
    <PanelSeparator className="group relative mx-1 w-1.5 cursor-col-resize bg-transparent transition-colors hover:bg-blue-100/40 active:bg-blue-200/60">
      <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-slate-300 transition-all group-hover:w-0.5 group-hover:bg-blue-400 group-active:w-1 group-active:bg-blue-500" />
    </PanelSeparator>
  );
}

function DocActionsMenu({
  doc,
  onReprocess,
  onDelete,
  disabled,
  canReprocess,
}: {
  doc: DocumentItem;
  onReprocess: (doc: DocumentItem) => void;
  onDelete: (doc: DocumentItem) => void;
  disabled: boolean;
  canReprocess: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label="操作"
        className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-8 z-30 min-w-[120px] overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg ring-1 ring-black/5"
        >
          {canReprocess && (
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-700 transition hover:bg-slate-100"
              onClick={() => {
                setOpen(false);
                onReprocess(doc);
              }}
            >
              <RotateCw className="h-3.5 w-3.5 text-blue-600" />
              重处理
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-rose-600 transition hover:bg-rose-50"
            onClick={() => {
              setOpen(false);
              onDelete(doc);
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除
          </button>
        </div>
      )}
    </div>
  );
}

function ServiceStatusChip({
  name,
  status,
}: {
  name: string;
  status: { mode: string; device: string } | null;
}) {
  let cls = "rounded-full px-3 py-1 font-medium ";
  let label = `${name} ?`;
  if (!status) {
    cls += "bg-slate-200 text-slate-500";
    label = `${name} 离线`;
  } else if (status.mode === "real") {
    cls += "bg-emerald-100 text-emerald-700";
    label = `${name} · real`;
  } else if (status.mode === "off") {
    cls += "bg-slate-100 text-slate-500";
    label = `${name} · off`;
  } else if (status.mode === "pseudo" || status.mode === "heuristic") {
    cls += "bg-amber-100 text-amber-700";
    label = `${name} · ${status.mode}`;
  } else {
    cls += "bg-slate-100 text-slate-600";
    label = `${name} · ${status.mode}`;
  }
  const title = status ? `mode=${status.mode} device=${status.device}` : "无法连接服务";
  return (
    <span className={cls} title={title}>
      {label}
    </span>
  );
}

const NOOP_LAYOUT_STORAGE: LayoutStorage = {
  getItem: () => null,
  setItem: () => {},
};

export default function HomePage() {
  const [layoutHydrated, setLayoutHydrated] = useState(false);
  useEffect(() => {
    setLayoutHydrated(true);
  }, []);
  const layoutStorage = useMemo<LayoutStorage>(
    () =>
      layoutHydrated && typeof window !== "undefined"
        ? window.localStorage
        : NOOP_LAYOUT_STORAGE,
    [layoutHydrated],
  );
  const { defaultLayout: panelDefaultLayout, onLayoutChanged: onPanelLayoutChanged } = useDefaultLayout({
    id: "stream-kg-main-layout-v2",
    panelIds: ["panel-docs", "panel-graph", "panel-chat"],
    storage: layoutStorage,
  });

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [chatSessionId, setChatSessionId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastRetrieval, setLastRetrieval] = useState<RetrievalMeta | null>(null);
  const [sending, setSending] = useState(false);
  const [selectedDocIds, setSelectedDocIds] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [batchDeleteFailed, setBatchDeleteFailed] = useState<BatchDeleteFailedItem[]>([]);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [graphDocFilter, setGraphDocFilter] = useState<string>("all");
  const [relationFilter, setRelationFilter] = useState<string>("balanced");
  const [selectedEntityId, setSelectedEntityId] = useState<string | null>(null);
  const [entityDetail, setEntityDetail] = useState<EntityDetail | null>(null);
  const [loadingEntityDetail, setLoadingEntityDetail] = useState(false);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const [citationDetail, setCitationDetail] = useState<ChunkDetail | null>(null);
  const [loadingCitationDetail, setLoadingCitationDetail] = useState(false);
  const [citationDetailError, setCitationDetailError] = useState<string | null>(null);
  const [embeddingStatus, setEmbeddingStatus] = useState<{ mode: string; device: string } | null>(null);
  const [rerankerStatus, setRerankerStatus] = useState<{ mode: string; device: string } | null>(null);

  const toast = useToast();
  const confirmDialog = useConfirm();

  const pendingCount = useMemo(
    () => documents.filter((item) => item.status === "pending" || item.status === "processing").length,
    [documents]
  );
  const readyCount = useMemo(() => documents.filter((item) => item.status === "ready").length, [documents]);
  const selectableDocIds = useMemo(
    () => documents.filter((item) => item.status !== "processing").map((item) => item.doc_id),
    [documents]
  );
  const selectedCount = selectedDocIds.size;
  const allSelectableSelected = useMemo(
    () => selectableDocIds.length > 0 && selectableDocIds.every((docId) => selectedDocIds.has(docId)),
    [selectableDocIds, selectedDocIds]
  );
  const firstBatchDeleteFailed = batchDeleteFailed[0] ?? null;

  // 文档状态元数据：用于展示更友好的中文标签和颜色样式。
  const statusMeta: Record<DocumentItem["status"], { label: string; tone: string }> = {
    pending: { label: "待处理", tone: "bg-amber-100 text-amber-700 ring-1 ring-inset ring-amber-200" },
    processing: { label: "处理中", tone: "bg-blue-100 text-blue-700 ring-1 ring-inset ring-blue-200" },
    ready: { label: "已就绪", tone: "bg-emerald-100 text-emerald-700 ring-1 ring-inset ring-emerald-200" },
    failed: { label: "失败", tone: "bg-rose-100 text-rose-700 ring-1 ring-inset ring-rose-200" },
  };

  async function refreshDocuments() {
    // 主动刷新文档列表（也被轮询逻辑复用）。
    setLoadingDocs(true);
    try {
      const res = await fetch(`${API_BASE}/documents`);
      const data = await res.json();
      setDocuments((data.documents ?? []) as DocumentItem[]);
    } finally {
      setLoadingDocs(false);
    }
  }

  const refreshGraph = useCallback(async (silent = false) => {
    if (!silent) {
      setLoadingGraph(true);
    }
    try {
      const params = new URLSearchParams({
        limit_nodes: "36",
        min_mentions: "2",
        max_edges: "48",
        relation_type: relationFilter,
      });
      if (graphDocFilter !== "all") {
        params.set("doc_id", graphDocFilter);
      }
      const res = await fetch(`${API_BASE}/graph?${params.toString()}`);
      if (!res.ok) {
        throw new Error(`图谱接口请求失败：${res.status}`);
      }
      const data = (await res.json()) as GraphData;
      setGraphData(data);
      setGraphError(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "图谱加载失败，请稍后重试。";
      setGraphError(message);
    } finally {
      if (!silent) {
        setLoadingGraph(false);
      }
    }
  }, [graphDocFilter, relationFilter]);

  const loadEntityDetail = useCallback(async (entityId: string | null) => {
    setSelectedEntityId(entityId);
    if (!entityId) {
      setEntityDetail(null);
      return;
    }
    setLoadingEntityDetail(true);
    try {
      const res = await fetch(`${API_BASE}/graph/entities/${entityId}`);
      if (!res.ok) {
        throw new Error(`实体详情请求失败：${res.status}`);
      }
      const data = (await res.json()) as EntityDetail;
      setEntityDetail(data);
    } catch {
      setEntityDetail(null);
    } finally {
      setLoadingEntityDetail(false);
    }
  }, []);

  const openCitation = useCallback(async (citation: Citation) => {
    setActiveCitation(citation);
    setCitationDetail(null);
    setCitationDetailError(null);
    setLoadingCitationDetail(true);
    try {
      const res = await fetch(
        `${API_BASE}/documents/${citation.doc_id}/chunks/${citation.chunk_id}`
      );
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const detail = (await res.json()) as ChunkDetail;
      setCitationDetail(detail);
    } catch (err) {
      setCitationDetailError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoadingCitationDetail(false);
    }
  }, []);

  const closeCitation = useCallback(() => {
    setActiveCitation(null);
    setCitationDetail(null);
    setCitationDetailError(null);
  }, []);

  async function ensureSession(): Promise<string> {
    // 首次发消息前创建会话，后续复用同一 session_id。
    if (chatSessionId) {
      return chatSessionId;
    }
    const res = await fetch(`${API_BASE}/chat/sessions`, { method: "POST" });
    const data = await res.json();
    setChatSessionId(data.session_id);
    return data.session_id;
  }

  async function onUploadFile(file: File) {
    // 上传 PDF：后端返回 202 后立即刷新列表，靠轮询拿状态变化。
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("title", file.name.replace(/\.pdf$/i, ""));
      await fetch(`${API_BASE}/documents/upload`, { method: "POST", body: formData });
      await refreshDocuments();
    } finally {
      setUploading(false);
    }
  }

  async function onImportUrl() {
    // 导入网页 URL，和 PDF 上传走同一异步入库流程。
    if (!urlInput.trim()) return;
    setUploading(true);
    try {
      await fetch(`${API_BASE}/documents/import-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: urlInput.trim() }),
      });
      setUrlInput("");
      await refreshDocuments();
    } finally {
      setUploading(false);
    }
  }

  function toggleDocSelected(docId: string, checked: boolean) {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(docId);
      } else {
        next.delete(docId);
      }
      return next;
    });
  }

  function toggleSelectAll(checked: boolean) {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (checked) {
        selectableDocIds.forEach((docId) => next.add(docId));
      } else {
        selectableDocIds.forEach((docId) => next.delete(docId));
      }
      return next;
    });
  }

  async function onReprocess(doc: DocumentItem) {
    if (doc.status === "processing" || deleting) return;
    try {
      const res = await fetch(`${API_BASE}/documents/${doc.doc_id}/reprocess`, {
        method: "POST",
      });
      if (res.status !== 202) {
        const detail = await res.text();
        throw new Error(detail || `HTTP ${res.status}`);
      }
      toast.show({
        kind: "info",
        message: `已重新加入处理队列：《${doc.title}》`,
        detail: "页面会自动轮询状态变化",
      });
      await refreshDocuments();
    } catch (error) {
      const message = error instanceof Error ? error.message : "重处理失败";
      toast.show({ kind: "error", message: "重处理失败", detail: message });
    }
  }

  async function onDeleteSingle(doc: DocumentItem) {
    if (doc.status === "processing" || deleting) return;
    const ok = await confirmDialog({
      title: "删除文档",
      message: (
        <span>
          确认删除《<b>{doc.title}</b>》？
          <br />
          会同时删除该文档对应的 chunk、向量与图谱节点，<span className="text-rose-600">不可恢复</span>。
        </span>
      ),
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;

    setDeleting(true);
    setBatchDeleteFailed([]);
    try {
      const res = await fetch(`${API_BASE}/documents/${doc.doc_id}`, { method: "DELETE" });
      if (res.status !== 204) {
        const detail = await res.text();
        throw new Error(detail || `删除失败（${res.status}）`);
      }
      setSelectedDocIds((prev) => {
        const next = new Set(prev);
        next.delete(doc.doc_id);
        return next;
      });
      await refreshDocuments();
      toast.show({ kind: "success", message: `已删除《${doc.title}》` });
    } catch (error) {
      const message = error instanceof Error ? error.message : "文档删除失败，请稍后重试。";
      toast.show({ kind: "error", message: "删除失败", detail: message });
    } finally {
      setDeleting(false);
    }
  }

  async function onBatchDelete() {
    if (selectedCount === 0 || deleting) return;
    const ok = await confirmDialog({
      title: "批量删除文档",
      message: (
        <span>
          确认批量删除已选 <b>{selectedCount}</b> 条文档？
          <br />
          会同时清理对应 chunk / 向量 / 图谱节点，<span className="text-rose-600">不可恢复</span>。
        </span>
      ),
      confirmText: `删除 ${selectedCount} 条`,
      danger: true,
    });
    if (!ok) return;

    setDeleting(true);
    setBatchDeleteFailed([]);
    try {
      const res = await fetch(`${API_BASE}/documents/batch-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_ids: [...selectedDocIds] }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `批量删除失败（${res.status}）`);
      }
      const data = (await res.json()) as BatchDeleteResponse;
      setBatchDeleteFailed(data.failed ?? []);
      setSelectedDocIds(new Set<string>());
      await refreshDocuments();
      const failedCount = (data.failed ?? []).length;
      if (failedCount > 0) {
        toast.show({
          kind: "warning",
          message: `批量删除：成功 ${data.deleted} 条，失败 ${failedCount} 条`,
          detail: `失败原因可在文档列表底部查看`,
        });
      } else {
        toast.show({
          kind: "success",
          message: `批量删除完成（${data.deleted} 条）`,
        });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "批量删除失败，请稍后重试。";
      toast.show({ kind: "error", message: "批量删除失败", detail: message });
    } finally {
      setDeleting(false);
    }
  }

  async function onSend() {
    // 发送对话消息：本地先插入 user + assistant 占位，再消费 SSE。
    const text = query.trim();
    if (!text || sending) return;
    setSending(true);
    setQuery("");
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);

    try {
      const sessionId = await ensureSession();
      const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text, retrieval_mode: null }),
      });
      if (!res.ok || !res.body) {
        throw new Error(`请求失败：${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let shouldStop = false;
      let receivedToken = false;
      const collectedCitations: Citation[] = [];

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        // SSE 在不同实现里可能使用 \n 或 \r\n；先统一行尾，避免分包失败。
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const rawEvent of events) {
          const lines = rawEvent.split("\n");
          const eventLine = lines.find((line) => line.startsWith("event: "));
          const dataLine = lines.find((line) => line.startsWith("data: "));
          if (!eventLine || !dataLine) continue;
          const eventType = eventLine.replace("event: ", "").trim();
          let payload: Record<string, unknown> = {};
          try {
            payload = JSON.parse(dataLine.replace("data: ", "").trim()) as Record<string, unknown>;
          } catch {
            // 忽略非 JSON 的 data 行，避免中断整个流式会话。
            payload = {};
          }

          // Phase 1 先消费 token 事件，citation/done 后续可继续增强 UI 展示。
          if (eventType === "retrieval") {
            setLastRetrieval({
              mode: typeof payload.mode === "string" ? payload.mode : "vector",
              chunk_count: typeof payload.chunk_count === "number" ? payload.chunk_count : 0,
              raw_chunk_count: typeof payload.raw_chunk_count === "number" ? payload.raw_chunk_count : 0,
              top_score: typeof payload.top_score === "number" ? payload.top_score : null,
              lexical_overlap:
                typeof payload.lexical_overlap === "number" ? payload.lexical_overlap : null,
              retrieval_reason:
                typeof payload.retrieval_reason === "string" ? payload.retrieval_reason : null,
              retrieval_source:
                typeof payload.retrieval_source === "string" ? payload.retrieval_source : null,
              locked_doc_title:
                typeof payload.locked_doc_title === "string" ? payload.locked_doc_title : null,
              reranker_mode:
                typeof payload.reranker_mode === "string" ? payload.reranker_mode : null,
            });
          } else if (eventType === "token") {
            receivedToken = true;
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                const tokenText = typeof payload.content === "string" ? payload.content : "";
                next[next.length - 1] = { ...last, content: last.content + tokenText };
              }
              return next;
            });
          } else if (eventType === "citation") {
            const citationId = typeof payload.citation_id === "string" ? payload.citation_id : "";
            const docId = typeof payload.doc_id === "string" ? payload.doc_id : "";
            const chunkId = typeof payload.chunk_id === "string" ? payload.chunk_id : "";
            if (citationId && docId && chunkId) {
              collectedCitations.push({
                citation_id: citationId,
                doc_id: docId,
                chunk_id: chunkId,
                doc_title:
                  typeof payload.doc_title === "string" ? payload.doc_title : "未命名文档",
                snippet: typeof payload.snippet === "string" ? payload.snippet : "",
                page_num: typeof payload.page_num === "number" ? payload.page_num : null,
                section_title:
                  typeof payload.section_title === "string" ? payload.section_title : null,
              });
            }
          } else if (eventType === "error") {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              const message = typeof payload.message === "string" ? payload.message : "对话服务返回错误。";
              if (last && last.role === "assistant" && !last.content.trim()) {
                next[next.length - 1] = { ...last, content: message };
              }
              return next;
            });
            shouldStop = true;
          } else if (eventType === "done") {
            if (collectedCitations.length > 0) {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last && last.role === "assistant") {
                  next[next.length - 1] = { ...last, citations: [...collectedCitations] };
                }
                return next;
              });
            }
            shouldStop = true;
          }
        }

        if (shouldStop) {
          // 后端 done 后主动结束读取，避免前端因 SSE 长连接而一直处于 sending 状态。
          await reader.cancel();
          break;
        }
      }

      // 兜底策略：若流式 token 未成功消费（例如网络层分包差异），
      // 则从会话历史回捞最后一条 assistant 消息，避免前端只显示“...”。
      if (!receivedToken) {
        try {
          const historyRes = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`);
          if (historyRes.ok) {
            const history = (await historyRes.json()) as {
              messages?: Array<{ role: "user" | "assistant"; content: string }>;
            };
            const lastAssistant = [...(history.messages ?? [])].reverse().find((msg) => msg.role === "assistant");
            if (lastAssistant?.content?.trim()) {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last && last.role === "assistant" && !last.content.trim()) {
                  next[next.length - 1] = { ...last, content: lastAssistant.content };
                }
                return next;
              });
            }
          }
        } catch {
          // 回捞失败不影响主流程，保持当前 UI 状态。
        }
      }
    } catch (error) {
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        const message = error instanceof Error ? error.message : "对话请求失败，请稍后重试。";
        if (last && last.role === "assistant" && !last.content.trim()) {
          next[next.length - 1] = { ...last, content: `请求失败：${message}` };
        }
        return next;
      });
    } finally {
      setSending(false);
    }
  }

  useEffect(() => {
    // 文档列表变化后，自动清理已不存在的选中项。
    setSelectedDocIds((prev) => {
      const existingIds = new Set(documents.map((item) => item.doc_id));
      const next = new Set([...prev].filter((docId) => existingIds.has(docId)));
      return next.size === prev.size ? prev : next;
    });
  }, [documents]);

  useEffect(() => {
    void refreshDocuments();
  }, []);

  useEffect(() => {
    void refreshGraph();
  }, [refreshGraph]);

  useEffect(() => {
    // 轮询服务健康状态，5s 一次；用于顶栏状态指示灯。
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/status`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setEmbeddingStatus(
          data?.embedding && typeof data.embedding === "object"
            ? { mode: String(data.embedding.mode ?? "?"), device: String(data.embedding.device ?? "?") }
            : null
        );
        setRerankerStatus(
          data?.reranker && typeof data.reranker === "object"
            ? { mode: String(data.reranker.mode ?? "?"), device: String(data.reranker.device ?? "?") }
            : null
        );
      } catch {
        setEmbeddingStatus(null);
        setRerankerStatus(null);
      }
    };
    void fetchStatus();
    const t = window.setInterval(fetchStatus, 5000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    // 仅在存在 pending/processing 文档时开启轮询，避免无效请求。
    const timer = window.setInterval(() => {
      if (pendingCount > 0) {
        void refreshDocuments();
        void refreshGraph(true);
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [pendingCount]);

  return (
    <main className="h-screen overflow-hidden bg-gradient-to-br from-slate-50 via-white to-blue-50 text-slate-800">
      <div className="flex h-full flex-col gap-2 px-2 py-2">
        <header className="rounded-xl border border-slate-200/80 bg-white/80 px-4 py-2 shadow-sm backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-baseline gap-2">
              <h1 className="text-sm font-semibold tracking-tight">stream-kg 控制台</h1>
              <span className="text-xs text-slate-400">增量知识库 · 文档处理与问答工作台</span>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span className="rounded-full bg-slate-100 px-3 py-1 font-medium text-slate-600">文档 {documents.length}</span>
              <span className="rounded-full bg-emerald-100 px-3 py-1 font-medium text-emerald-700">就绪 {readyCount}</span>
              <span className="rounded-full bg-amber-100 px-3 py-1 font-medium text-amber-700">处理中 {pendingCount}</span>
              <ServiceStatusChip name="embed" status={embeddingStatus} />
              <ServiceStatusChip name="rerank" status={rerankerStatus} />
            </div>
          </div>
        </header>

        <PanelGroup
          orientation="horizontal"
          id="stream-kg-main-layout-v2"
          defaultLayout={panelDefaultLayout}
          onLayoutChanged={onPanelLayoutChanged}
          className="flex min-h-0 flex-1"
        >
          <Panel defaultSize="24%" minSize="18%" id="panel-docs" className="flex min-h-0">
          <section className="flex h-full min-h-0 w-full flex-col rounded-2xl border border-slate-200/80 bg-white/85 p-4 shadow-sm">
            <div className="space-y-2">
              <h2 className="text-sm font-semibold text-slate-800">文档导入</h2>
              <label
                className={`flex w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed px-3 py-2.5 text-xs font-medium transition ${
                  uploading
                    ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400"
                    : "border-slate-300 bg-slate-50/70 text-slate-600 hover:border-blue-400 hover:bg-blue-50/50 hover:text-blue-700"
                }`}
              >
                {uploading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Upload className="h-3.5 w-3.5" />
                )}
                <span>{uploading ? "上传处理中..." : "上传 PDF"}</span>
                <input
                  type="file"
                  accept=".pdf"
                  className="hidden"
                  disabled={uploading}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void onUploadFile(file);
                  }}
                />
              </label>
              <div className="relative flex items-center gap-1.5">
                <div className="relative flex-1">
                  <LinkIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                  <input
                    className="w-full rounded-xl border border-slate-200 bg-white py-2 pl-8 pr-3 text-xs outline-none transition placeholder:text-slate-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                    placeholder="粘贴网页链接 URL"
                    value={urlInput}
                    onChange={(e) => setUrlInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && urlInput.trim()) onImportUrl();
                    }}
                  />
                </div>
                <button
                  type="button"
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-slate-900 text-white shadow-sm transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                  onClick={onImportUrl}
                  disabled={uploading || !urlInput.trim()}
                  aria-label="导入链接"
                  title="导入链接"
                >
                  <Plus className="h-4 w-4" />
                </button>
              </div>
              {uploading && <p className="text-[11px] text-slate-500">正在提交任务并刷新文档状态...</p>}
            </div>

            <div className="mt-4 mb-2 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-800">文档列表</h3>
              <button
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-blue-600 transition hover:bg-blue-50 disabled:opacity-50"
                onClick={() => void refreshDocuments()}
                disabled={loadingDocs}
                aria-label="刷新文档列表"
                title="刷新文档列表"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loadingDocs ? "animate-spin" : ""}`} />
                {loadingDocs ? "刷新中" : "刷新"}
              </button>
            </div>

            <div className="mb-2 rounded-xl border border-slate-200 bg-slate-50/90 p-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <label className="inline-flex items-center gap-2 text-slate-600">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 rounded border-slate-300"
                    checked={allSelectableSelected}
                    disabled={selectableDocIds.length === 0 || deleting}
                    onChange={(e) => toggleSelectAll(e.target.checked)}
                  />
                  全选可删除项
                </label>
                <span className="text-slate-500">已选 {selectedCount}</span>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <button
                  className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 font-medium text-rose-700 transition hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => void onBatchDelete()}
                  disabled={selectedCount === 0 || deleting}
                >
                  {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  {deleting ? "删除中..." : "批量删除"}
                </button>
                <button
                  className="rounded-lg border border-slate-200 bg-white px-2 py-1 font-medium text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => setSelectedDocIds(new Set<string>())}
                  disabled={selectedCount === 0 || deleting}
                >
                  取消选择
                </button>
              </div>
              {batchDeleteFailed.length > 0 && (
                <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
                  删除失败 {batchDeleteFailed.length} 条：{firstBatchDeleteFailed?.doc_id ?? "unknown"}
                  {batchDeleteFailed.length > 1 ? ` 等 ${batchDeleteFailed.length} 条` : ""}
                </div>
              )}
            </div>

            <div className="min-h-0 flex-1 space-y-2 overflow-auto pr-1 text-sm">
              {documents.map((doc) => (
                <div key={doc.doc_id} className="rounded-xl border border-slate-200 bg-slate-50/80 p-3">
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-1 h-3.5 w-3.5 rounded border-slate-300"
                      checked={selectedDocIds.has(doc.doc_id)}
                      disabled={doc.status === "processing" || deleting}
                      onChange={(e) => toggleDocSelected(doc.doc_id, e.target.checked)}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="break-words font-medium text-slate-800">{doc.title}</div>
                      <div className="mt-2 flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-500">{doc.doc_type.toUpperCase()}</span>
                        <span className={`rounded-full px-2 py-1 ${statusMeta[doc.status].tone}`}>
                          {statusMeta[doc.status].label}
                        </span>
                      </div>
                      <div className="mt-2 text-xs text-slate-500">Chunks: {doc.chunk_count}</div>
                      {doc.status === "failed" && doc.error_message && (
                        <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50/80 px-2 py-1 text-[11px] leading-relaxed text-rose-700">
                          <span className="font-semibold">失败原因：</span>
                          <span className="break-words">{doc.error_message}</span>
                        </div>
                      )}
                    </div>
                    <DocActionsMenu
                      doc={doc}
                      onReprocess={(d) => void onReprocess(d)}
                      onDelete={(d) => void onDeleteSingle(d)}
                      disabled={doc.status === "processing" || deleting}
                      canReprocess={doc.status === "failed" || doc.status === "ready"}
                    />
                  </div>
                </div>
              ))}
              {documents.length === 0 && (
                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-center text-xs text-slate-500">
                  暂无文档，先上传一份 PDF 或导入网页链接
                </div>
              )}
            </div>
          </section>

          </Panel>
          <ResizeHandle />
          <Panel defaultSize="52%" minSize="30%" id="panel-graph" className="flex min-h-0">
          <section className="flex h-full min-h-0 w-full flex-col rounded-2xl border border-slate-200/80 bg-white/85 p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-800">知识图谱视图</h2>
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-indigo-100 px-2 py-1 text-xs font-medium text-indigo-700">Phase 2</span>
                <button
                  className="rounded-lg border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-100 disabled:opacity-50"
                  onClick={() => void refreshGraph()}
                  disabled={loadingGraph}
                >
                  {loadingGraph ? "刷新中..." : "刷新图谱"}
                </button>
              </div>
            </div>
            {graphError ? (
              <div className="flex min-h-0 flex-1 items-center justify-center rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center text-sm text-rose-600">
                <div>
                  <p className="font-medium">图谱加载失败</p>
                  <p className="mt-1 text-xs">{graphError}</p>
                </div>
              </div>
            ) : graphData?.placeholder ? (
              <div className="flex min-h-0 flex-1 items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-gradient-to-b from-slate-50 to-slate-100/70 p-6 text-center text-sm text-slate-500">
                <div>
                  <p className="font-medium text-slate-600">图谱功能未启用</p>
                  <p className="mt-1 text-xs">
                    请在后端开启 `FEATURE_KG_ENABLED=true` 并重启服务后，再导入文档构建图谱
                  </p>
                </div>
              </div>
            ) : !graphData || graphData.stats.node_count === 0 ? (
              <div className="flex min-h-0 flex-1 items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-gradient-to-b from-slate-50 to-slate-100/70 p-6 text-center text-sm text-slate-500">
                <div>
                  <p className="font-medium text-slate-600">暂无图谱节点</p>
                  <p className="mt-1 text-xs">请先导入并处理文档，系统会在 Phase 2 自动增量入图</p>
                </div>
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <label className="inline-flex items-center gap-1 text-slate-600">
                    文档
                    <select
                      className="rounded-lg border border-slate-300 bg-white px-2 py-1"
                      value={graphDocFilter}
                      onChange={(e) => setGraphDocFilter(e.target.value)}
                    >
                      <option value="all">全部</option>
                      {documents
                        .filter((doc) => doc.status === "ready")
                        .map((doc) => (
                          <option key={doc.doc_id} value={doc.doc_id}>
                            {doc.title}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="inline-flex items-center gap-1 text-slate-600">
                    关系
                    <select
                      className="rounded-lg border border-slate-300 bg-white px-2 py-1"
                      value={relationFilter}
                      onChange={(e) => setRelationFilter(e.target.value)}
                    >
                      <option value="balanced">均衡（推荐）</option>
                      <option value="semantic">仅语义关系</option>
                      <option value="all">全部关系</option>
                      <option value="mentions">mentions</option>
                      <option value="improves">improves</option>
                      <option value="contradicts">contradicts</option>
                      <option value="extends">extends</option>
                      <option value="surveys">surveys</option>
                    </select>
                  </label>
                  <span className="text-slate-500">
                    节点 {graphData.stats.node_count} · 边 {graphData.stats.edge_count}
                    {relationFilter === "semantic" ? " · 仅语义边" : ""}
                    {relationFilter === "balanced" ? " · 连通子图 · 术语优先" : ""}
                  </span>
                </div>
                <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_240px]">
                  <GraphCanvas
                    nodes={graphData.nodes}
                    edges={graphData.edges}
                    selectedNodeId={selectedEntityId}
                    relationFilter={relationFilter}
                    onSelectNode={(nodeId) => void loadEntityDetail(nodeId)}
                  />
                  <div className="min-h-0 rounded-xl border border-slate-200 bg-slate-50/70 p-3 text-xs">
                    <div className="mb-2 font-medium text-slate-700">实体详情</div>
                    {!selectedEntityId ? (
                      <p className="text-slate-500">点击节点查看 mentions 与关系</p>
                    ) : loadingEntityDetail ? (
                      <p className="text-slate-500">加载中...</p>
                    ) : entityDetail ? (
                      <div className="space-y-2">
                        <div>
                          <div className="text-sm font-semibold text-slate-800">{entityDetail.label}</div>
                          <div className="mt-1 text-slate-500">
                            {entityDetail.entity_type} · mentions {entityDetail.mention_count}
                          </div>
                        </div>
                        {entityDetail.aliases.length > 0 && (
                          <div>
                            <div className="font-medium text-slate-600">别名</div>
                            <p className="mt-1 break-words text-slate-500">{entityDetail.aliases.join("、")}</p>
                          </div>
                        )}
                        <div>
                          <div className="font-medium text-slate-600">关系 ({entityDetail.relations.length})</div>
                          <div className="mt-1 max-h-[28vh] space-y-1 overflow-auto">
                            {entityDetail.relations.map((rel) => (
                              <div key={rel.edge_id} className="rounded border border-slate-200 bg-white px-2 py-1">
                                <div className="font-medium text-slate-700">
                                  {rel.direction === "out" ? "→" : "←"} {localizeRelation(rel.relation_type)}
                                </div>
                                <div className="text-[11px] text-slate-500">
                                  conf {rel.confidence.toFixed(2)}
                                </div>
                              </div>
                            ))}
                            {entityDetail.relations.length === 0 && (
                              <p className="text-slate-500">暂无关系边</p>
                            )}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <p className="text-slate-500">无法加载实体详情</p>
                    )}
                  </div>
                </div>
              </div>
            )}
          </section>

          </Panel>
          <ResizeHandle />
          <Panel defaultSize="24%" minSize="18%" id="panel-chat" className="flex min-h-0">
          <section className="flex h-full min-h-0 w-full flex-col rounded-2xl border border-slate-200/80 bg-white/85 p-4 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold text-slate-800">对话助手</h2>
          {lastRetrieval && (
            <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
              <span
                className={
                  lastRetrieval.retrieval_source?.startsWith("meta:")
                    ? "rounded-full bg-purple-100 px-2 py-0.5 font-medium text-purple-700"
                    : lastRetrieval.retrieval_source?.startsWith("browse:")
                    ? "rounded-full bg-blue-100 px-2 py-0.5 font-medium text-blue-700"
                    : "rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-700"
                }
              >
                {lastRetrieval.retrieval_source?.startsWith("meta:")
                  ? "元数据直答"
                  : lastRetrieval.retrieval_source?.startsWith("browse:")
                  ? `锁定文档 · ${lastRetrieval.locked_doc_title ?? "目标文档"}`
                  : `向量检索 · ${lastRetrieval.mode}`}
              </span>
              <span>
                chunks {lastRetrieval.chunk_count}/{lastRetrieval.raw_chunk_count}
              </span>
              {typeof lastRetrieval.top_score === "number" && (
                <span>top {lastRetrieval.top_score.toFixed(3)}</span>
              )}
              {typeof lastRetrieval.lexical_overlap === "number" && (
                <span>overlap {Math.round(lastRetrieval.lexical_overlap * 100)}%</span>
              )}
              {lastRetrieval.retrieval_reason && lastRetrieval.retrieval_reason !== "ok" && (
                <span className="text-amber-600">reason {lastRetrieval.retrieval_reason}</span>
              )}
              {lastRetrieval.reranker_mode && lastRetrieval.reranker_mode !== "off" && (
                <span
                  className={
                    lastRetrieval.reranker_mode === "real"
                      ? "text-emerald-600"
                      : lastRetrieval.reranker_mode.startsWith("client_skipped") ||
                        lastRetrieval.reranker_mode === "cooldown"
                      ? "text-amber-600"
                      : "text-slate-500"
                  }
                >
                  rerank {lastRetrieval.reranker_mode}
                </span>
              )}
            </div>
          )}
            <div className="min-h-0 flex-1 space-y-5 overflow-auto px-1 py-2">
              {messages.map((msg, idx) => (
                <div
                  key={`${msg.role}-${idx}`}
                  className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-stretch"}`}
                >
                  {msg.role === "user" ? (
                    <div className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-blue-600 px-3.5 py-2 text-sm leading-relaxed text-white shadow-sm">
                      {msg.content || "…"}
                    </div>
                  ) : msg.content ? (
                    <MarkdownMessage content={msg.content} />
                  ) : (
                    <div className="flex items-center gap-1.5 text-xs text-slate-400">
                      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400" />
                      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400 [animation-delay:120ms]" />
                      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400 [animation-delay:240ms]" />
                    </div>
                  )}
                  {msg.role === "assistant" && msg.citations && msg.citations.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {msg.citations.map((cit) => (
                        <button
                          key={cit.citation_id}
                          type="button"
                          onClick={() => void openCitation(cit)}
                          className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700 transition hover:border-blue-400 hover:bg-blue-100"
                          title={cit.snippet}
                        >
                          <span className="font-semibold">[{cit.citation_id}]</span>
                          <span className="max-w-[200px] truncate">{cit.doc_title}</span>
                          {typeof cit.page_num === "number" && (
                            <span className="text-blue-500">p.{cit.page_num}</span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {messages.length === 0 && (
                <div className="rounded-xl border border-dashed border-slate-300 bg-white/60 p-6 text-center text-xs text-slate-500">
                  输入问题后开始对话，系统会基于已导入文档返回答案
                </div>
              )}
            </div>
            <div className="mt-3 rounded-2xl border border-slate-200 bg-white shadow-sm transition focus-within:border-blue-300 focus-within:ring-2 focus-within:ring-blue-100">
              <textarea
                className="block w-full resize-none rounded-2xl bg-transparent px-4 pt-3 pb-1 text-sm leading-relaxed outline-none placeholder:text-slate-400"
                rows={2}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="尽管问，关于已导入资料的任何问题..."
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (!sending && query.trim()) void onSend();
                  }
                }}
              />
              <div className="flex items-center justify-between px-2 pb-2">
                <button
                  type="button"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-slate-200 text-slate-400 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled
                  title="更多功能（暂未开放）"
                  aria-label="更多"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-400">Enter 发送 · Shift+Enter 换行</span>
                  <button
                    type="button"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-white shadow-sm transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-slate-300"
                    disabled={sending || !query.trim()}
                    onClick={() => void onSend()}
                    aria-label={sending ? "发送中" : "发送"}
                    title={sending ? "发送中..." : "发送"}
                  >
                    {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            </div>
          </section>
          </Panel>
        </PanelGroup>
      </div>
      {activeCitation && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4"
          onClick={closeCitation}
        >
          <div
            className="max-h-[80vh] w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span className="rounded-full bg-blue-100 px-2 py-0.5 font-semibold text-blue-700">
                    [{activeCitation.citation_id}]
                  </span>
                  {citationDetail?.doc_type === "pdf" ? "PDF" : citationDetail?.doc_type === "web" ? "网页" : "文档"}
                  {typeof activeCitation.page_num === "number" && (
                    <span>第 {activeCitation.page_num} 页</span>
                  )}
                  {activeCitation.section_title && (
                    <span className="max-w-[200px] truncate">· {activeCitation.section_title}</span>
                  )}
                </div>
                <div className="mt-1 truncate text-sm font-semibold text-slate-800">
                  {activeCitation.doc_title}
                </div>
              </div>
              <button
                type="button"
                onClick={closeCitation}
                className="rounded-lg px-2 py-1 text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
                aria-label="关闭"
              >
                ✕
              </button>
            </div>
            <div className="max-h-[60vh] overflow-auto px-5 py-4 text-sm leading-relaxed text-slate-700">
              {loadingCitationDetail && <p className="text-slate-500">加载 chunk 内容...</p>}
              {citationDetailError && (
                <div className="space-y-2">
                  <p className="text-red-600">加载失败：{citationDetailError}</p>
                  <p className="text-xs text-slate-500">下面是 SSE 截短版预览（200 字符内）</p>
                  <pre className="whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
                    {activeCitation.snippet || "(空)"}
                  </pre>
                </div>
              )}
              {citationDetail && (
                <pre className="whitespace-pre-wrap break-words text-sm text-slate-800">
                  {citationDetail.content || "(空)"}
                </pre>
              )}
            </div>
            <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-5 py-2 text-xs text-slate-500">
              <span>chunk_id {activeCitation.chunk_id.slice(0, 8)}…</span>
              {citationDetail?.doc_type === "pdf" && (
                <a
                  href={`${API_BASE}/documents/${activeCitation.doc_id}/file`}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-slate-700 hover:border-blue-400 hover:text-blue-700"
                >
                  打开原 PDF
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
