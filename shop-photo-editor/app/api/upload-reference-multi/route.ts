import { NextRequest } from "next/server";
import { saveBlob } from "@/lib/blob";

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_PER_REQUEST = 5;

function extFromType(type: string): string {
  if (type.includes("png")) return "png";
  if (type.includes("webp")) return "webp";
  if (type.includes("gif")) return "gif";
  return "jpg";
}

export async function POST(req: NextRequest) {
  try {
    const form = await req.formData();
    const files = form.getAll("file").filter((f): f is File => f instanceof File);
    if (files.length === 0) {
      return Response.json({ error: "No files provided" }, { status: 400 });
    }
    if (files.length > MAX_PER_REQUEST) {
      return Response.json(
        { error: `Too many files (max ${MAX_PER_REQUEST})` },
        { status: 400 }
      );
    }
    const urls: string[] = [];
    for (const f of files) {
      if (f.size > MAX_BYTES) {
        return Response.json({ error: "An image is larger than 10 MB." }, { status: 413 });
      }
      const buf = Buffer.from(await f.arrayBuffer());
      const type = f.type || "image/jpeg";
      urls.push(await saveBlob(buf, extFromType(type)));
    }
    return Response.json({ urls });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Upload failed" },
      { status: 500 }
    );
  }
}
