import { NextRequest } from "next/server";
import { listProducts } from "@/lib/shopify";

export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const cursor = sp.get("cursor") || undefined;
    const q = sp.get("q") || undefined;
    const { products, pageInfo } = await listProducts({ cursor, query: q });
    return Response.json({
      products: products.map((p) => ({
        id: p.id,
        title: p.title,
        featuredImageUrl: p.featuredImage?.url ?? p.images[0]?.url ?? null,
        imagesCount: p.images.length,
        totalInventory: p.totalInventory,
      })),
      pageInfo,
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed to load products" },
      { status: 500 }
    );
  }
}
