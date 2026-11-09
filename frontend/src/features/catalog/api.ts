import { apiFetch, type ApiFetchOptions } from "@/lib/api-client";
import { PAGE_SIZE } from "./constants";
import type { Course, CourseFilters, Page, Subject } from "./types";

type RequestOptions = Omit<ApiFetchOptions, "searchParams" | "json">;

export function listCourses(filters: CourseFilters, init?: RequestOptions): Promise<Page<Course>> {
  const page = filters.page ?? 1;
  return apiFetch<Page<Course>>("/api/v1/courses", {
    ...init,
    searchParams: {
      grade_level: filters.gradeLevel,
      subject: filters.subject,
      q: filters.q,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    },
  });
}

export function getCourseBySlug(slug: string, init?: RequestOptions): Promise<Course> {
  return apiFetch<Course>(`/api/v1/courses/slug/${encodeURIComponent(slug)}`, init);
}

export function listSubjects(init?: RequestOptions): Promise<Subject[]> {
  return apiFetch<Subject[]>("/api/v1/subjects", init);
}
