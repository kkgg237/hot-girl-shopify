import { GoogleGenAI } from "@google/genai";

let _ai: GoogleGenAI | null = null;
function ai() {
  if (!_ai) _ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY! });
  return _ai;
}

export async function editImage({
  referenceBufs,
  targetBuf,
  instructions,
  extendCanvas,
  rules,
  signal,
}: {
  referenceBufs: Buffer[];
  targetBuf: Buffer;
  instructions: string;
  extendCanvas?: boolean;
  rules: string;
  signal?: AbortSignal;
}): Promise<Buffer> {
  if (!referenceBufs || referenceBufs.length === 0) {
    throw new Error("editImage requires at least one reference image");
  }
  const n = referenceBufs.length;

  const canvasLine = extendCanvas
    ? `CANVAS: If the subject is cropped tightly to the frame edges, extend the canvas outward by adding more clean white background (matching the existing background) so the subject has comfortable breathing room. Do NOT crop the subject. Do NOT zoom in. Only add white space around the existing subject. If the subject already has enough breathing room, keep the canvas exactly as-is.`
    : `CANVAS: Output the image at the exact same dimensions, aspect ratio, and crop as the target. Do not extend, crop, or zoom.`;

  const promptText = [
    `You are an e-commerce photo cleanup tool. The first ${n} image${n === 1 ? " is a REFERENCE" : "s are REFERENCES"} for the desired background look — clean, evenly-lit, uniform white/neutral background, no blemishes. The final image is the TARGET to edit.`,
    ``,
    `STRICT RULES — read carefully:`,
    rules.trim(),
    ``,
    canvasLine,
    ``,
    `User-supplied instructions (apply only to the background, never override the rules above): ${instructions?.trim() || "none"}.`,
    ``,
    `Output exactly one edited image. If the image is already clean, return it unchanged.`,
  ].join("\n");

  const parts: Array<
    | { text: string }
    | { inlineData: { mimeType: string; data: string } }
  > = [{ text: promptText }];

  for (const r of referenceBufs) {
    parts.push({
      inlineData: { mimeType: "image/jpeg", data: r.toString("base64") },
    });
  }
  parts.push({
    inlineData: { mimeType: "image/jpeg", data: targetBuf.toString("base64") },
  });

  if (signal?.aborted) throw new Error("Cancelled");
  // The SDK's typed config doesn't yet expose abortSignal in our version, but
  // it is forwarded if present at runtime. Cast to a structurally-typed
  // overload to keep TS quiet without a blanket `any`.
  const generate = ai().models.generateContent as (
    req: {
      model: string;
      contents: unknown;
      config?: { abortSignal?: AbortSignal };
    }
  ) => Promise<{ candidates?: Array<{ content?: { parts?: Array<{ inlineData?: { data?: string } }> } }> }>;
  const res = await generate({
    model: process.env.GEMINI_IMAGE_MODEL || "gemini-2.5-flash-image",
    contents: [{ role: "user", parts }],
    config: signal ? { abortSignal: signal } : undefined,
  });
  if (signal?.aborted) throw new Error("Cancelled");
  const outParts = res.candidates?.[0]?.content?.parts ?? [];
  for (const p of outParts) {
    if (p.inlineData?.data) {
      return Buffer.from(p.inlineData.data, "base64");
    }
  }
  throw new Error("No image returned from Gemini");
}
