"use client";

import { localizeRelation } from "@/lib/kg-labels";

const RELATION_CHIPS = [
  { id: "balanced", label: "均衡" },
  { id: "semantic", label: "语义" },
  { id: "mentions", label: "文档提及" },
  { id: "extends", label: localizeRelation("extends") },
  { id: "improves", label: localizeRelation("improves") },
  { id: "uses", label: localizeRelation("uses") },
  { id: "conflict", label: localizeRelation("conflict") },
];

type GraphToolbarProps = {
  selected: string[];
  onChange: (next: string[]) => void;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  onSearchSubmit: () => void;
};

export function GraphToolbar({
  selected,
  onChange,
  searchQuery,
  onSearchChange,
  onSearchSubmit,
}: GraphToolbarProps) {
  const toggle = (id: string) => {
    if (selected.includes(id)) {
      onChange(selected.filter((item) => item !== id));
    } else {
      onChange([id]);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <div className="flex flex-wrap gap-1">
        {RELATION_CHIPS.map((chip) => (
          <button
            key={chip.id}
            type="button"
            onClick={() => toggle(chip.id)}
            className={`rounded-full px-2 py-0.5 transition ${
              selected.includes(chip.id)
                ? "bg-indigo-600 text-white"
                : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            {chip.label}
          </button>
        ))}
      </div>
      <input
        className="min-w-[140px] flex-1 rounded-lg border border-slate-300 px-2 py-1"
        placeholder="搜索节点…"
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSearchSubmit();
        }}
      />
    </div>
  );
}
