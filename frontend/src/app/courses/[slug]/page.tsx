import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";
import { getCourseBySlug } from "@/features/catalog/api";
import { GradeMark } from "@/features/catalog/components/grade-tile";
import { ApiError } from "@/lib/api-client";
import { formatDate } from "@/lib/format";

interface CoursePageProps {
  params: Promise<{ slug: string }>;
}

// Deduplicated per request, so generateMetadata and the page share one API call.
const loadCourse = cache(async (slug: string) => {
  try {
    return await getCourseBySlug(slug, { next: { revalidate: 60 } });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
});

export async function generateMetadata({ params }: CoursePageProps): Promise<Metadata> {
  const { slug } = await params;
  const course = await loadCourse(slug);
  return { title: course.title, description: course.summary };
}

export default async function CoursePage({ params }: CoursePageProps) {
  const { slug } = await params;
  const course = await loadCourse(slug);

  return (
    <article className="mx-auto max-w-6xl px-5 py-10 lg:py-14">
      <nav aria-label="Breadcrumb" className="mb-8 text-sm font-bold text-ink-soft">
        <Link href="/courses" className="underline underline-offset-4 hover:text-ink">
          Courses
        </Link>
        <span aria-hidden> / </span>
        <Link
          href={`/courses?grade=${course.grade_level}`}
          className="underline underline-offset-4 hover:text-ink"
        >
          Grade {course.grade_level}
        </Link>
      </nav>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div>
          <GradeMark grade={course.grade_level} className="mb-6 size-16 text-4xl" />
          <h1 className="text-4xl font-black leading-tight tracking-tight sm:text-5xl">{course.title}</h1>
          <p className="mt-4 max-w-2xl text-xl leading-relaxed text-ink-soft">{course.summary}</p>
          <div className="mt-8 max-w-2xl whitespace-pre-line leading-relaxed">
            {course.description ?? "The full course outline has not been published yet."}
          </div>
        </div>

        <aside className="h-fit rounded-lg border-2 border-ink bg-white p-5 shadow-sticker">
          <dl className="grid gap-4 text-sm">
            <div>
              <dt className="font-bold text-ink-soft">Subject</dt>
              <dd className="mt-0.5 text-base font-extrabold">{course.subject.name}</dd>
            </div>
            <div>
              <dt className="font-bold text-ink-soft">Grade</dt>
              <dd className="mt-0.5 text-base font-extrabold">{course.grade_level}</dd>
            </div>
            {course.published_at && (
              <div>
                <dt className="font-bold text-ink-soft">Published</dt>
                <dd className="mt-0.5 text-base font-extrabold">{formatDate(course.published_at)}</dd>
              </div>
            )}
          </dl>
        </aside>
      </div>
    </article>
  );
}
