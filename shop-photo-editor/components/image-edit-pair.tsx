"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Check, RotateCw, X, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ImageEditView {
  id: string;
  originalUrl: string;
  editedUrl: string | null;
  status: string; // pending | approved | rejected | failed | uploaded
  error: string | null;
}

export function ImageEditPair({
  edit,
  productProcessing,
  onChanged,
}: {
  edit: ImageEditView;
  productProcessing: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const act = useCallback(
    async (action: "approve" | "reject" | "regenerate") => {
      setBusy(true);
      try {
        const res = await fetch(`/api/edits/${edit.id}/${action}`, { method: "POST" });
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(j.error || `HTTP ${res.status}`);
        }
        toast.success(
          action === "approve"
            ? "Approved."
            : action === "reject"
              ? "Rejected."
              : "Regenerating..."
        );
        onChanged();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : `Failed to ${action}`);
      } finally {
        setBusy(false);
      }
    },
    [edit.id, onChanged]
  );

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (busy) return;
    const key = e.key.toLowerCase();
    if (key === "a") {
      e.preventDefault();
      act("approve");
    } else if (key === "r") {
      e.preventDefault();
      act("reject");
    } else if (key === "g") {
      e.preventDefault();
      act("regenerate");
    } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const target = e.currentTarget;
      const all = Array.from(
        target.parentElement?.querySelectorAll<HTMLElement>("[data-edit-pair]") ?? []
      );
      const idx = all.indexOf(target);
      const next =
        e.key === "ArrowRight"
          ? all[idx + 1]
          : all[idx - 1];
      if (next) {
        e.preventDefault();
        next.focus();
      }
    }
  };

  const ringClass =
    edit.status === "approved" || edit.status === "uploaded"
      ? "ring-2 ring-emerald-300"
      : edit.status === "rejected"
        ? "ring-2 ring-rose-300"
        : edit.status === "failed"
          ? "ring-2 ring-rose-300"
          : "ring-1 ring-neutral-200";

  const grayed = edit.status === "rejected" ? "grayscale-50" : "";

  return (
    <div
      data-edit-pair
      tabIndex={0}
      onKeyDown={onKey}
      className={cn(
        "rounded-lg p-3 bg-white outline-none focus:ring-offset-2",
        ringClass,
        grayed
      )}
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs font-medium text-neutral-500 mb-1">Original</div>
          <div className="aspect-square rounded-md overflow-hidden bg-neutral-100">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={edit.originalUrl}
              alt="Original"
              className="size-full object-cover"
            />
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-neutral-500 mb-1">Edited</div>
          <div className="aspect-square rounded-md overflow-hidden bg-neutral-100 relative">
            {edit.editedUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={edit.editedUrl}
                alt="Edited"
                className="size-full object-cover"
              />
            ) : edit.status === "failed" ? (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-rose-600 text-xs gap-1 p-3 text-center">
                <AlertTriangle className="size-5" />
                <div>{edit.error || "Generation failed"}</div>
              </div>
            ) : (
              <Skeleton className="size-full" />
            )}
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="text-xs text-neutral-500">
          {edit.status === "approved" && "Approved"}
          {edit.status === "uploaded" && "Published"}
          {edit.status === "rejected" && "Rejected"}
          {edit.status === "failed" && (edit.error || "Failed")}
          {edit.status === "pending" &&
            (edit.editedUrl
              ? "Awaiting review"
              : productProcessing
                ? "Processing..."
                : "Queued")}
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy || !edit.editedUrl || edit.status === "uploaded"}
            onClick={() => act("approve")}
            className="border-emerald-200 text-emerald-700 hover:bg-emerald-50"
          >
            <Check className="size-4" /> Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || edit.status === "uploaded"}
            onClick={() => act("reject")}
            className="border-rose-200 text-rose-700 hover:bg-rose-50"
          >
            <X className="size-4" /> Reject
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || edit.status === "uploaded"}
            onClick={() => act("regenerate")}
            className="border-zinc-200 text-zinc-700 hover:bg-zinc-50"
          >
            <RotateCw className="size-4" /> Regenerate
          </Button>
        </div>
      </div>
    </div>
  );
}
