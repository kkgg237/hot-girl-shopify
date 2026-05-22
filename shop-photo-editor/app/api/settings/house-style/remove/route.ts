import { NextRequest } from "next/server";
import { getSettings, setHouseStyle } from "@/lib/settings";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { url?: string };
    const url = (body.url ?? "").trim();
    if (!url) {
      return Response.json({ error: "Missing 'url'" }, { status: 400 });
    }
    const current = getSettings().houseStyleImageUrls;
    const next = current.filter((u) => u !== url);
    setHouseStyle(next);
    return Response.json({ houseStyleImageUrls: next });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
