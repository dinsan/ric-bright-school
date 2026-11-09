// NEXT_PUBLIC_* values are inlined into the browser bundle at build time, so each
// must be referenced literally (no dynamic lookups) for Next.js to replace it.
const publicApiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const env = {
  /** Base URL the browser uses to reach the API. */
  publicApiUrl,
  /** Base URL server-side code uses (may be an internal network address). */
  serverApiUrl: process.env.API_URL ?? publicApiUrl,
} as const;
