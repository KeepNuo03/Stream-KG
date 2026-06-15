"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";

type Props = {
  content: string;
  className?: string;
};

function MarkdownMessageImpl({ content, className }: Props) {
  return (
    <div
      className={`markdown-message text-[13.5px] leading-7 text-slate-800 ${className ?? ""}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          [rehypeHighlight, { detect: true, ignoreMissing: true }],
          // 用 MathML 输出替代默认的 HTML + KaTeX 字体方案。
          // 历史踩坑：HTML 方案依赖 KaTeX 私有字体的同步 metrics 计算 layout，
          // 而浏览器 preload+@font-face 在 Next 15 环境下时序复杂，KaTeX 同步渲染
          // 拿不到正确字体 metrics，导致分数/根号/上下标全错位。
          // MathML 由浏览器原生数学排版（Chrome 109+/Safari 14+/Firefox 全版本），
          // 零字体依赖，零打包陷阱，零 Tailwind preflight 冲突。
          [rehypeKatex, { strict: false, throwOnError: false, output: "mathml" }],
        ]}
        components={{
          h1: ({ children }) => (
            <h1 className="mt-4 mb-2 text-base font-semibold text-slate-900 first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mt-4 mb-2 text-[15px] font-semibold text-slate-900 first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mt-3 mb-1.5 text-sm font-semibold text-slate-800 first:mt-0">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="mt-3 mb-1 text-sm font-semibold text-slate-700 first:mt-0">
              {children}
            </h4>
          ),
          p: ({ children }) => (
            <p className="my-2 first:mt-0 last:mb-0">{children}</p>
          ),
          ul: ({ children }) => (
            <ul className="my-2 list-disc space-y-1 pl-5 first:mt-0 last:mb-0 marker:text-slate-400">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="my-2 list-decimal space-y-1 pl-5 first:mt-0 last:mb-0 marker:text-slate-400">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="pl-1">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-slate-900">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 underline decoration-blue-300 underline-offset-2 transition hover:text-blue-700 hover:decoration-blue-500"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-slate-300 bg-slate-50/60 px-3 py-1 text-slate-600">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-3 border-slate-200" />,
          pre: ({ children }) => (
            <pre className="my-2 overflow-x-auto rounded-lg bg-slate-900 px-3 py-2.5 text-[12.5px] leading-6 text-slate-100">
              {children}
            </pre>
          ),
          code: ({ className: codeClassName, children }) => {
            const isFenced =
              typeof codeClassName === "string" &&
              (codeClassName.startsWith("language-") || codeClassName.includes("hljs"));
            if (isFenced) {
              return <code className={`${codeClassName ?? ""} font-mono`}>{children}</code>;
            }
            return (
              <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.88em] text-rose-700">
                {children}
              </code>
            );
          },
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-left text-[12.5px]">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-slate-100/70 text-slate-700">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border border-slate-200 px-2 py-1 font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-slate-200 px-2 py-1 align-top">
              {children}
            </td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownMessage = memo(MarkdownMessageImpl);
