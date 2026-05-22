import { NextRequest } from "next/server";
import { resolveProductRef, getProductMedia } from "@/lib/shopify";
import { fetchImageBuffer } from "@/lib/fetch-image";
import { saveBlob } from "@/lib/blob";
import { getSettings, setHouseStyle } from "@/lib/settings";

const MAX_TOTAL = 5;

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

    const current = getSettings().houseStyleImageUrls;
    if (current.length >= MAX_TOTAL) {
      return Response.json(
        { error: `House style already has ${MAX_TOTAL} images. Remove one before adding more.` },
        { status: 400 }
      );
    }

    const product = await resolveProductRef(ref);
    const { media } = await getProductMedia(product.id);

    let urlsToFetch: string[];
    if (Array.isArray(body.imageUrls) && body.imageUrls.length > 0) {
      const allowed = new Set(media.map((m) => m.url));
      urlsToFetch = body.imageUrls.filter((u) => allowed.has(u));
    } else {
      const featured = product.featuredImage?.url;
      urlsToFetch = featured ? [featured] : media[0] ? [media[0].url] : [];
    }

    if (urlsToFetch.length === 0) {
      return Response.json({ error: "No images available on listing" }, { status: 422 });
    }

    const slots = MAX_TOTAL - current.length;
    const toFetch = urlsToFetch.slice(0, slots);

    const saved: string[] = [];
    for (const u of toFetch) {
      const buf = await fetchImageBuffer(u);
      saved.push(await saveBlob(buf, extFromUrl(u)));
    }

    const next = [...current, ...saved].slice(0, MAX_TOTAL);
    setHouseStyle(next);
    return Response.json({ houseStyleImageUrls: next });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed to add from listing" },
      { status: 500 }
    );
  }
}
