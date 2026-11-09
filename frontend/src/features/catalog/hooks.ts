"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { listCourses, listSubjects } from "./api";
import type { CourseFilters } from "./types";

export const catalogKeys = {
  all: ["catalog"] as const,
  courses: (filters: CourseFilters) => [...catalogKeys.all, "courses", filters] as const,
  subjects: () => [...catalogKeys.all, "subjects"] as const,
};

export function useCourses(filters: CourseFilters) {
  return useQuery({
    queryKey: catalogKeys.courses(filters),
    queryFn: ({ signal }) => listCourses(filters, { signal }),
    placeholderData: keepPreviousData,
  });
}

export function useSubjects() {
  return useQuery({
    queryKey: catalogKeys.subjects(),
    queryFn: ({ signal }) => listSubjects({ signal }),
    staleTime: 5 * 60_000,
  });
}
