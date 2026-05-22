import { NextRequest } from "next/server";
import { eq, asc } from "drizzle-orm";
import { db } from "@/db";
import { jobs, productJobs, imageEdits } from "@/db/schema";
import { jobReferenceUrls } from "@/lib/job-refs";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ jobId: string }> }
) {
  const { jobId } = await params;
  try {
    const job = db.select().from(jobs).where(eq(jobs.id, jobId)).get();
    if (!job) return Response.json({ error: "Not found" }, { status: 404 });
    const pjs = db
      .select()
      .from(productJobs)
      .where(eq(productJobs.jobId, jobId))
      .all();

    const out = [];
    for (const pj of pjs) {
      const edits = db
        .select()
        .from(imageEdits)
        .where(eq(imageEdits.productJobId, pj.id))
        .orderBy(asc(imageEdits.position))
        .all();
      out.push({
        id: pj.id,
        title: pj.title,
        status: pj.status,
        error: pj.error,
        edits: edits.map((e) => ({
          id: e.id,
          originalUrl: e.originalUrl,
          editedUrl: e.editedUrl,
          status: e.status,
          error: e.error,
        })),
      });
    }

    const referenceImageUrls = jobReferenceUrls(job);

    return Response.json({
      job: {
        ...job,
        referenceImageUrls,
      },
      productJobs: out,
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
