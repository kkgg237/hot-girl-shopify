import { NextRequest } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { productJobs, imageEdits } from "@/db/schema";
import { readBlobByPublicUrl } from "@/lib/blob";
import {
  stagedUploadCreate,
  uploadToStaged,
  productCreateMedia,
  productDeleteMedia,
} from "@/lib/shopify";

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
    if (pj.status === "published") {
      return Response.json({ ok: true, status: "published" });
    }

    const edits = db
      .select()
      .from(imageEdits)
      .where(eq(imageEdits.productJobId, pj.id))
      .all();

    const pending = edits.filter((e) => e.status === "pending");
    if (pending.length > 0) {
      return Response.json(
        { error: "There are still pending images" },
        { status: 400 }
      );
    }
    const approved = edits.filter((e) => e.status === "approved" && e.editedUrl);
    if (approved.length === 0) {
      return Response.json({ error: "No approved images to publish" }, { status: 400 });
    }

    const uploadedIds: string[] = [];

    for (const e of approved) {
      if (!e.editedUrl) continue;
      const buf = await readBlobByPublicUrl(e.editedUrl);
      const filename = `${e.id}.jpg`;
      const target = await stagedUploadCreate(filename, "image/jpeg", buf.byteLength);
      await uploadToStaged(target, buf);
      const newMediaId = await productCreateMedia(
        pj.shopifyProductId,
        target.resourceUrl,
        null
      );
      uploadedIds.push(newMediaId);
      db.update(imageEdits)
        .set({ status: "uploaded", error: null })
        .where(eq(imageEdits.id, e.id))
        .run();
    }

    // Delete the originals corresponding to the approved edits
    const oldMediaIds = approved.map((e) => e.shopifyMediaId);
    await productDeleteMedia(pj.shopifyProductId, oldMediaIds);

    db.update(productJobs)
      .set({ status: "published", error: null })
      .where(eq(productJobs.id, pj.id))
      .run();

    return Response.json({
      ok: true,
      status: "published",
      uploaded: uploadedIds.length,
      deleted: oldMediaIds.length,
    });
  } catch (e) {
    db.update(productJobs)
      .set({
        error: e instanceof Error ? e.message : "Publish failed",
      })
      .where(eq(productJobs.id, productJobId))
      .run();
    return Response.json(
      { error: e instanceof Error ? e.message : "Publish failed" },
      { status: 500 }
    );
  }
}
