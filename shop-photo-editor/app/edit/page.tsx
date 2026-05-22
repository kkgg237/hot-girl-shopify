"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ArrowLeft } from "lucide-react";
import { ReferencePicker } from "@/components/reference-picker";

interface ProductLite {
  id: string;
  title: string;
  featuredImageUrl: string | null;
}

const PLACEHOLDER =
  "Optional. e.g. 'Even out the wall lighting on the right side.' or 'Remove the small mark on the floor near the model's left foot.' Keep instructions about the BACKGROUND only — the product and model are never modified.";

export default function EditSetupPage() {
  const router = useRouter();
  const [ids, setIds] = useState<string[]>([]);
  const [products, setProducts] = useState<ProductLite[]>([]);
  const [loadingProducts, setLoadingProducts] = useState(true);
  const [referenceUrls, setReferenceUrls] = useState<string[]>([]);
  const [instructions, setInstructions] = useState("");
  const [extendCanvas, setExtendCanvas] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    try {
      const raw = window.sessionStorage.getItem("selectedProductIds");
      const parsed = raw ? (JSON.parse(raw) as unknown) : [];
      const arr = Array.isArray(parsed)
        ? parsed.filter((x): x is string => typeof x === "string")
        : [];
      setIds(arr);
      if (arr.length === 0) {
        setLoadingProducts(false);
        return;
      }
      const sp = new URLSearchParams({ ids: arr.join(",") });
      fetch(`/api/shopify/products-by-ids?${sp.toString()}`)
        .then(async (r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data: { products: ProductLite[] }) => setProducts(data.products))
        .catch((e: unknown) => {
          toast.error(
            e instanceof Error ? e.message : "Failed to load selected products"
          );
        })
        .finally(() => setLoadingProducts(false));
    } catch {
      setLoadingProducts(false);
    }
  }, []);

  const start = async () => {
    if (referenceUrls.length === 0 || ids.length === 0) return;
    setSubmitting(true);
    try {
      const res = await fetch("/api/edit/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          referenceUrls,
          instructions: instructions.trim(),
          productIds: ids,
          extendCanvas,
        }),
      });
      if (!res.ok) {
        const j = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as { jobId: string };
      router.push(`/review/${data.jobId}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start job");
      setSubmitting(false);
    }
  };

  const canSubmit =
    referenceUrls.length > 0 && ids.length > 0 && !submitting;

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-900 mb-4"
      >
        <ArrowLeft className="size-4" /> Back to catalog
      </Link>

      <h1 className="text-2xl font-semibold tracking-tight mb-1">Set up the edit</h1>
      <p className="text-sm text-neutral-500 mb-8">
        Pick reference photos and describe how to apply them to the selected products.
      </p>

      <section className="mb-8">
        <h2 className="text-sm font-semibold text-neutral-700 mb-3">
          Selected products ({ids.length})
        </h2>
        {loadingProducts ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="aspect-square rounded-md" />
            ))}
          </div>
        ) : ids.length === 0 ? (
          <div className="rounded-lg border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-500">
            No products selected.{" "}
            <Link href="/" className="text-neutral-900 underline">
              Pick some
            </Link>{" "}
            first.
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {products.map((p) => (
              <Card key={p.id} className="overflow-hidden p-0">
                <div className="relative aspect-square bg-neutral-100">
                  {p.featuredImageUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={p.featuredImageUrl}
                      alt={p.title}
                      className="absolute inset-0 size-full object-cover"
                    />
                  ) : null}
                </div>
                <CardContent className="p-2">
                  <div className="text-xs font-medium line-clamp-2">{p.title}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section className="mb-8">
        <ReferencePicker
          referenceUrls={referenceUrls}
          onChange={setReferenceUrls}
        />
      </section>

      <section className="mb-8">
        <h2 className="text-sm font-semibold text-neutral-700 mb-1">
          Instructions <span className="font-normal text-neutral-400">(optional)</span>
        </h2>
        <p className="text-xs text-neutral-500 mb-3">
          The product and model are <span className="font-medium">never</span> modified. Only the background is cleaned up: even lighting, remove dirt/marks, match the reference&apos;s background tone.
        </p>
        <Textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder={PLACEHOLDER}
          className="min-h-24"
        />
      </section>

      <section className="mb-8">
        <h2 className="text-sm font-semibold text-neutral-700 mb-3">Options</h2>
        <label className="flex items-start gap-3 rounded-lg border border-neutral-200 p-4 cursor-pointer hover:bg-neutral-50">
          <input
            type="checkbox"
            checked={extendCanvas}
            onChange={(e) => setExtendCanvas(e.target.checked)}
            className="mt-0.5 size-4 accent-neutral-900"
          />
          <div>
            <div className="text-sm font-medium">Extend canvas if subject is too tight in frame</div>
            <div className="text-xs text-neutral-500 mt-0.5">
              Adds white space around the subject when it&apos;s cropped close to the edges. Won&apos;t crop or zoom. Skipped automatically if the framing is already comfortable.
            </div>
          </div>
        </label>
      </section>

      <div className="flex justify-end">
        <Button onClick={start} disabled={!canSubmit} size="lg">
          {submitting ? "Starting..." : "Start editing"}
        </Button>
      </div>
    </div>
  );
}
