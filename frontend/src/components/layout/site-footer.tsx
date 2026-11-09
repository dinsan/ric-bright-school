import { siteConfig } from "@/config/site";

export function SiteFooter() {
  return (
    <footer className="border-t-2 border-ink bg-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-1 px-5 py-6 text-sm text-ink-soft sm:flex-row sm:justify-between">
        <p>{siteConfig.description}</p>
        <p>&copy; {new Date().getFullYear()} {siteConfig.name}</p>
      </div>
    </footer>
  );
}
