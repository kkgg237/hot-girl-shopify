import { NextRequest } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { jobs, productJobs, imageEdits } from "@/db/schema";
import { editImage } from "@/lib/gemini";
import { fetchImageBuffer } from "@/lib/fetch-image";
import { saveBlob, readBlobByPublicUrl } from "@/lib/blob";
import { jobReferenceUrls } from "@/lib/job-refs";
import { getSettings } from "@/lib/settings";
import {
  getSubjectMask,
  compositeSubjectOverEdit,
  isMaskReliable,
} from "@/lib/mask-composite";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ productJobId: string }> }
) {
  const { productJobId } = await params;
  try {
    const pj = db
      .select()
      .from(productJobs)
      .where(eq(productJobs.id, productJobId))
      .get();
    if (!pj) return Response.json({ error: "Not found" }, { status: 404 });
    if (pj.status !== "pending") {
      return Response.json({ ok: true, status: pj.status });
    }

    const job = db.select().from(jobs).where(eq(jobs.id, pj.jobId)).get();
    if (!job) return Response.json({ error: "Job not found" }, { status: 404 });

    db.update(productJobs)
      .set({ status: "processing", error: null })
      .where(eq(productJobs.id, pj.id))
      .run();

    const refUrls = jobReferenceUrls(job);
    if (refUrls.length === 0) {
      throw new Error("Job has no reference images");
    }
    const referenceBufs: Buffer[] = [];
    for (const u of refUrls) {
      referenceBufs.push(await readBlobByPublicUrl(u));
    }

    const edits = db
      .select()
      .from(imageEdits)
      .where(eq(imageEdits.productJobId, pj.id))
      .all();

    // Poll DB for cancellation while the loop runs. If the job or this product
    // job is cancelled, abort the in-flight Gemini call and stop the loop.
    const controller = new AbortController();
    const cancelPoll = setInterval(() => {
      try {
        const fresh = db
          .select()
          .from(productJobs)
          .where(eq(productJobs.id, pj.id))
          .get();
        const freshJob = db.select().from(jobs).where(eq(jobs.id, pj.jobId)).get();
        if (
          fresh?.status === "cancelled" ||
          freshJob?.status === "cancelled"
        ) {
          controller.abort();
        }
      } catch {
        /* ignore */
      }
    }, 500);

    let anySuccess = false;
    let anyFailure = false;
    let cancelled = false;

    try {
      for (const e of edits) {
        if (controller.signal.aborted) {
          cancelled = true;
          break;
        }
        if (e.status !== "pending" || e.editedUrl) {
          if (e.editedUrl) anySuccess = true;
          continue;
        }
        try {
          const targetBuf = await fetchImageBuffer(e.originalUrl);
          if (controller.signal.aborted) {
            cancelled = true;
            break;
          }
          // Compute subject mask BEFORE editing. If segmentation is unreliable
          // (close-up textures, no clear foreground), DO NOT call Gemini and
          // just keep the original — better than risking a wrong product.
          const seg = job.extendCanvas
            ? null
            : await getSubjectMask(targetBuf).catch(() => null);

          let finalBuf: Buffer;
          let editNote: string | null = null;

          if (!job.extendCanvas && (!seg || !isMaskReliable(seg.coverage))) {
            // Skip the edit entirely; preserve original.
            finalBuf = targetBuf;
            editNote =
              "Skipped: couldn't isolate subject reliably (close-up or textured frame). Original kept.";
          } else {
            const editedBuf = await editImage({
              referenceBufs,
              targetBuf,
              instructions: job.instructions,
              extendCanvas: !!job.extendCanvas,
              rules: getSettings().promptRules,
              signal: controller.signal,
            });
            finalBuf = seg
              ? await compositeSubjectOverEdit({
                  originalBuf: targetBuf,
                  editedBuf,
                  maskBuf: seg.mask,
                })
              : editedBuf;
          }

          const url = await saveBlob(finalBuf, "jpg");
          db.update(imageEdits)
            .set({ editedUrl: url, status: "pending", error: editNote })
            .where(eq(imageEdits.id, e.id))
            .run();
          anySuccess = true;
        } catch (err) {
          if (controller.signal.aborted) {
            cancelled = true;
            db.update(imageEdits)
              .set({ status: "failed", error: "Cancelled" })
              .where(eq(imageEdits.id, e.id))
              .run();
            break;
          }
          anyFailure = true;
          db.update(imageEdits)
            .set({
              status: "failed",
              error: err instanceof Error ? err.message : "Generation failed",
            })
            .where(eq(imageEdits.id, e.id))
            .run();
        }
      }
    } finally {
      clearInterval(cancelPoll);
    }

    // Mark any still-pending edits as cancelled if we bailed out early.
    if (cancelled) {
      const remaining = db
        .select()
        .from(imageEdits)
        .where(eq(imageEdits.productJobId, pj.id))
        .all();
      for (const e of remaining) {
        if (e.status === "pending" && !e.editedUrl) {
          db.update(imageEdits)
            .set({ status: "failed", error: "Cancelled" })
            .where(eq(imageEdits.id, e.id))
            .run();
        }
      }
    }

    const finalStatus = cancelled
      ? "cancelled"
      : anySuccess
        ? "ready"
        : anyFailure
          ? "failed"
          : "ready";

    db.update(productJobs)
      .set({
        status: finalStatus,
        error: cancelled
          ? "Cancelled by user"
          : !anySuccess && anyFailure
            ? "All images failed"
            : null,
      })
      .where(eq(productJobs.id, pj.id))
      .run();

    return Response.json({ ok: true, status: finalStatus });
  } catch (e) {
    db.update(productJobs)
      .set({
        status: "failed",
        error: e instanceof Error ? e.message : "Processing failed",
      })
      .where(eq(productJobs.id, productJobId))
      .run();
    return Response.json(
      { error: e instanceof Error ? e.message : "Processing failed" },
      { status: 500 }
    );
  }
}
