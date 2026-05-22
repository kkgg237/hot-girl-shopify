"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { StepIndicator } from "@/components/step-indicator";
import { ImageIcon, Settings as SettingsIcon } from "lucide-react";

const STEPS = [
  { label: "Catalog", match: (p: string) => p === "/" },
  { label: "Edit", match: (p: string) => p.startsWith("/edit") },
  { label: "Review", match: (p: string) => p.startsWith("/review") },
];

export function TopNav() {
  const pathname = usePathname() || "/";
  const activeIdx = STEPS.findIndex((s) => s.match(pathname));

  return (
    <header className="sticky top-0 z-30 border-b border-neutral-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <ImageIcon className="size-5 text-neutral-700" />
          <span>Photo Editor</span>
        </Link>
        <div className="flex items-center gap-4">
          {STEPS.map((s, i) => (
            <StepIndicator
              key={s.label}
              index={i + 1}
              label={s.label}
              active={i === activeIdx}
              done={activeIdx > i}
            />
          ))}
        </div>
        <div className="ml-auto">
          <Link
            href="/settings"
            aria-label="Settings"
            className="inline-flex items-center justify-center rounded-md p-2 text-neutral-500 hover:text-neutral-900 hover:bg-neutral-100 transition"
          >
            <SettingsIcon className="size-4" />
          </Link>
        </div>
      </div>
    </header>
  );
}
