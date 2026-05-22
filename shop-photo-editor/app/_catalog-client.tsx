"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { ArrowRight, Search, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

interface ListedProduct {
  id: string;
  title: string;
  featuredImageUrl: string | null;
  imagesCount: number;
  totalInventory: number | null;
}

interface ListResponse {
  products: ListedProduct[];
  pageInfo: { hasNextPage: boolean; endCursor: string | null };
}

const SELECTION_KEY = "selectedProductIds";

function readSelection(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(SELECTION_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeSelection(ids: string[]) {
  window.sessionStorage.setItem(SELECTION_KEY, JSON.stringify(ids));
}

export default function CatalogClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialQ = searchParams?.get("q") ?? "";

  const [query, setQuery] = useState(initialQ);
  const [debouncedQ, setDebouncedQ] = useState(initialQ);
  const [products, setProducts] = useState<ListedProduct[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Lazy initializer hydrates from sessionStorage exactly once, on first render,
  // so user clicks afterwards can't be overwritten by a delayed effect.
  const [selected, setSelected] = useState<string[]>(() => readSelection());
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // debounce search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedQ(query);
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      if (query) params.set("q", query);
      else params.delete("q");
      router.replace(`/?${params.toString()}`);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  // Track current query so stale fetches don't write into a newer query's state.
  const queryRef = useRef("");
  const inflightRef = useRef(false);

  const load = useCallback(
    async (q: string, after: string | null) => {
      if (inflightRef.current) return;
      inflightRef.current = true;
      const isFirst = after === null;
      if (isFirst) setLoading(true);
      else setLoadingMore(true);
      setError(null);
      try {
        const sp = new URLSearchParams();
        if (q) sp.set("q", q);
        if (after) sp.set("cursor", after);
        const res = await fetch(`/api/shopify/products?${sp.toString()}`);
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(j.error || `HTTP ${res.status}`);
        }
        const data = (await res.json()) as ListResponse;
        // Drop the response if the query changed while we were fetching.
        if (queryRef.current !== q) return;
        setProducts((prev) => {
          const merged = isFirst ? data.products : [...prev, ...data.products];
          // Dedupe by id (defensive against any pagination overlap).
          const seen = new Set<string>();
          const out: ListedProduct[] = [];
          for (const p of merged) {
            if (seen.has(p.id)) continue;
            seen.add(p.id);
            out.push(p);
          }
          return out;
        });
        setCursor(data.pageInfo.endCursor);
        setHasNext(data.pageInfo.hasNextPage);
      } catch (e) {
        if (queryRef.current === q) {
          setError(e instanceof Error ? e.message : "Failed to load");
        }
      } finally {
        inflightRef.current = false;
        if (queryRef.current === q) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    []
  );

  // fetch on q change — reset pagination state synchronously so the
  // auto-load-more effect doesn't fire with the previous query's cursor.
  useEffect(() => {
    queryRef.current = debouncedQ;
    setCursor(null);
    setHasNext(false);
    setProducts([]);
    load(debouncedQ, null);
  }, [debouncedQ, load]);

  // auto-load remaining pages until we've fetched everything for this query
  useEffect(() => {
    if (loading || loadingMore) return;
    if (!hasNext || !cursor) return;
    if (inflightRef.current) return;
    load(debouncedQ, cursor);
  }, [hasNext, cursor, loading, loadingMore, debouncedQ, load]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      writeSelection(next);
      return next;
    });
  }, []);

  const selectedCount = selected.length;
  const showEmpty = !loading && !error && products.length === 0;

  const skeletons = useMemo(
    () =>
      Array.from({ length: 8 }).map((_, i) => (
        <Card key={i} className="overflow-hidden p-0">
          <Skeleton className="aspect-square w-full rounded-none" />
          <CardContent className="p-3 space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/3" />
          </CardContent>
        </Card>
      )),
    []
  );

  return (
    <div className="mx-auto w-full max-w-7xl px-6 py-8">
      <div className="flex flex-col gap-2 mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Catalog</h1>
        <p className="text-sm text-neutral-500">
          Pick products you want to restyle. Selection persists while you navigate.
        </p>
      </div>

      <div className="relative mb-6 max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-neutral-400" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search products by title, vendor, type..."
          className="pl-9"
        />
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700 mb-6 flex items-start gap-3">
          <AlertTriangle className="size-4 mt-0.5" />
          <div>
            <div className="font-medium">Couldn&apos;t reach Shopify.</div>
            <div className="text-rose-600/80">
              {error}. Double-check <code>SHOPIFY_STORE_DOMAIN</code> and{" "}
              <code>SHOPIFY_ADMIN_TOKEN</code> in <code>.env</code>, then refresh.
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
        {loading
          ? skeletons
          : products.map((p) => {
              const isSelected = selected.includes(p.id);
              return (
                <Card
                  key={p.id}
                  className={cn(
                    "overflow-hidden p-0 cursor-pointer transition-all",
                    isSelected && "ring-2 ring-neutral-900"
                  )}
                  onClick={() => toggle(p.id)}
                >
                  <div className="relative aspect-square bg-neutral-100">
                    {p.featuredImageUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={p.featuredImageUrl}
                        alt={p.title}
                        className="absolute inset-0 size-full object-cover"
                      />
                    ) : (
                      <div className="absolute inset-0 flex items-center justify-center text-xs text-neutral-400">
                        No image
                      </div>
                    )}
                    <div className="absolute top-2 right-2 pointer-events-none">
                      <Checkbox
                        checked={isSelected}
                        tabIndex={-1}
                        aria-hidden
                        className="bg-white/90 border-neutral-300"
                      />
                    </div>
                    <div className="absolute bottom-2 left-2 flex gap-1">
                      <Badge
                        variant="secondary"
                        className="bg-white/90 text-neutral-700"
                      >
                        {p.imagesCount} {p.imagesCount === 1 ? "image" : "images"}
                      </Badge>
                      {typeof p.totalInventory === "number" && p.totalInventory <= 0 ? (
                        <Badge className="bg-rose-50 text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50">
                          Out of stock
                        </Badge>
                      ) : null}
                    </div>
                  </div>
                  <CardContent className="p-3">
                    <div className="text-sm font-medium line-clamp-2">{p.title}</div>
                  </CardContent>
                </Card>
              );
            })}
      </div>

      {showEmpty && (
        <div className="rounded-lg border border-dashed border-neutral-300 p-10 text-center text-sm text-neutral-500">
          No products match{debouncedQ ? ` "${debouncedQ}"` : ""}. Try a different search.
        </div>
      )}

      {!loading && hasNext && (
        <div className="flex justify-center mt-8">
          <Button
            variant="outline"
            disabled={loadingMore}
            onClick={() => load(debouncedQ, cursor)}
          >
            {loadingMore ? "Loading..." : "Load more"}
          </Button>
        </div>
      )}

      {mounted && selectedCount > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-20 rounded-full bg-neutral-900 text-white shadow-lg flex items-center gap-2 pl-4 pr-2 py-2">
          <span className="text-sm">
            {selectedCount} {selectedCount === 1 ? "product" : "products"} selected
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSelected([]);
              writeSelection([]);
            }}
            className="rounded-full text-neutral-300 hover:text-white hover:bg-white/10"
          >
            Clear
          </Button>
          <Button asChild size="sm" className="rounded-full bg-white text-neutral-900 hover:bg-neutral-100">
            <Link href="/edit">
              Continue to Edit <ArrowRight className="size-4 ml-1" />
            </Link>
          </Button>
        </div>
      )}
    </div>
  );
}
