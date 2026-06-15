import type { Metadata } from "next";
import "./globals.css";
import "highlight.js/styles/github-dark.css";
import { ToastProvider } from "@/components/Toast";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";

export const metadata: Metadata = {
  title: "stream-kg",
  description: "Incremental personal knowledge graph",
};

// 公式渲染采用 KaTeX 的 MathML 输出模式（见 MarkdownMessage.tsx 中的 rehypeKatex 配置）。
// MathML 由浏览器原生数学排版引擎渲染（Chrome 109+/Safari 14+/Firefox 全版本），
// 完全不依赖 KaTeX 私有字体；因此这里不再引入 katex.min.css、不再 preload 字体。
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <ConfirmDialogProvider>
          <ToastProvider>{children}</ToastProvider>
        </ConfirmDialogProvider>
      </body>
    </html>
  );
}
