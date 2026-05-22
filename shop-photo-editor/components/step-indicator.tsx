"use client";

import { cn } from "@/lib/utils";

export interface StepIndicatorProps {
  index: number;
  label: string;
  active: boolean;
  done: boolean;
}

export function StepIndicator({ index, label, active, done }: StepIndicatorProps) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          "inline-flex size-6 items-center justify-center rounded-full text-xs font-semibold ring-1",
          active && "bg-neutral-900 text-white ring-neutral-900",
          !active && done && "bg-emerald-50 text-emerald-700 ring-emerald-200",
          !active && !done && "bg-white text-neutral-500 ring-neutral-200"
        )}
      >
        {index}
      </span>
      <span
        className={cn(
          "text-sm font-medium",
          active ? "text-neutral-900" : "text-neutral-500"
        )}
      >
        {label}
      </span>
    </div>
  );
}
