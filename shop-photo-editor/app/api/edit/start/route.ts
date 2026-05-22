import { NextRequest } from "next/server";
import { z } from "zod";
import crypto from "node:crypto";
import { db } from "@/db";
import { jobs, productJobs, imageEdits } from "@/db/schema";
import { getProductMedia } from "@/lib/shopify";
import { getSettings } from "@/lib/settings";

const Body = z.object({
  // Multi-reference shape; optional so we can fall back to settings.
  referenceUrls: z.array(z.string().min(1)).optional(),
  // Back-compat single shape.
  referenceUrl: z.string().min(1).optional(),
  instructions: z.string().optional().default(""),
  productIds: z.array(z.string().min(1)).min(1),
  extendCanvas: z.boolean().optional(),
});

export async function POST(req: NextRequest) {
  try {
    const json = await req.json();
    const parsed = Body.safeParse(json);
    if (!parsed.success) {
      return Response.json({ error: "Invalid body" }, { status: 400 });
    }
    const { instructions, productIds } = parsed.data;

    let referenceUrls: string[] = [];
    if (parsed.data.referenceUrls && parsed.data.referenceUrls.length > 0) {
      referenceUrls = parsed.data.referenceUrls;
    } else if (parsed.data.referenceUrl) {
      referenceUrls = [parsed.data.referenceUrl];
    }
    if (referenceUrls.length === 0) {
      referenceUrls = getSettings().houseStyleImageUrls;
    }
    if (referenceUrls.length === 0) {
      return Response.json(
        { error: "No reference images. Add some on /settings or pick a tab on /edit." },
        { status: 400 }
      );
    }

    const jobId = crypto.randomUUID();
    db.insert(jobs)
      .values({
        id: jobId,
        referenceImageUrl: referenceUrls[0],
        referenceImageUrls: JSON.stringify(referenceUrls),
        instructions,
        createdAt: Date.now(),
        status: "queued",
        extendCanvas: parsed.data.extendCanvas ? 1 : 0,
      })
      .run();

    for (const productId of productIds) {
      try {
        const { product, media } = await getProductMedia(productId);
        const pjId = crypto.randomUUID();
        db.insert(productJobs)
          .values({
            id: pjId,
            jobId,
            shopifyProductId: product.id,
            title: product.title,
            status: media.length === 0 ? "failed" : "pending",
            error: media.length === 0 ? "No images on product" : null,
          })
          .run();
        media.forEach((m, i) => {
          db.insert(imageEdits)
            .values({
              id: crypto.randomUUID(),
              productJobId: pjId,
              shopifyMediaId: m.id,
              originalUrl: m.url,
              editedUrl: null,
              status: "pending",
              error: null,
              position: i,
            })
            .run();
        });
      } catch (e) {
        const pjId = crypto.randomUUID();
        db.insert(productJobs)
          .values({
            id: pjId,
            jobId,
            shopifyProductId: productId,
            title: productId,
            status: "failed",
            error: e instanceof Error ? e.message : "Failed to load product",
          })
          .run();
      }
    }

    return Response.json({ jobId });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed to start job" },
      { status: 500 }
    );
  }
}
