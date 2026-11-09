// Mirrors the backend's response schemas (backend/app/modules/catalog/schemas.py).
// When the API stabilises, generate these from /openapi.json instead of hand-maintaining them.

export type CourseStatus = "draft" | "published" | "archived";

export interface Subject {
  id: string;
  slug: string;
  name: string;
  description: string | null;
}

export interface Course {
  id: string;
  slug: string;
  title: string;
  summary: string;
  description: string | null;
  grade_level: number;
  status: CourseStatus;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  subject: Subject;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CourseFilters {
  gradeLevel?: number;
  subject?: string;
  q?: string;
  page?: number;
}
