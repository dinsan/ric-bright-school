import { ButtonLink } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-6xl px-5 py-24">
      <h1 className="text-4xl font-black tracking-tight">We can&apos;t find that page</h1>
      <p className="mt-3 max-w-md text-ink-soft">
        The link may be out of date, or the course may have been unpublished.
      </p>
      <ButtonLink href="/courses" className="mt-8">
        Browse courses
      </ButtonLink>
    </div>
  );
}
