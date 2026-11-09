import Link from "next/link";
import type { Course } from "../types";
import { GradeMark } from "./grade-tile";

export function CourseCard({ course }: { course: Course }) {
  return (
    <Link
      href={`/courses/${course.slug}`}
      className="group flex h-full flex-col gap-4 rounded-lg border-2 border-ink bg-white p-5 shadow-sticker transition-[transform,box-shadow] hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-sticker-lg"
    >
      <div className="flex items-center gap-3">
        <GradeMark grade={course.grade_level} className="size-11 text-2xl" />
        <p className="text-sm font-bold text-ink-soft">
          <span className="sr-only">Grade {course.grade_level}, </span>
          {course.subject.name}
        </p>
      </div>
      <div>
        <h3 className="text-lg font-extrabold leading-snug group-hover:underline group-hover:underline-offset-4">
          {course.title}
        </h3>
        <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">{course.summary}</p>
      </div>
    </Link>
  );
}
