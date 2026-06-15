"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

const Document = dynamic(() => import("react-pdf").then((mod) => mod.Document), { ssr: false });
const Page = dynamic(() => import("react-pdf").then((mod) => mod.Page), { ssr: false });

type PdfDrawerProps = {
  docId: string;
  docTitle: string;
  pageNum: number | null;
  open: boolean;
  onClose: () => void;
};

export function PdfDrawer({ docId, docTitle, pageNum, open, onClose }: PdfDrawerProps) {
  const [numPages, setNumPages] = useState(0);
  const [pdfReady, setPdfReady] = useState(false);

  useEffect(() => {
    if (!open) return;
    void import("react-pdf").then(({ pdfjs }) => {
      pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;
      setPdfReady(true);
    });
  }, [open]);

  if (!open) return null;

  const targetPage = pageNum && pageNum > 0 ? pageNum : 1;
  const fileUrl = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1"}/documents/${docId}/file`;

  return (
    <div className="fixed inset-y-0 right-0 z-[70] flex w-full max-w-xl flex-col border-l border-slate-200 bg-white shadow-2xl">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800">{docTitle}</div>
          <div className="text-xs text-slate-500">第 {targetPage} 页{numPages ? ` / 共 ${numPages} 页` : ""}</div>
        </div>
        <button type="button" className="rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100" onClick={onClose}>
          关闭
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-slate-100 p-4">
        {pdfReady ? (
          <Document
            file={fileUrl}
            onLoadSuccess={(doc) => setNumPages(doc.numPages)}
            loading={<div className="text-sm text-slate-500">PDF 加载中…</div>}
            error={<div className="text-sm text-rose-600">PDF 加载失败</div>}
          >
            <Page pageNumber={Math.min(targetPage, numPages || targetPage)} width={480} />
          </Document>
        ) : (
          <div className="text-sm text-slate-500">初始化 PDF 查看器…</div>
        )}
      </div>
    </div>
  );
}
