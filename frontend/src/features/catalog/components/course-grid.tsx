import { Skeleton } from "@/components/ui/skeleton";
import type { Course } from "../types";
import { CourseCard } from "./course-card";

const gridClass = "grid gap-5 sm:grid-cols-2 lg:grid-cols-3";

export function CourseGrid({ courses }: { courses: Course[] }) {
  return (
    <ul className={gridClass}>
      {courses.map((course) => (
        <li key={course.id}>
          <CourseCard course={course} />
        </li>
      ))}
    </ul>
  );
}

export function CourseGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className={gridClass} role="status" aria-label="Loading courses">
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} className="h-40 rounded-lg" />
      ))}
    </div>
  );
}
