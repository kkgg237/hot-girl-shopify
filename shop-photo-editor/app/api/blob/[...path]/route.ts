import { NextRequest } from "next/server";
import { readBlobByFilename } from "@/lib/blob";

const MIME: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  gif: "image/gif",
  bin: "application/octet-stream",
};

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  try {
    const { path } = await params;
    if (!path || path.length === 0) {
      return Response.json({ error: "Missing path" }, { status: 400 });
    }
    if (path.length > 1) {
      return Response.json({ error: "Invalid path" }, { status: 400 });
    }
    const name = path[0];
    if (name.includes("..") || name.includes("/") || name.includes("\\")) {
      return Response.json({ error: "Invalid path" }, { status: 400 });
    }
    const buf = await readBlobByFilename(name);
    const ext = name.split(".").pop()?.toLowerCase() ?? "bin";
    const mime = MIME[ext] ?? "application/octet-stream";
    const ab = new ArrayBuffer(buf.byteLength);
    new Uint8Array(ab).set(buf);
    return new Response(ab, {
      headers: {
        "content-type": mime,
        "cache-control": "private, max-age=3600",
      },
    });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error ? e.message : "Not found" },
      { status: 404 }
    );
  }
}
