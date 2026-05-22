import { NextRequest } from "next/server";
import { z } from "zod";
import { setPromptRules } from "@/lib/settings";

const Body = z.object({ rules: z.string().max(8000) });

export async function POST(req: NextRequest) {
  try {
    const json = await req.json();
    const parsed = Body.safeParse(json);
    if (!parsed.success) {
      return Response.json({ error: "Invalid body" }, { status: 400 });
    }
    setPromptRules(parsed.data.rules);
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed to save" },
      { status: 500 }
    );
  }
}
