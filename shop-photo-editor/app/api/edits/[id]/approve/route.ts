import { NextRequest } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { imageEdits } from "@/db/schema";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  try {
    const edit = db.select().from(imageEdits).where(eq(imageEdits.id, id)).get();
    if (!edit) return Response.json({ error: "Not found" }, { status: 404 });
    if (!edit.editedUrl) {
      return Response.json({ error: "Not ready" }, { status: 400 });
    }
    db.update(imageEdits)
      .set({ status: "approved", error: null })
      .where(eq(imageEdits.id, id))
      .run();
    return Response.json({ ok: true });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Failed" },
      { status: 500 }
    );
  }
}
