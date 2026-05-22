"use client";

import { useState } from "react";
import { X, GripVertical } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  urls: string[];
  onRemove: (url: string) => void;
  onReorder: (urls: string[]) => void;
}

export function HouseStyleGrid({ urls, onRemove, onReorder }: Props) {
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);

  const handleDrop = (toIdx: number) => {
    if (dragIdx === null || dragIdx === toIdx) {
      setDragIdx(null);
      setOverIdx(null);
      return;
    }
    const next = [...urls];
    const [moved] = next.splice(dragIdx, 1);
    next.splice(toIdx, 0, moved);
    onReorder(next);
    setDragIdx(null);
    setOverIdx(null);
  };

  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
      {urls.map((url, i) => (
        <div
          key={url}
          draggable
          onDragStart={() => setDragIdx(i)}
          onDragOver={(e) => {
            e.preventDefault();
            setOverIdx(i);
          }}
          onDragLeave={() => setOverIdx((cur) => (cur === i ? null : cur))}
          onDrop={(e) => {
            e.preventDefault();
            handleDrop(i);
          }}
          onDragEnd={() => {
            setDragIdx(null);
            setOverIdx(null);
          }}
          className={cn(
            "group relative aspect-square rounded-lg border bg-neutral-100 overflow-hidden cursor-grab active:cursor-grabbing",
            overIdx === i && dragIdx !== null && dragIdx !== i
              ? "border-neutral-900 ring-2 ring-neutral-900"
              : "border-neutral-200",
            dragIdx === i ? "opacity-50" : ""
          )}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={`House style ${i + 1}`}
            className="absolute inset-0 size-full object-cover"
            draggable={false}
          />
          <div className="absolute top-1 left-1 inline-flex items-center justify-center rounded bg-black/70 text-white text-xs font-medium size-6">
            {i + 1}
          </div>
          <button
            type="button"
            aria-label="Remove"
            onClick={() => onRemove(url)}
            className="absolute top-1 right-1 inline-flex items-center justify-center rounded bg-black/70 text-white opacity-0 group-hover:opacity-100 transition size-6 hover:bg-black"
          >
            <X className="size-3.5" />
          </button>
          <div className="absolute bottom-1 right-1 rounded bg-black/70 text-white opacity-0 group-hover:opacity-100 transition p-0.5">
            <GripVertical className="size-3.5" />
          </div>
        </div>
      ))}
    </div>
  );
}
