"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ChevronDown, ChevronUp } from "lucide-react";
import { ImageEditPair, ImageEditView } from "@/components/image-edit-pair";
import { cn } from "@/lib/utils";

export interface ProductJobView {
  id: string;
  title: string;
  status: string; // pending | processing | ready | published | failed
  error: string | null;
  edits: ImageEditView[];
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "ready":
      return "bg-amber-50 text-amber-700 ring-1 ring-amber-200";
    case "published":
      return "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200";
    case "processing":
      return "bg-sky-50 text-sky-700 ring-1 ring-sky-200";
    case "failed":
      return "bg-rose-50 text-rose-700 ring-1 ring-rose-200";
    default:
      return "bg-neutral-100 text-neutral-700 ring-1 ring-neutral-200";
  }
}

export function ProductJobCard({
  pj,
  onChanged,
}: {
  pj: ProductJobView;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(pj.status !== "published");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);

  const stats = useMemo(() => {
    let approved = 0;
    let pending = 0;
    let rejected = 0;
    let failed = 0;
    let uploaded = 0;
    for (const e of pj.edits) {
      if (e.status === "approved") approved++;
      else if (e.status === "rejected") rejected++;
      else if (e.status === "failed") failed++;
      else if (e.status === "uploaded") uploaded++;
      else pending++;
    }
    return { approved, pending, rejected, failed, uploaded };
  }, [pj.edits]);

  const canPublish =
    pj.status !== "published" &&
    stats.pending === 0 &&
    stats.approved >= 1 &&
    !publishing;

  const productProcessing = pj.status === "processing";

  const publish = async () => {
    setPublishing(true);
    try {
      const res = await fetch(`/api/publish/${pj.id}`, { method: "POST" });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      toast.success(`Published to "${pj.title}".`);
      setConfirmOpen(false);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  };

  return (
    <Card>
      <CardContent className="p-5">
        <div
          className="flex items-start justify-between gap-3 cursor-pointer"
          onClick={() => setOpen((o) => !o)}
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span
                className={cn(
                  "text-xs px-2 py-0.5 rounded-full font-medium",
                  statusBadgeClass(pj.status)
                )}
              >
                {pj.status}
              </span>
              <Badge variant="outline" className="text-xs">
                {pj.edits.length} {pj.edits.length === 1 ? "image" : "images"}
              </Badge>
              <span className="text-xs text-neutral-500">
                {stats.approved} approved · {stats.rejected} rejected
                {stats.failed > 0 ? ` · ${stats.failed} failed` : ""}
                {stats.pending > 0 ? ` · ${stats.pending} pending` : ""}
              </span>
            </div>
            <div className="font-medium truncate">{pj.title}</div>
            {pj.error && (
              <div className="text-xs text-rose-600 mt-1">{pj.error}</div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {pj.status !== "published" && (
              <Button
                size="sm"
                disabled={!canPublish}
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmOpen(true);
                }}
              >
                Publish to Shopify
              </Button>
            )}
            <button
              type="button"
              className="text-neutral-500 hover:text-neutral-900"
              onClick={(e) => {
                e.stopPropagation();
                setOpen((o) => !o);
              }}
            >
              {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
            </button>
          </div>
        </div>

        {open && (
          <div className="mt-4 grid grid-cols-1 gap-3">
            {pj.edits.map((e) => (
              <ImageEditPair
                key={e.id}
                edit={e}
                productProcessing={productProcessing}
                onChanged={onChanged}
              />
            ))}
          </div>
        )}

        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Publish to Shopify?</DialogTitle>
              <DialogDescription>
                Replace {stats.approved} {stats.approved === 1 ? "image" : "images"} on
                &quot;{pj.title}&quot;. Originals will be deleted permanently. This cannot
                be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setConfirmOpen(false)}
                disabled={publishing}
              >
                Cancel
              </Button>
              <Button onClick={publish} disabled={publishing}>
                {publishing ? "Publishing..." : "Publish"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
