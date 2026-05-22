"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ImagePlus, Link2, Sparkles, Upload, X } from "lucide-react";
import { cn } from "@/lib/utils";

const MAX_REFS = 5;

type Mode = "house" | "listing" | "upload";

interface Props {
  referenceUrls: string[];
  onChange: (urls: string[]) => void;
}

interface ListingMedia {
  product: { id: string; title: string };
  images: { id: string; url: string; altText: string | null }[];
}

export function ReferencePicker({ referenceUrls, onChange }: Props) {
  const [houseStyle, setHouseStyle] = useState<string[]>([]);
  const [houseLoaded, setHouseLoaded] = useState(false);
  const [mode, setMode] = useState<Mode>("upload");
  const initRef = useRef(false);

  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const [listingRef, setListingRef] = useState("");
  const [listingMedia, setListingMedia] = useState<ListingMedia | null>(null);
  const [listingLoading, setListingLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  // Load house style and pick default tab.
  useEffect(() => {
    fetch("/api/settings")
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: { houseStyleImageUrls: string[] }) => {
        setHouseStyle(data.houseStyleImageUrls);
        setHouseLoaded(true);
        if (!initRef.current) {
          initRef.current = true;
          if (data.houseStyleImageUrls.length > 0) {
            setMode("house");
            onChange(data.houseStyleImageUrls);
          }
        }
      })
      .catch(() => setHouseLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setTab = (m: Mode) => {
    setMode(m);
    if (m === "house") onChange(houseStyle);
    else onChange([]);
  };

  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      const arr = Array.from(files);
      if (arr.length === 0) return;
      const remaining = MAX_REFS - referenceUrls.length;
      if (remaining <= 0) {
        toast.error(`Already at the ${MAX_REFS}-image limit.`);
        return;
      }
      const eligible = arr.slice(0, remaining);
      setUploading(true);
      try {
        const fd = new FormData();
        for (const f of eligible) fd.append("file", f);
        const res = await fetch("/api/upload-reference-multi", {
          method: "POST",
          body: fd,
        });
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(j.error || `HTTP ${res.status}`);
        }
        const data = (await res.json()) as { urls: string[] };
        onChange([...referenceUrls, ...data.urls].slice(0, MAX_REFS));
        toast.success(`Added ${data.urls.length} image${data.urls.length === 1 ? "" : "s"}.`);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [referenceUrls, onChange]
  );

  const loadListing = useCallback(async () => {
    const ref = listingRef.trim();
    if (!ref) return;
    setListingLoading(true);
    setListingMedia(null);
    setSelected(new Set());
    try {
      const sp = new URLSearchParams({ ref });
      const res = await fetch(`/api/shopify/listing-media?${sp.toString()}`);
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as ListingMedia;
      setListingMedia(data);
      if (data.images[0]) setSelected(new Set([data.images[0].url]));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load listing");
    } finally {
      setListingLoading(false);
    }
  }, [listingRef]);

  const importListing = useCallback(async () => {
    if (!listingMedia) return;
    const imageUrls = Array.from(selected).slice(0, MAX_REFS);
    if (imageUrls.length === 0) {
      toast.error("Pick at least one image.");
      return;
    }
    setImporting(true);
    try {
      const res = await fetch("/api/reference-from-listing-multi", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ref: listingRef.trim(), imageUrls }),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as { urls: string[] };
      onChange(data.urls.slice(0, MAX_REFS));
      toast.success(`Using ${data.urls.length} image${data.urls.length === 1 ? "" : "s"} as references.`);
      setListingMedia(null);
      setSelected(new Set());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to use listing");
    } finally {
      setImporting(false);
    }
  }, [listingMedia, listingRef, selected, onChange]);

  const removeAt = (idx: number) => {
    const next = [...referenceUrls];
    next.splice(idx, 1);
    onChange(next);
  };

  const removeAll = () => onChange([]);

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-neutral-700">Reference photos</h2>
        <div className="inline-flex rounded-md bg-neutral-100 p-0.5 text-xs font-medium">
          <button
            type="button"
            onClick={() => setTab("house")}
            disabled={!houseLoaded || houseStyle.length === 0}
            className={cn(
              "inline-flex items-center gap-1 rounded px-3 py-1 transition disabled:opacity-50",
              mode === "house"
                ? "bg-white text-neutral-900 shadow-sm"
                : "text-neutral-500 hover:text-neutral-900"
            )}
          >
            <Sparkles className="size-3.5" /> House style
          </button>
          <button
            type="button"
            onClick={() => setTab("listing")}
            className={cn(
              "inline-flex items-center gap-1 rounded px-3 py-1 transition",
              mode === "listing"
                ? "bg-white text-neutral-900 shadow-sm"
                : "text-neutral-500 hover:text-neutral-900"
            )}
          >
            <Link2 className="size-3.5" /> From listing
          </button>
          <button
            type="button"
            onClick={() => setTab("upload")}
            className={cn(
              "inline-flex items-center gap-1 rounded px-3 py-1 transition",
              mode === "upload"
                ? "bg-white text-neutral-900 shadow-sm"
                : "text-neutral-500 hover:text-neutral-900"
            )}
          >
            <Upload className="size-3.5" /> Upload
          </button>
        </div>
      </div>

      {mode === "house" && (
        <div className="rounded-lg border border-neutral-200 p-4 bg-white">
          {houseStyle.length === 0 ? (
            <div className="text-sm text-neutral-500">
              No house style set yet.{" "}
              <Link href="/settings" className="text-neutral-900 underline">
                Configure in Settings
              </Link>
              .
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <div className="flex gap-2 flex-wrap">
                {houseStyle.map((u, i) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={u}
                    src={u}
                    alt={`House style ${i + 1}`}
                    className="size-16 rounded-md object-cover border border-neutral-200"
                  />
                ))}
              </div>
              <div className="ml-auto text-right">
                <div className="text-sm font-medium">
                  {houseStyle.length} reference image{houseStyle.length === 1 ? "" : "s"} from your house style
                </div>
                <Link
                  href="/settings"
                  className="text-xs text-neutral-500 hover:text-neutral-900 underline"
                >
                  Manage in Settings →
                </Link>
              </div>
            </div>
          )}
        </div>
      )}

      {mode === "upload" && (
        <div>
          <div
            className={cn(
              "rounded-lg border-2 border-dashed p-6 transition-colors cursor-pointer bg-neutral-50/50",
              dragOver ? "border-neutral-900 bg-neutral-50" : "border-neutral-300"
            )}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
            }}
            onClick={() => fileRef.current?.click()}
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) uploadFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <div className="flex flex-col items-center gap-2 text-center text-sm text-neutral-500 py-4">
              <ImagePlus className="size-6 text-neutral-400" />
              <div>
                <span className="font-medium text-neutral-900">
                  {uploading ? "Uploading…" : "Click to upload"}
                </span>{" "}
                or drag and drop
              </div>
              <div className="text-xs text-neutral-400">
                Up to {MAX_REFS} images, JPEG/PNG, 10 MB each.
              </div>
            </div>
          </div>
          {referenceUrls.length > 0 && (
            <div className="mt-3 flex items-center gap-2 flex-wrap">
              {referenceUrls.map((u, i) => (
                <div
                  key={u}
                  className="relative size-20 rounded-md overflow-hidden border border-neutral-200 bg-neutral-100"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={u}
                    alt={`Ref ${i + 1}`}
                    className="absolute inset-0 size-full object-cover"
                  />
                  <button
                    type="button"
                    onClick={() => removeAt(i)}
                    aria-label="Remove"
                    className="absolute top-0.5 right-0.5 inline-flex items-center justify-center rounded bg-black/70 text-white size-5 hover:bg-black"
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={removeAll}
                className="text-xs text-neutral-500 hover:text-neutral-900 underline"
              >
                Remove all
              </button>
            </div>
          )}
        </div>
      )}

      {mode === "listing" && (
        <div className="rounded-lg border border-neutral-200 p-4 bg-white">
          <div className="flex gap-2 mb-3">
            <Input
              value={listingRef}
              onChange={(e) => setListingRef(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  loadListing();
                }
              }}
              placeholder="Paste a Shopify product URL, handle, or ID"
              className="flex-1"
            />
            <Button
              onClick={loadListing}
              disabled={!listingRef.trim() || listingLoading}
              size="sm"
            >
              {listingLoading ? "Loading…" : "Load listing"}
            </Button>
          </div>

          {listingMedia && (
            <div>
              <div className="text-xs font-medium text-neutral-600 mb-2">
                {listingMedia.product.title} — pick the images to use
              </div>
              <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
                {listingMedia.images.map((img) => {
                  const checked = selected.has(img.url);
                  return (
                    <button
                      key={img.id}
                      type="button"
                      onClick={() => {
                        setSelected((prev) => {
                          const next = new Set(prev);
                          if (next.has(img.url)) {
                            next.delete(img.url);
                          } else {
                            if (next.size >= MAX_REFS) {
                              toast.error(`Max ${MAX_REFS} references.`);
                              return prev;
                            }
                            next.add(img.url);
                          }
                          return next;
                        });
                      }}
                      className={cn(
                        "relative aspect-square rounded border overflow-hidden",
                        checked
                          ? "border-neutral-900 ring-2 ring-neutral-900"
                          : "border-neutral-200"
                      )}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={img.url}
                        alt={img.altText ?? ""}
                        className="absolute inset-0 size-full object-cover"
                      />
                      <div
                        aria-hidden
                        className={cn(
                          "absolute top-1 left-1 size-4 rounded-sm border flex items-center justify-center text-[10px] font-bold",
                          checked
                            ? "bg-neutral-900 text-white border-neutral-900"
                            : "bg-white/90 border-neutral-300 text-transparent"
                        )}
                      >
                        ✓
                      </div>
                    </button>
                  );
                })}
              </div>
              <div className="mt-3 flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={importListing}
                  disabled={importing || selected.size === 0}
                >
                  {importing ? "Importing…" : `Use selected (${selected.size})`}
                </Button>
                <div className="text-xs text-neutral-500">
                  Up to {MAX_REFS} images.
                </div>
              </div>
            </div>
          )}

          {referenceUrls.length > 0 && !listingMedia && (
            <div className="mt-3 flex items-center gap-2 flex-wrap">
              {referenceUrls.map((u, i) => (
                <div
                  key={u}
                  className="relative size-20 rounded-md overflow-hidden border border-neutral-200 bg-neutral-100"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={u}
                    alt={`Ref ${i + 1}`}
                    className="absolute inset-0 size-full object-cover"
                  />
                  <button
                    type="button"
                    onClick={() => removeAt(i)}
                    aria-label="Remove"
                    className="absolute top-0.5 right-0.5 inline-flex items-center justify-center rounded bg-black/70 text-white size-5 hover:bg-black"
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
