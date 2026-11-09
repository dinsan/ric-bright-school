"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useDebouncedCallback } from "@/hooks/use-debounced-callback";
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/cn";
import { pluralize } from "@/lib/format";
import { PAGE_SIZE } from "../constants";
import { useCourses, useSubjects } from "../hooks";
import { buildCatalogQuery, parseCatalogParams, type CatalogUrlState } from "../url-state";
import { CourseGrid, CourseGridSkeleton } from "./course-grid";
import { GradeFilter } from "./grade-filter";
import { Pagination } from "./pagination";

const fieldClass =
  "h-11 w-full rounded-md border-2 border-ink bg-white px-3 text-sm font-medium placeholder:text-ink-soft";

export function CoursesBrowser() {
  const router = useRouter();
  const pathname = usePathname();
  const state = parseCatalogParams(useSearchParams());

  const navigate = (patch: Partial<CatalogUrlState>) => {
    const next: CatalogUrlState = { ...state, page: 1, ...patch };
    router.replace(`${pathname}${buildCatalogQuery(next)}`, { scroll: false });
  };

  const subjects = useSubjects();
  const courses = useCourses({
    gradeLevel: state.grade,
    subject: state.subject,
    q: state.q,
    page: state.page,
  });

  const onSearch = useDebouncedCallback((value: string) => {
    navigate({ q: value.trim() || undefined });
  }, 300);

  const hasFilters = state.grade !== undefined || Boolean(state.subject) || Boolean(state.q);
  const pageCount = courses.data ? Math.ceil(courses.data.total / PAGE_SIZE) : 0;

  return (
    <div className="grid gap-8">
      <div className="grid gap-5 rounded-lg border-2 border-ink bg-white p-5 shadow-sticker">
        <GradeFilter value={state.grade} onChange={(grade) => navigate({ grade })} />
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="grid gap-2 text-sm font-bold">
            Subject
            <select
              className={fieldClass}
              value={state.subject ?? ""}
              onChange={(event) => navigate({ subject: event.target.value || undefined })}
            >
              <option value="">All subjects</option>
              {subjects.data?.map((subject) => (
                <option key={subject.id} value={subject.slug}>
                  {subject.name}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm font-bold">
            Search by title
            <input
              type="search"
              className={fieldClass}
              placeholder="For example, fractions"
              defaultValue={state.q ?? ""}
              onChange={(event) => onSearch(event.target.value)}
            />
          </label>
        </div>
      </div>

      <section aria-label="Results" className={cn("transition-opacity", courses.isPlaceholderData && "opacity-60")}>
        {courses.isPending ? (
          <CourseGridSkeleton />
        ) : courses.isError ? (
          <LoadError error={courses.error} onRetry={() => courses.refetch()} />
        ) : courses.data.items.length === 0 ? (
          <EmptyResults
            hasFilters={hasFilters}
            onClear={() => router.replace(pathname, { scroll: false })}
          />
        ) : (
          <>
            <p className="mb-4 text-sm font-bold" aria-live="polite">
              {pluralize(courses.data.total, "course")}
            </p>
            <CourseGrid courses={courses.data.items} />
            <Pagination page={state.page} pageCount={pageCount} onChange={(page) => navigate({ page })} />
          </>
        )}
      </section>
    </div>
  );
}

function LoadError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const unreachable = !(error instanceof ApiError);
  return (
    <div role="alert" className="rounded-lg border-2 border-error bg-white p-6">
      <h2 className="text-lg font-extrabold">Courses did not load</h2>
      <p className="mt-1 text-sm text-ink-soft">
        {unreachable
          ? "The course service could not be reached. Check your connection and try again."
          : error.message}
      </p>
      <Button className="mt-4" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

function EmptyResults({ hasFilters, onClear }: { hasFilters: boolean; onClear: () => void }) {
  return (
    <div className="rounded-lg border-2 border-dashed border-ink bg-white p-8 text-center">
      <h2 className="text-lg font-extrabold">
        {hasFilters ? "No courses match these filters" : "No courses yet"}
      </h2>
      {hasFilters && (
        <>
          <p className="mt-1 text-sm text-ink-soft">Try another grade or subject, or search for a different title.</p>
          <Button className="mt-4" onClick={onClear}>
            Clear filters
          </Button>
        </>
      )}
    </div>
  );
}
