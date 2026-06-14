"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

type ConfirmPayload = {
  title: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
};

type ConfirmApi = (payload: ConfirmPayload) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmApi | null>(null);

export function ConfirmDialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<
    | (ConfirmPayload & {
        resolve: (value: boolean) => void;
      })
    | null
  >(null);

  const confirm = useCallback<ConfirmApi>((payload) => {
    return new Promise<boolean>((resolve) => {
      setPending({ ...payload, resolve });
    });
  }, []);

  const close = useCallback(
    (result: boolean) => {
      if (!pending) return;
      pending.resolve(result);
      setPending(null);
    },
    [pending]
  );

  const api = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmContext.Provider value={api}>
      {children}
      {pending && (
        <div
          className="fixed inset-0 z-[55] flex items-center justify-center bg-slate-900/40 px-4"
          onClick={() => close(false)}
        >
          <div
            className="w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-slate-200 px-5 py-3">
              <div className="text-base font-semibold text-slate-800">{pending.title}</div>
            </div>
            <div className="px-5 py-4 text-sm leading-relaxed text-slate-600">
              {pending.message}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
              <button
                type="button"
                onClick={() => close(false)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-100"
              >
                {pending.cancelText ?? "取消"}
              </button>
              <button
                type="button"
                onClick={() => close(true)}
                className={
                  pending.danger
                    ? "rounded-lg border border-rose-300 bg-rose-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-rose-700"
                    : "rounded-lg border border-slate-900 bg-slate-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-slate-700"
                }
              >
                {pending.confirmText ?? "确认"}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmApi {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    return async () => {
      if (typeof window !== "undefined") return window.confirm("确认操作？");
      return false;
    };
  }
  return ctx;
}
