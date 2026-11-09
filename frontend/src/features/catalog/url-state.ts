import { MAX_GRADE, MIN_GRADE } from "./constants";

/** Catalog filters as they live in the URL, so every view is shareable and back-button safe. */
export interface CatalogUrlState {
  grade?: number;
  subject?: string;
  q?: string;
  page: number;
}

interface ParamReader {
  get(name: string): string | null;
}

export function parseCatalogParams(params: ParamReader): CatalogUrlState {
  const grade = Number(params.get("grade"));
  const page = Number(params.get("page"));

  return {
    grade: Number.isInteger(grade) && grade >= MIN_GRADE && grade <= MAX_GRADE ? grade : undefined,
    subject: params.get("subject") || undefined,
    q: params.get("q") || undefined,
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** Returns "" or a string starting with "?" — ready to append to a pathname. */
export function buildCatalogQuery(state: CatalogUrlState): string {
  const params = new URLSearchParams();
  if (state.grade !== undefined) params.set("grade", String(state.grade));
  if (state.subject) params.set("subject", state.subject);
  if (state.q) params.set("q", state.q);
  if (state.page > 1) params.set("page", String(state.page));
  const query = params.toString();
  return query ? `?${query}` : "";
}
