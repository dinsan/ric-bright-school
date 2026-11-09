import Link from "next/link";
import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary";

const base =
  "inline-flex items-center justify-center rounded-md border-2 border-ink px-4 py-2.5 text-sm font-bold " +
  "shadow-sticker transition-[transform,box-shadow] hover:-translate-x-px hover:-translate-y-px " +
  "active:translate-x-0.5 active:translate-y-0.5 active:shadow-none " +
  "disabled:pointer-events-none disabled:opacity-50";

const variants: Record<Variant, string> = {
  primary: "bg-cobalt text-white",
  secondary: "bg-white text-ink",
};

interface VariantProps {
  variant?: Variant;
}

export function Button({
  variant = "secondary",
  className,
  type = "button",
  ...props
}: ComponentProps<"button"> & VariantProps) {
  return <button type={type} className={cn(base, variants[variant], className)} {...props} />;
}

export function ButtonLink({
  variant = "primary",
  className,
  ...props
}: ComponentProps<typeof Link> & VariantProps) {
  return <Link className={cn(base, variants[variant], className)} {...props} />;
}
