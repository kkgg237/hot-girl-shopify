import { NextRequest } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { imageEdits, productJobs, jobs } from "@/db/schema";
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
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const edit = db.select().from(imageEdits).where(eq(imageEdits.id, id)).get();
    if (!edit) return Response.json({ error: "Not found" }, { status: 404 });

    const pj = db
      .select()
      .from(productJobs)
      .where(eq(productJobs.id, edit.productJobId))
      .get();
    if (!pj) return Response.json({ error: "Product job missing" }, { status: 404 });

    const job = db.select().from(jobs).where(eq(jobs.id, pj.jobId)).get();
    if (!job) return Response.json({ error: "Job missing" }, { status: 404 });

    db.update(imageEdits)
      .set({ status: "pending", error: null })
      .where(eq(imageEdits.id, id))
      .run();

    try {
      const refUrls = jobReferenceUrls(job);
      if (refUrls.length === 0) throw new Error("Job has no reference images");
      const referenceBufs: Buffer[] = [];
      for (const u of refUrls) referenceBufs.push(await readBlobByPublicUrl(u));
      const targetBuf = await fetchImageBuffer(edit.originalUrl);
      const seg = job.extendCanvas
        ? null
        : await getSubjectMask(targetBuf).catch(() => null);

      let finalBuf: Buffer;
      let editNote: string | null = null;
      if (!job.extendCanvas && (!seg || !isMaskReliable(seg.coverage))) {
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
        .where(eq(imageEdits.id, id))
        .run();
      return Response.json({ ok: true });
    } catch (err) {
      db.update(imageEdits)
        .set({
          status: "failed",
          error: err instanceof Error ? err.message : "Regenerate failed",
        })
        .where(eq(imageEdits.id, id))
        .run();
      throw err;
    }
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
