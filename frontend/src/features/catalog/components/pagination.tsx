import { Button } from "@/components/ui/button";

interface PaginationProps {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pageCount, onChange }: PaginationProps) {
  if (pageCount <= 1) return null;
  return (
    <nav aria-label="Pagination" className="mt-8 flex items-center justify-between gap-4">
      <Button disabled={page <= 1} onClick={() => onChange(page - 1)}>
        Previous page
      </Button>
      <p className="text-sm font-bold" aria-live="polite">
        Page {page} of {pageCount}
      </p>
      <Button disabled={page >= pageCount} onClick={() => onChange(page + 1)}>
        Next page
      </Button>
    </nav>
  );
}
