import { NextRequest } from "next/server";
import { db } from "@/db";
import { jobs, productJobs } from "@/db/schema";
import { and, eq, inArray } from "drizzle-orm";

export async function POST(
  _req: NextRequest,
  ctx: { params: Promise<{ jobId: string }> }
) {
  try {
    const { jobId } = await ctx.params;
    // Mark job cancelled
    await db.update(jobs).set({ status: "cancelled" }).where(eq(jobs.id, jobId));
    // Cancel any productJobs not yet finished
    await db
      .update(productJobs)
      .set({ status: "cancelled", error: "Cancelled by user" })
      .where(
        and(
          eq(productJobs.jobId, jobId),
          inArray(productJobs.status, ["pending", "processing"])
        )
      );
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Cancel failed" },
      { status: 500 }
    );
  }
}
