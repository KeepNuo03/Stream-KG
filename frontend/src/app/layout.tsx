import type { Metadata } from "next";
import "./globals.css";
import { ToastProvider } from "@/components/Toast";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";

export const metadata: Metadata = {
  title: "stream-kg",
  description: "Incremental personal knowledge graph",
};

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
