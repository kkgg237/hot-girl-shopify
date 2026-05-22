import { mkdirSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const STORAGE_DIR = path.resolve(process.cwd(), "storage");
const PUBLIC_PREFIX = "/api/blob/";

function ensureDir() {
  mkdirSync(STORAGE_DIR, { recursive: true });
}

export async function saveBlob(buf: Buffer, ext: string): Promise<string> {
  ensureDir();
  const safeExt = ext.replace(/[^a-z0-9]/gi, "").toLowerCase() || "bin";
  const name = `${crypto.randomUUID()}.${safeExt}`;
  await writeFile(path.join(STORAGE_DIR, name), buf);
  return `${PUBLIC_PREFIX}${name}`;
}

function safeJoin(name: string): string {
  if (name.includes("..") || name.includes("/") || name.includes("\\")) {
    throw new Error("Invalid blob path");
  }
  return path.join(STORAGE_DIR, name);
}

export async function readBlobByPublicUrl(url: string): Promise<Buffer> {
  if (!url.startsWith(PUBLIC_PREFIX)) {
    throw new Error("Not a local blob url");
  }
  const name = url.slice(PUBLIC_PREFIX.length);
  return readBlobByFilename(name);
}

export async function readBlobByFilename(name: string): Promise<Buffer> {
  const full = safeJoin(name);
  return readFile(full);
}

export function blobStorageDir(): string {
  return STORAGE_DIR;
}
