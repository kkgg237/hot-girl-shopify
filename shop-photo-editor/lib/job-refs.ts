import type { Job } from "@/db/schema";

export function jobReferenceUrls(job: Pick<Job, "referenceImageUrl" | "referenceImageUrls">): string[] {
  if (job.referenceImageUrls) {
    try {
      const parsed = JSON.parse(job.referenceImageUrls);
      if (Array.isArray(parsed)) {
        const urls = parsed.filter((x): x is string => typeof x === "string");
        if (urls.length > 0) return urls;
      }
    } catch {
      // fall through
    }
  }
  return job.referenceImageUrl ? [job.referenceImageUrl] : [];
}
