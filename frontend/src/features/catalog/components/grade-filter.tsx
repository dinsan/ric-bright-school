"use client";

import { cn } from "@/lib/cn";
import { GRADES, bandForGrade } from "../constants";

interface GradeFilterProps {
  value: number | undefined;
  onChange: (grade: number | undefined) => void;
}

const chip =
  "flex h-10 min-w-10 items-center justify-center rounded-md border-2 border-ink px-3 text-sm font-bold transition-colors";

export function GradeFilter({ value, onChange }: GradeFilterProps) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-bold">Grade</legend>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          aria-pressed={value === undefined}
          onClick={() => onChange(undefined)}
          className={cn(chip, value === undefined ? "bg-ink text-white" : "bg-white hover:bg-ink/5")}
        >
          All
        </button>
        {GRADES.map((grade) => (
          <button
            key={grade}
            type="button"
            aria-pressed={value === grade}
            aria-label={`Grade ${grade}`}
            onClick={() => onChange(grade)}
            className={cn(chip, value === grade ? bandForGrade(grade).tile : "bg-white hover:bg-ink/5")}
          >
            {grade}
          </button>
        ))}
      </div>
    </fieldset>
  );
}
