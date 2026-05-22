"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { ImagePlus, Link2, Upload, X } from "lucide-react";
import { HouseStyleGrid } from "@/components/house-style-grid";
import { cn } from "@/lib/utils";

const MAX_TOTAL = 5;

interface ListingMedia {
  product: { id: string; title: string };
  images: { id: string; url: string; altText: string | null }[];
}

export default function SettingsClient() {
  const [urls, setUrls] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [panel, setPanel] = useState<null | "upload" | "listing">(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [listingRef, setListingRef] = useState("");
  const [listingMedia, setListingMedia] = useState<ListingMedia | null>(null);
  const [listingLoading, setListingLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/settings");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { houseStyleImageUrls: string[] };
      setUrls(data.houseStyleImageUrls);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const remaining = MAX_TOTAL - urls.length;

  const onUpload = useCallback(
    async (files: FileList | File[]) => {
      const arr = Array.from(files);
      if (arr.length === 0) return;
      const eligible = arr.slice(0, Math.max(0, remaining));
      if (eligible.length === 0) {
        toast.error(`House style is full (${MAX_TOTAL} images max).`);
        return;
      }
      setUploading(true);
      try {
        const fd = new FormData();
        for (const f of eligible) fd.append("file", f);
        const res = await fetch("/api/settings/house-style/add-upload", {
          method: "POST",
          body: fd,
        });
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(j.error || `HTTP ${res.status}`);
        }
        const data = (await res.json()) as { houseStyleImageUrls: string[] };
        setUrls(data.houseStyleImageUrls);
        toast.success(`Added ${eligible.length} image${eligible.length === 1 ? "" : "s"}.`);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [remaining]
  );

  const onRemove = useCallback(async (url: string) => {
    try {
      const res = await fetch("/api/settings/house-style/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as { houseStyleImageUrls: string[] };
      setUrls(data.houseStyleImageUrls);
      toast.success("Removed.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Remove failed");
    }
  }, []);

  const onReorder = useCallback(
    async (next: string[]) => {
      const prev = urls;
      setUrls(next);
      try {
        const res = await fetch("/api/settings/house-style/reorder", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ urls: next }),
        });
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(j.error || `HTTP ${res.status}`);
        }
      } catch (e) {
        setUrls(prev);
        toast.error(e instanceof Error ? e.message : "Reorder failed");
      }
    },
    [urls]
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
      // default: first image checked
      if (data.images[0]) setSelected(new Set([data.images[0].url]));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load listing");
    } finally {
      setListingLoading(false);
    }
  }, [listingRef]);

  const addSelected = useCallback(async () => {
    if (!listingMedia) return;
    const imageUrls = Array.from(selected);
    if (imageUrls.length === 0) {
      toast.error("Pick at least one image.");
      return;
    }
    setAdding(true);
    try {
      const res = await fetch("/api/settings/house-style/add-from-listing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ref: listingRef.trim(), imageUrls }),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as { houseStyleImageUrls: string[] };
      setUrls(data.houseStyleImageUrls);
      toast.success("Added from listing.");
      setListingMedia(null);
      setSelected(new Set());
      setListingRef("");
      setPanel(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to add");
    } finally {
      setAdding(false);
    }
  }, [listingMedia, listingRef, selected]);

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-semibold tracking-tight mb-1">Settings</h1>
      <p className="text-sm text-neutral-500 mb-8">
        Configure how every batch starts.
      </p>

      <Card>
        <CardContent className="p-5">
          <div className="mb-4">
            <h2 className="text-sm font-semibold text-neutral-900">House style</h2>
            <p className="text-xs text-neutral-500 mt-0.5">
              Up to 5 images. The first image is the strongest reference. Drag to reorder.
            </p>
          </div>

          {loading ? (
            <div className="text-sm text-neutral-500">Loading…</div>
          ) : urls.length === 0 ? (
            <div className="rounded-lg border border-dashed border-neutral-300 p-8 text-center">
              <ImagePlus className="size-6 text-neutral-400 mx-auto mb-2" />
              <div className="text-sm text-neutral-700 mb-1">
                No house style yet
              </div>
              <div className="text-xs text-neutral-500 mb-4">
                Add a few photos that capture the lighting and mood you want.
              </div>
              <div className="flex gap-2 justify-center">
                <Button onClick={() => setPanel("upload")} size="sm">
                  <Upload className="size-3.5" /> Upload images
                </Button>
                <Button
                  onClick={() => setPanel("listing")}
                  size="sm"
                  variant="outline"
                >
                  <Link2 className="size-3.5" /> From a listing
                </Button>
              </div>
            </div>
          ) : (
            <HouseStyleGrid urls={urls} onRemove={onRemove} onReorder={onReorder} />
          )}

          {urls.length > 0 && urls.length < MAX_TOTAL && (
            <div className="mt-4 flex gap-2">
              <Button
                onClick={() => setPanel(panel === "upload" ? null : "upload")}
                size="sm"
                variant="outline"
              >
                <Upload className="size-3.5" /> Add upload
              </Button>
              <Button
                onClick={() => setPanel(panel === "listing" ? null : "listing")}
                size="sm"
                variant="outline"
              >
                <Link2 className="size-3.5" /> Add from listing
              </Button>
              <div className="text-xs text-neutral-400 self-center ml-auto">
                {urls.length}/{MAX_TOTAL}
              </div>
            </div>
          )}

          {panel === "upload" && remaining > 0 && (
            <div className="mt-4 rounded-lg border border-neutral-200 p-4">
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) onUpload(e.target.files);
                  e.target.value = "";
                }}
              />
              <div className="flex items-center gap-3">
                <Button
                  size="sm"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                >
                  {uploading ? "Uploading…" : "Choose files"}
                </Button>
                <div className="text-xs text-neutral-500">
                  {remaining} slot{remaining === 1 ? "" : "s"} left. JPEG/PNG, up to 10 MB.
                </div>
                <button
                  type="button"
                  onClick={() => setPanel(null)}
                  className="ml-auto text-xs text-neutral-500 hover:text-neutral-900 inline-flex items-center gap-1"
                >
                  <X className="size-3" /> Close
                </button>
              </div>
            </div>
          )}

          {panel === "listing" && remaining > 0 && (
            <div className="mt-4 rounded-lg border border-neutral-200 p-4">
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
                  {listingLoading ? "Loading…" : "Load images"}
                </Button>
                <button
                  type="button"
                  onClick={() => {
                    setPanel(null);
                    setListingMedia(null);
                    setSelected(new Set());
                  }}
                  className="text-xs text-neutral-500 hover:text-neutral-900 inline-flex items-center gap-1"
                >
                  <X className="size-3" /> Close
                </button>
              </div>

              {listingMedia && (
                <div>
                  <div className="text-xs font-medium text-neutral-600 mb-2">
                    {listingMedia.product.title} — pick the images to import
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
                              if (next.has(img.url)) next.delete(img.url);
                              else next.add(img.url);
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
                          <div className="absolute top-1 left-1 bg-white/90 rounded p-0.5">
                            <Checkbox checked={checked} />
                          </div>
                        </button>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <Button
                      size="sm"
                      onClick={addSelected}
                      disabled={adding || selected.size === 0}
                    >
                      {adding ? "Adding…" : `Add selected (${selected.size})`}
                    </Button>
                    <div className="text-xs text-neutral-500">
                      {remaining} slot{remaining === 1 ? "" : "s"} left.
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
