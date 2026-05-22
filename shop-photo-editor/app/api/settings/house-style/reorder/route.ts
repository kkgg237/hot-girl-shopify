import { NextRequest } from "next/server";
import { getSettings, setHouseStyle } from "@/lib/settings";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { urls?: string[] };
    const urls = Array.isArray(body.urls) ? body.urls : null;
    if (!urls) {
      return Response.json({ error: "Missing 'urls'" }, { status: 400 });
    }
    const current = getSettings().houseStyleImageUrls;
    if (urls.length !== current.length) {
      return Response.json(
        { error: "Reorder must contain the same set of urls" },
        { status: 400 }
      );
    }
    const a = [...current].sort();
    const b = [...urls].sort();
    for (let i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) {
        return Response.json(
          { error: "Reorder must contain the same set of urls" },
          { status: 400 }
        );
      }
    }
    setHouseStyle(urls);
    return Response.json({ houseStyleImageUrls: urls });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
