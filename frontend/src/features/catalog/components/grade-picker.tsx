import Link from "next/link";
import { cn } from "@/lib/cn";
import { GRADES, GRADE_BANDS, bandForGrade } from "../constants";

/** Landing-page hero element: pick a grade, land on that grade's courses. */
export function GradePicker() {
  return (
    <div>
      <nav aria-label="Choose a grade">
        <ul className="grid grid-cols-4 gap-3 sm:gap-4">
          {GRADES.map((grade) => (
            <li key={grade}>
              <Link
                href={`/courses?grade=${grade}`}
                className={cn(
                  "flex aspect-square items-center justify-center rounded-lg border-2 border-ink text-5xl font-black shadow-sticker",
                  "transition-[transform,box-shadow] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-sticker-lg",
                  bandForGrade(grade).tile,
                )}
              >
                <span className="sr-only">Grade </span>
                {grade}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <ul className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-sm text-ink-soft">
        {GRADE_BANDS.map((band) => (
          <li key={band.id} className="flex items-center gap-2">
            <span aria-hidden className={cn("size-3.5 rounded-sm border-2 border-ink", band.swatch)} />
            {band.label}, Grades {band.from} to {band.to}
          </li>
        ))}
      </ul>
    </div>
  );
}
