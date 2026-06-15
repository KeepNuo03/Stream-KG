"use client";

import { ENTITY_TYPE_COLORS, RELATION_COLORS } from "@/components/GraphCanvas";

export function GraphLegend() {
  return (
    <div className="rounded-xl border border-slate-200 bg-white/95 p-2 text-[10px] shadow-sm">
      <div className="mb-1 font-semibold text-slate-700">图例</div>
      <div className="mb-2 max-h-24 space-y-0.5 overflow-auto">
        {Object.entries(ENTITY_TYPE_COLORS)
          .filter(([k]) => k !== "document")
          .map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-slate-600">{type}</span>
            </div>
          ))}
      </div>
      <div className="space-y-0.5">
        {Object.entries(RELATION_COLORS)
          .slice(0, 8)
          .map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-3" style={{ backgroundColor: color }} />
              <span className="text-slate-600">{type}</span>
            </div>
          ))}
      </div>
    </div>
  );
}
