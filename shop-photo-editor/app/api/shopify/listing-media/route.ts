import { NextRequest } from "next/server";
import { resolveProductRef, getProductMedia } from "@/lib/shopify";

export async function GET(req: NextRequest) {
  try {
    const ref = (req.nextUrl.searchParams.get("ref") ?? "").trim();
    if (!ref) {
      return Response.json({ error: "Missing 'ref'" }, { status: 400 });
    }
    const product = await resolveProductRef(ref);
    const { product: p, media } = await getProductMedia(product.id);
    return Response.json({
      product: { id: p.id, title: p.title },
      images: media.map((m) => ({ id: m.id, url: m.url, altText: m.altText })),
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
