import { NextRequest } from "next/server";
import { resolveProductRef, getProductMedia } from "@/lib/shopify";
import { fetchImageBuffer } from "@/lib/fetch-image";
import { saveBlob } from "@/lib/blob";

const MAX_PER_REQUEST = 5;

function extFromUrl(url: string): string {
  const lower = url.toLowerCase();
  if (lower.includes(".png")) return "png";
  if (lower.includes(".webp")) return "webp";
  if (lower.includes(".gif")) return "gif";
  return "jpg";
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { ref?: string; imageUrls?: string[] };
    const ref = (body.ref ?? "").trim();
    if (!ref) {
      return Response.json({ error: "Missing 'ref'" }, { status: 400 });
    }
    const product = await resolveProductRef(ref);
    const { media } = await getProductMedia(product.id);

    let pickUrls: string[];
    if (Array.isArray(body.imageUrls) && body.imageUrls.length > 0) {
      const allowed = new Set(media.map((m) => m.url));
      pickUrls = body.imageUrls.filter((u) => allowed.has(u));
    } else {
      const featured = product.featuredImage?.url;
      pickUrls = featured ? [featured] : media[0] ? [media[0].url] : [];
    }

    if (pickUrls.length === 0) {
      return Response.json({ error: "No images available on listing" }, { status: 422 });
    }

    const toFetch = pickUrls.slice(0, MAX_PER_REQUEST);
    const urls: string[] = [];
    for (const u of toFetch) {
      const buf = await fetchImageBuffer(u);
      urls.push(await saveBlob(buf, extFromUrl(u)));
    }
    return Response.json({
      urls,
      product: { id: product.id, title: product.title },
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
