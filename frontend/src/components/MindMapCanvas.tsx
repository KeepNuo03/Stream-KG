"use client";

import { useEffect, useRef } from "react";
import { Markmap } from "markmap-view";
import { Transformer } from "markmap-lib";

type MindMapCanvasProps = {
  markdown: string;
};

const transformer = new Transformer();

function syncSvgPixelSize(svg: SVGSVGElement, width: number, height: number): boolean {
  if (width <= 0 || height <= 0) return false;
  const w = Math.round(width);
  const h = Math.round(height);
  svg.setAttribute("width", String(w));
  svg.setAttribute("height", String(h));
  svg.style.width = `${w}px`;
  svg.style.height = `${h}px`;
  return true;
}

/** markmap.fit() 在容器无尺寸或 SVG 仍为相对长度时会抛 SVGLength 异常 */
async function safeFit(markmap: Markmap | null, container: HTMLElement | null) {
  if (!markmap || !container) return;
  const svg = container.querySelector("svg");
  if (!svg) return;
  const { width, height } = container.getBoundingClientRect();
  if (!syncSvgPixelSize(svg, width, height)) return;
  try {
    await markmap.fit();
  } catch {
    // 布局未就绪时忽略
  }
}

export function MindMapCanvas({ markdown }: MindMapCanvasProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const markmapRef = useRef<Markmap | null>(null);
  const markdownRef = useRef(markdown);
  markdownRef.current = markdown;

  useEffect(() => {
    const container = containerRef.current;
    const svg = svgRef.current;
    if (!container || !svg) return;

    let rafId = 0;
    let disposed = false;

    const render = () => {
      if (disposed) return;
      const text = markdownRef.current.trim();
      if (!text) return;

      const { width, height } = container.getBoundingClientRect();
      if (!syncSvgPixelSize(svg, width, height)) return;

      const { root } = transformer.transform(text);
      if (!markmapRef.current) {
        markmapRef.current = Markmap.create(svg, { autoFit: false }, root);
      } else {
        markmapRef.current.setData(root);
      }
      void safeFit(markmapRef.current, container);
    };

    const scheduleRender = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(render);
    };

    const ro = new ResizeObserver(scheduleRender);
    ro.observe(container);
    scheduleRender();

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      ro.disconnect();
      markmapRef.current = null;
      svg.replaceChildren();
    };
  }, [markdown]);

  if (!markdown.trim()) {
    return (
      <div className="flex h-full min-h-[280px] items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-500">
        暂无思维导图内容
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="h-full min-h-[280px] w-full overflow-hidden rounded-xl border border-slate-200 bg-white"
    >
      <svg ref={svgRef} className="block" />
    </div>
  );
}
