import Link from "next/link";
import { siteConfig } from "@/config/site";

export function SiteHeader() {
  return (
    <header className="border-b-2 border-ink bg-paper/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
        <Link href="/" className="flex items-center gap-2.5 text-xl font-black tracking-tight">
          <svg aria-hidden viewBox="0 0 32 32" className="size-8">
            <rect x="2" y="2" width="28" height="28" rx="7" fill="#2547f5" stroke="#0e1a3b" strokeWidth="2" />
            <circle cx="16" cy="16" r="5.5" fill="#ffd23f" />
          </svg>
          {siteConfig.name}
        </Link>
        <nav aria-label="Main">
          <ul className="flex items-center gap-6 text-sm font-bold">
            {siteConfig.nav.map((item) => (
              <li key={item.href}>
                <Link href={item.href} className="underline-offset-4 hover:underline">
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
