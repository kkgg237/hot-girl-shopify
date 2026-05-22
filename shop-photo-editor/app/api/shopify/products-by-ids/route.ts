import { NextRequest } from "next/server";
import { productsByIds } from "@/lib/shopify";

export async function GET(req: NextRequest) {
  try {
    const ids = (req.nextUrl.searchParams.get("ids") || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (ids.length === 0) return Response.json({ products: [] });
    const products = await productsByIds(ids);
    return Response.json({
      products: products.map((p) => ({
        id: p.id,
        title: p.title,
        featuredImageUrl: p.featuredImage?.url ?? null,
      })),
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed to load products" },
      { status: 500 }
    );
  }
}
