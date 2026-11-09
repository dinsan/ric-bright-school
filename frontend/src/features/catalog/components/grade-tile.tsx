import { cn } from "@/lib/cn";
import { bandForGrade } from "../constants";

/** A grade number on its band colour. Decorative text; the grade is announced by the parent. */
export function GradeMark({ grade, className }: { grade: number; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex items-center justify-center rounded-md border-2 border-ink font-black leading-none",
        bandForGrade(grade).tile,
        className,
      )}
    >
      {grade}
    </span>
  );
}
