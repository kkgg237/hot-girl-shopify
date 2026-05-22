import { Suspense } from "react";
import CatalogClient from "./_catalog-client";

export const dynamic = "force-dynamic";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <CatalogClient />
    </Suspense>
  );
}
