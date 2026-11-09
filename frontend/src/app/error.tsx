"use client";

import { Button } from "@/components/ui/button";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto max-w-6xl px-5 py-24">
      <h1 className="text-4xl font-black tracking-tight">This page did not load</h1>
      <p className="mt-3 max-w-md text-ink-soft">
        Something went wrong on our side. Try again, and if it keeps happening, come back in a few minutes.
      </p>
      <Button variant="primary" className="mt-8" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
