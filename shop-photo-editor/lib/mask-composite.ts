import sharp from "sharp";
import { removeBackground } from "@imgly/background-removal-node";

/**
 * Returns a single-channel PNG (8-bit alpha) where 255 = subject, 0 = background,
 * along with the fraction of pixels classified as subject.
 *
 * Uses U²-Net style background removal under the hood. Same size as input.
 */
export async function getSubjectMask(originalBuf: Buffer): Promise<{
  mask: Buffer;
  coverage: number; // 0..1 — fraction of pixels considered subject
}> {
  const result = await removeBackground(originalBuf);
  const arr = await result.arrayBuffer();
  const cutoutBuf = Buffer.from(arr);
  // Extract the alpha channel as a single-channel image (subject = white).
  const mask = await sharp(cutoutBuf).extractChannel(3).png().toBuffer();
  // Sample coverage from the raw alpha channel.
  const { data, info } = await sharp(cutoutBuf)
    .extractChannel(3)
    .raw()
    .toBuffer({ resolveWithObject: true });
  let subjectPx = 0;
  for (let i = 0; i < data.length; i++) if (data[i] > 128) subjectPx++;
  const coverage = subjectPx / (info.width * info.height);
  return { mask, coverage };
}

/**
 * A mask is "reliable" only if a meaningful but not totalitarian portion of the
 * frame is identified as subject. Out of these bounds (e.g. extreme close-ups,
 * pure-white catalog shots with no subject detected, or whole-frame textures),
 * we treat the segmenter as having failed and skip the edit.
 */
export const MASK_RELIABLE_MIN = 0.05;
export const MASK_RELIABLE_MAX = 0.95;
export function isMaskReliable(coverage: number): boolean {
  return coverage >= MASK_RELIABLE_MIN && coverage <= MASK_RELIABLE_MAX;
}

/**
 * Returns the result of compositing the ORIGINAL subject pixels (defined by mask)
 * over the EDITED background. Guarantees the subject pixels are byte-identical to
 * the original — the model can never alter the bag/clothing/model.
 *
 * If editedBuf has different dimensions, it's resized to match the original.
 * Subject mask is feathered slightly to avoid hard composite edges.
 */
export async function compositeSubjectOverEdit({
  originalBuf,
  editedBuf,
  maskBuf,
  feather = 1,
}: {
  originalBuf: Buffer;
  editedBuf: Buffer;
  maskBuf: Buffer;
  feather?: number;
}): Promise<Buffer> {
  const orig = sharp(originalBuf);
  const meta = await orig.metadata();
  const W = meta.width;
  const H = meta.height;
  if (!W || !H) throw new Error("Could not read original image dimensions");

  // Normalize edited to match original dimensions.
  const editedResized = await sharp(editedBuf)
    .resize(W, H, { fit: "fill" })
    .removeAlpha()
    .toBuffer();

  // Resize and softly feather the mask so the seam between subject and edited
  // background isn't a hard pixel cliff.
  const maskAlpha = await sharp(maskBuf)
    .resize(W, H, { fit: "fill" })
    .blur(feather > 0 ? feather : undefined)
    .toColourspace("b-w")
    .raw()
    .toBuffer();

  // Build an RGBA subject layer: original RGB + mask as alpha.
  const { data: origRaw, info: origInfo } = await sharp(originalBuf)
    .removeAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  if (origInfo.width !== W || origInfo.height !== H) {
    throw new Error("Unexpected original raw size");
  }
  const pixels = W * H;
  const rgba = Buffer.allocUnsafe(pixels * 4);
  for (let i = 0; i < pixels; i++) {
    rgba[i * 4] = origRaw[i * 3];
    rgba[i * 4 + 1] = origRaw[i * 3 + 1];
    rgba[i * 4 + 2] = origRaw[i * 3 + 2];
    rgba[i * 4 + 3] = maskAlpha[i];
  }
  const subjectLayer = await sharp(rgba, {
    raw: { width: W, height: H, channels: 4 },
  })
    .png()
    .toBuffer();

  // Composite subject (with alpha) over the edited background.
  return sharp(editedResized)
    .composite([{ input: subjectLayer, blend: "over" }])
    .jpeg({ quality: 92 })
    .toBuffer();
}
