import { Suspense } from "react";
import SettingsClient from "./_settings-client";

export const dynamic = "force-dynamic";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <SettingsClient />
    </Suspense>
  );
}
