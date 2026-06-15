"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

type ToastKind = "info" | "success" | "warning" | "error";

type ToastItem = {
  id: string;
  kind: ToastKind;
  message: string;
  detail?: string;
  onClick?: () => void;
};

type ToastApi = {
  show: (input: {
    kind?: ToastKind;
    message: string;
    detail?: string;
    ttlMs?: number;
    onClick?: () => void;
  }) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const toneByKind: Record<ToastKind, string> = {
  info: "border-slate-200 bg-white text-slate-700",
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  error: "border-rose-200 bg-rose-50 text-rose-800",
};

const iconByKind: Record<ToastKind, string> = {
  info: "i",
  success: "✓",
  warning: "!",
  error: "✕",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      show: ({ kind = "info", message, detail, ttlMs = 4000, onClick }) => {
        const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        setItems((prev) => [...prev, { id, kind, message, detail, onClick }]);
        if (ttlMs > 0) {
          window.setTimeout(() => dismiss(id), ttlMs);
        }
      },
    }),
    [dismiss]
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-3 z-[60] flex flex-col items-center gap-2 px-4">
        {items.map((it) => (
          <div
            key={it.id}
            className={`pointer-events-auto flex w-full max-w-md items-start gap-3 rounded-xl border px-4 py-2.5 shadow-lg ${toneByKind[it.kind]} ${it.onClick ? "cursor-pointer" : ""}`}
            onClick={() => {
              it.onClick?.();
              dismiss(it.id);
            }}
          >
            <span className="mt-0.5 inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full border border-current text-xs font-bold">
              {iconByKind[it.kind]}
            </span>
            <div className="min-w-0 flex-1 text-sm leading-relaxed">
              <div className="break-words font-medium">{it.message}</div>
              {it.detail && (
                <div className="mt-0.5 break-words text-xs opacity-80">{it.detail}</div>
              )}
            </div>
            <button
              type="button"
              onClick={() => dismiss(it.id)}
              className="rounded-md px-1 text-xs opacity-60 hover:opacity-100"
              aria-label="关闭通知"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // 退化：未挂载 Provider 时把消息打到 console，避免组件直接 throw。
    return {
      show: ({ message, detail }) => {
        if (typeof window !== "undefined") {
          // eslint-disable-next-line no-console
          console.warn("[toast]", message, detail);
        }
      },
    };
  }
  return ctx;
}

/** 兼容性辅助：等待 Toast 自动消失。 */
export function useDismissAfter(ms: number, dep: unknown) {
  useEffect(() => {
    if (!dep) return;
    const t = window.setTimeout(() => undefined, ms);
    return () => window.clearTimeout(t);
  }, [ms, dep]);
}
