"use client";

import { useEffect, useRef } from "react";
import { Markmap } from "markmap-view";
import { Transformer } from "markmap-lib";

type MindMapCanvasProps = {
  markdown: string;
};

const transformer = new Transformer();

export function MindMapCanvas({ markdown }: MindMapCanvasProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const markmapRef = useRef<Markmap | null>(null);

  useEffect(() => {
    if (!svgRef.current || !markdown.trim()) return;
    const { root } = transformer.transform(markdown);
    if (!markmapRef.current) {
      markmapRef.current = Markmap.create(svgRef.current, { autoFit: true }, root);
    } else {
      markmapRef.current.setData(root);
      markmapRef.current.fit();
    }
  }, [markdown]);

  useEffect(() => {
    const onResize = () => markmapRef.current?.fit();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  if (!markdown.trim()) {
    return (
      <div className="flex h-full min-h-[280px] items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-500">
        暂无思维导图内容
      </div>
    );
  }

  return (
    <div className="h-full min-h-[280px] w-full overflow-hidden rounded-xl border border-slate-200 bg-white">
      <svg ref={svgRef} className="h-full w-full" />
    </div>
  );
}
