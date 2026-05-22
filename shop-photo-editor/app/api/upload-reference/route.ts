import { NextRequest } from "next/server";
import { saveBlob } from "@/lib/blob";

const MAX = 10 * 1024 * 1024;

export async function POST(req: NextRequest) {
  try {
    const form = await req.formData();
    const file = form.get("file");
    if (!file || !(file instanceof Blob)) {
      return Response.json({ error: "Missing file" }, { status: 400 });
    }
    if (file.size > MAX) {
      return Response.json({ error: "File too large (max 10 MB)" }, { status: 413 });
    }
    const buf = Buffer.from(await file.arrayBuffer());
    const type = file.type || "image/jpeg";
    let ext = "jpg";
    if (type.includes("png")) ext = "png";
    else if (type.includes("webp")) ext = "webp";
    else if (type.includes("gif")) ext = "gif";
    else if (type.includes("jpeg") || type.includes("jpg")) ext = "jpg";
    const url = await saveBlob(buf, ext);
    return Response.json({ url });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Upload failed" },
      { status: 500 }
    );
  }
}
