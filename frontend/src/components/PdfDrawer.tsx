"use client";

type PdfDrawerProps = {
  docId: string;
  docTitle: string;
  pageNum: number | null;
  open: boolean;
  onClose: () => void;
};

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export function PdfDrawer({ docId, docTitle, pageNum, open, onClose }: PdfDrawerProps) {
  if (!open) return null;

  const targetPage = pageNum && pageNum > 0 ? pageNum : 1;
  const fileUrl = `${API_BASE}/documents/${docId}/file`;
  // 浏览器内置 PDF 查看器支持 #page=N 跳页（Chrome / Edge）
  const viewerUrl = `${fileUrl}#page=${targetPage}`;

  return (
    <div className="fixed inset-y-0 right-0 z-[70] flex w-full max-w-xl flex-col border-l border-slate-200 bg-white shadow-2xl">
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800">{docTitle}</div>
          <div className="text-xs text-slate-500">第 {targetPage} 页</div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <a
            href={viewerUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg px-2 py-1 text-xs text-indigo-600 hover:bg-indigo-50"
          >
            新标签页打开
          </a>
          <button
            type="button"
            className="rounded-lg px-2 py-1 text-xs text-slate-500 hover:bg-slate-100"
            onClick={onClose}
          >
            关闭
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 bg-slate-100">
        <iframe
          key={viewerUrl}
          src={viewerUrl}
          title={docTitle}
          className="h-full w-full min-h-[480px] border-0 bg-white"
        />
      </div>
    </div>
  );
}
