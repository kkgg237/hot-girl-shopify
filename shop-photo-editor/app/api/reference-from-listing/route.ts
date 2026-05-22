import { NextRequest } from "next/server";
import { resolveProductRef } from "@/lib/shopify";
import { fetchImageBuffer } from "@/lib/fetch-image";
import { saveBlob } from "@/lib/blob";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { ref?: string };
    const ref = (body.ref ?? "").trim();
    if (!ref) {
      return Response.json({ error: "Missing 'ref' (URL, handle, or product ID)" }, { status: 400 });
    }
    const product = await resolveProductRef(ref);
    const imageUrl = product.featuredImage?.url;
    if (!imageUrl) {
      return Response.json({ error: "Listing has no featured image" }, { status: 422 });
    }
    const buf = await fetchImageBuffer(imageUrl);
    // Detect ext from content-type fallback to jpg via the image url path
    let ext = "jpg";
    const lower = imageUrl.toLowerCase();
    if (lower.includes(".png")) ext = "png";
    else if (lower.includes(".webp")) ext = "webp";
    else if (lower.includes(".gif")) ext = "gif";
    const url = await saveBlob(buf, ext);
    return Response.json({
      url,
      product: { id: product.id, title: product.title, sourceImageUrl: imageUrl },
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed to load reference" },
      { status: 500 }
    );
  }
}
