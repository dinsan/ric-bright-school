import { ButtonLink } from "@/components/ui/button";
import { GradePicker } from "@/features/catalog/components/grade-picker";

export default function HomePage() {
  return (
    <section className="mx-auto grid max-w-6xl gap-12 px-5 py-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:items-center lg:gap-16 lg:py-24">
      <div>
        <h1 className="text-5xl font-black leading-[1.02] tracking-tight sm:text-6xl">
          Start with your grade.
        </h1>
        <p className="mt-5 max-w-md text-lg leading-relaxed text-ink-soft">
          Lumina has courses for every grade from 1 to 12. Pick a grade to see what you can learn, or browse
          everything by subject.
        </p>
        <div className="mt-8">
          <ButtonLink href="/courses" variant="primary">
            Browse all courses
          </ButtonLink>
        </div>
      </div>
      <GradePicker />
    </section>
  );
}
