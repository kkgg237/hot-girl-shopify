"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { HelpCircle, AlertTriangle, OctagonX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProductJobCard, ProductJobView } from "@/components/product-job-card";
import type { ImageEditView } from "@/components/image-edit-pair";

interface JobPayload {
  job: {
    id: string;
    referenceImageUrl: string;
    referenceImageUrls?: string[];
    instructions: string;
    status: string;
    createdAt: number;
  };
  productJobs: Array<{
    id: string;
    title: string;
    status: string;
    error: string | null;
    edits: ImageEditView[];
  }>;
}

const MAX_CONCURRENT = 2;

export default function ReviewPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params?.jobId;

  const [data, setData] = useState<JobPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const processingRef = useRef<Set<string>>(new Set());

  const fetchStatus = useCallback(async () => {
    if (!jobId) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}/status`);
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      const json = (await res.json()) as JobPayload;
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load job");
    }
  }, [jobId]);

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 2000);
    return () => clearInterval(t);
  }, [fetchStatus]);

  const cancelBatch = useCallback(async () => {
    if (!jobId) return;
    if (!window.confirm("Stop processing? Any pending products will be cancelled. In-flight requests will finish on their own.")) return;
    try {
      const r = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      toast.success("Batch cancelled.");
      fetchStatus();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Cancel failed");
    }
  }, [jobId, fetchStatus]);

  // kick off processing for pending product jobs (max 2 concurrent)
  useEffect(() => {
    if (!data) return;
    if (data.job.status === "cancelled") return;
    const pending = data.productJobs.filter((p) => p.status === "pending");
    for (const p of pending) {
      if (processingRef.current.size >= MAX_CONCURRENT) break;
      if (processingRef.current.has(p.id)) continue;
      processingRef.current.add(p.id);
      fetch(`/api/edit/process/${p.id}`, { method: "POST" })
        .then(async (r) => {
          if (!r.ok) {
            const j = (await r.json().catch(() => ({}))) as { error?: string };
            throw new Error(j.error || `HTTP ${r.status}`);
          }
        })
        .catch((e: unknown) => {
          toast.error(
            e instanceof Error ? e.message : `Failed to process "${p.title}"`
          );
        })
        .finally(() => {
          processingRef.current.delete(p.id);
          fetchStatus();
        });
    }
  }, [data, fetchStatus]);

  const summary = useMemo(() => {
    if (!data) return { total: 0, done: 0 };
    let total = 0;
    let done = 0;
    for (const pj of data.productJobs) {
      total += 1;
      if (pj.status === "ready" || pj.status === "published") done += 1;
    }
    return { total, done };
  }, [data]);

  if (error && !data) {
    return (
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 flex items-start gap-3">
          <AlertTriangle className="size-4 mt-0.5" />
          <div>
            <div className="font-medium">Couldn&apos;t load this job.</div>
            <div className="text-rose-600/80">{error}</div>
          </div>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="mx-auto w-full max-w-5xl px-6 py-8 space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  const productViews: ProductJobView[] = data.productJobs.map((pj) => ({
    id: pj.id,
    title: pj.title,
    status: pj.status,
    error: pj.error,
    edits: pj.edits,
  }));

  const progress =
    summary.total === 0 ? 0 : Math.round((summary.done / summary.total) * 100);

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review edits</h1>
          <p className="text-sm text-neutral-500">
            Approve, reject, or regenerate. Publish when each product is ready.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {data.job.status !== "cancelled" &&
            data.productJobs.some(
              (p) => p.status === "pending" || p.status === "processing"
            ) && (
              <Button
                variant="outline"
                size="sm"
                onClick={cancelBatch}
                className="text-rose-700 border-rose-200 hover:bg-rose-50 hover:text-rose-800"
              >
                <OctagonX className="size-4" /> Stop batch
              </Button>
            )}
          {data.job.status === "cancelled" && (
            <span className="inline-flex items-center gap-1 text-sm text-rose-700 bg-rose-50 ring-1 ring-rose-200 rounded-full px-3 py-1">
              <OctagonX className="size-3.5" /> Batch cancelled
            </span>
          )}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-900"
                >
                  <HelpCircle className="size-4" /> Shortcuts
                </button>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                <div className="text-xs space-y-1">
                  <div>
                    <kbd>A</kbd> approve · <kbd>R</kbd> reject · <kbd>G</kbd> regenerate
                  </div>
                  <div>
                    <kbd>←</kbd> / <kbd>→</kbd> move between pairs
                  </div>
                </div>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </div>

      <Card className="mb-6">
        <CardContent className="p-5">
          <div className="flex items-start gap-4">
            <div className="flex gap-2 flex-wrap shrink-0">
              {(data.job.referenceImageUrls && data.job.referenceImageUrls.length > 0
                ? data.job.referenceImageUrls
                : [data.job.referenceImageUrl]
              ).map((u, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  key={u + i}
                  src={u}
                  alt={`Reference ${i + 1}`}
                  className="size-24 rounded-md object-cover border border-neutral-200"
                />
              ))}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
                Reference & instructions
              </div>
              <div className="text-sm text-neutral-800 whitespace-pre-wrap">
                {data.job.instructions}
              </div>
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-neutral-500 mb-1">
                  <span>
                    {summary.done} / {summary.total} products ready
                  </span>
                  <span>{progress}%</span>
                </div>
                <Progress value={progress} />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="space-y-4">
        {productViews.map((pj) => (
          <ProductJobCard key={pj.id} pj={pj} onChanged={fetchStatus} />
        ))}
      </div>
    </div>
  );
}
