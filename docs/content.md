# Courses, lessons and content

How courses are built, published and read. Permissions are described in [auth.md](auth.md), error codes in [errors.md](errors.md). All paths are under `/api/v1`.

## Drafts and versions

A course has two sides:

- **The draft**: its units, lessons and their content. The author (or a platform admin) changes it at any time; nobody else sees it.
- **Published versions**: publishing freezes the draft into **version 1, 2, 3, …**, an immutable JSON snapshot (the database refuses to change or delete one). Learners only ever read versions.

```
 author edits the draft ──► POST /courses/{id}/publish ──► version N (immutable)
                                                              │
 learners read ◄── GET /courses/{id}/content[?version=N] ◄────┘
```

- Edits after publishing stay invisible until the next publish. `GET /courses/{id}/draft` shows `has_unpublished_changes`.
- Publishing an unchanged draft does not create a new version.
- `?version=N` reads an older version, so a learner who started version 1 can finish it while version 2 is current. (Pinning each learner's version automatically comes with enrolments, Phase 4.)
- `POST /courses/{id}/unpublish` takes the course out of the catalog and search; its versions are kept, and publishing again brings it back.
- `PATCH /courses/{id} {"status": "published"}` and creating a course with `"status": "published"` go through the same publishing (validation, new version, search).
- Course details (title, summary, grade, subject, tags, …) are edited directly on the course and show at once in the catalog; each version also keeps a copy of them as they were.

## Structure: units and lessons

A course is a list of **units**, each a list of **lessons**. Positions are `0, 1, 2, …` without gaps; the API keeps them that way.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/courses/{id}/draft` | The outline: units, lessons, positions, durations, standards, prerequisites |
| POST | `/courses/{id}/units` | `{title, summary?, position?}`: add a unit (default: at the end) |
| PATCH / DELETE | `/courses/{id}/units/{unit_id}` | Rename; delete the unit and its lessons (from the draft only) |
| PUT | `/courses/{id}/units/order` | `{ids: [...]}`: every unit exactly once, in the new order |
| POST | `/courses/{id}/units/{unit_id}/lessons` | `{title, summary?, position?, estimated_minutes?, difficulty?, content?}` |
| PUT | `/courses/{id}/units/{unit_id}/lessons/order` | `{ids: [...]}`: every lesson of the unit exactly once |
| POST | `/courses/{id}/lessons/{lesson_id}/move` | `{unit_id, position?}`: move within a unit or to another unit |
| GET | `/courses/{id}/draft/lessons/{lesson_id}` | A lesson with its blocks (and media previews) |
| PATCH | `/courses/{id}/lessons/{lesson_id}` | Title, summary, `estimated_minutes` (1–600), `difficulty` |
| PUT | `/courses/{id}/lessons/{lesson_id}/content` | `{content, expected_revision}`: save the blocks |
| DELETE | `/courses/{id}/lessons/{lesson_id}` | Delete a lesson from the draft |
| PUT | `/courses/{id}/lessons/{lesson_id}/standards` | `{standard_ids}`: curriculum standards it covers |
| PUT | `/courses/{id}/lessons/{lesson_id}/prerequisites` | `{lesson_ids}`: lessons of this course to finish first (no circles) |

Structure changes answer with the whole refreshed outline, so an editor never has to guess positions.

**Two people editing the same lesson.** Every lesson has a `revision` that goes up with each change. Send the revision you loaded as `expected_revision`; if someone saved in between, the API answers **409** instead of overwriting their work. Reload, re-apply, save again.

## Lesson content: blocks

A lesson's content is an ordered list of blocks:

```json
{
  "schema_version": 1,
  "blocks": [
    {"id": "6f1c…", "type": "text", "markdown": "## Fractions\nA fraction has a *numerator*."},
    {"id": "0b7a…", "type": "image", "media_id": "…", "caption": "Three of four slices"}
  ]
}
```

- `PUT …/content` replaces all blocks at once: adding, editing, removing and reordering blocks is one save.
- Every block has an `id`. Leave it out for new blocks and the server assigns one; keep it when you save again, so progress and comments can point at a block.
- Unknown block types, unknown fields and unsafe links are rejected (**422** `validation_error`). At most 200 blocks per lesson.
- `schema_version` lets the format evolve; older content is upgraded automatically when read.

| Type | Fields | Rules |
| --- | --- | --- |
| `text` | `markdown` (≤ 20,000), `format: "markdown"` | Plain markdown. **Render it with a sanitising renderer and no raw HTML** (the API stores it as written). |
| `image` | `media_id`, `alt?`, `caption?` | An uploaded image. `alt` overrides the image's own description for this lesson. |
| `video` | `title`, **either** `media_id` (upload) **or** `url` (YouTube / Vimeo only), `captions_media_id?`, `transcript?` | Needs captions or a transcript to be published. |
| `file` | `media_id`, `title` | A document or audio file to download. |
| `embed` | `url`, `title`, `height?` (100–2000, default 480) | https only, from an allowed site (`LUMINA_EMBED_ALLOWED_HOSTS`: YouTube, Vimeo, GeoGebra, PhET, Desmos, Scratch, Google Docs by default). `title` is read by screen readers. |
| `interactive` | `title`, `instructions?`, `activity` | `activity.widget` is one of: `flashcards` (`cards: [{front, back}]`), `sorting` (`categories: [{name, items}]`, 2–8), `matching` (`pairs: [{left, right}]`, 2–30), `simulation` (`url`, same allowed sites as embeds). |
| `quiz` | `quiz_id`, `title` | A reference to a quiz (the question bank arrives in Phase 5). |

When content is read (draft preview or published), blocks that show media get a `media` object (and videos a `captions` object) with a short-lived signed `url`, `kind`, `content_type`, `filename`, `status` and `alt_text`.

**For the web app**: show embeds in an `<iframe sandbox="allow-scripts allow-same-origin">` with the block's `title`, and never insert markdown as HTML without sanitising it.

## Media uploads

Files go straight from the browser to the storage (S3-compatible; MinIO locally), never through the API:

1. `POST /media {kind, filename, content_type, size_bytes, alt_text?, decorative?}` → a signed upload form.
2. The browser POSTs the file to `upload.url` as `multipart/form-data`: every `upload.fields` entry, then `file` last. The form expires after 15 minutes; the storage refuses larger files and other types.
3. `POST /media/{id}/complete`: the API checks what actually arrived (size within the limit and equal to the declared size, exact type), runs the virus-scan hook, and marks it `ready` (or `rejected`, with a `rejection_reason`). If the storage cannot be reached, the answer is `503 service_unavailable` and the upload stays pending: confirm again later.

```bash
curl -s -X POST http://localhost:8000/api/v1/media -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"image","filename":"pie.png","content_type":"image/png","size_bytes":48213,"alt_text":"A pie cut in quarters"}'
# then: curl -F key=… -F policy=… (every field) -F file=@pie.png <upload.url>
# then: POST /api/v1/media/{id}/complete
```

| Kind | Types | Max size |
| --- | --- | --- |
| `image` | PNG, JPEG, GIF, WebP (no SVG: it can carry scripts) | 10 MB |
| `video` | MP4, WebM | 1 GB |
| `audio` | MP3, Ogg, WAV, M4A | 100 MB |
| `document` | PDF, plain text, Word, PowerPoint, Excel (.docx/.pptx/.xlsx) | 50 MB |
| `captions` | WebVTT (`text/vtt`) | 1 MB |

Images need a description (`alt_text`) unless they are `decorative`; it can be changed later (`PATCH /media/{id}`). `GET /media` lists your uploads. Only teachers and admins upload; uploads are private to their owner (and admins).

## Publishing checks

`POST /courses/{id}/publish {change_note?}` checks the whole draft first and answers **422** with every problem in `details` (`field` = `lessons.{lesson_id}.blocks.{index}`):

- every media file exists, has finished uploading (`ready`) and is of the right kind;
- every image has a description (its own alt text, the block's `alt`, or it is decorative);
- every video has captions (`captions_media_id`) or a transcript;
- every embed and simulation is on the allowed list.

Only verified teachers and platform admins can publish (see [auth.md](auth.md)).

## Copying, exporting, importing

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/courses/{id}/duplicate` | `{title?, slug?}`: a new draft copy (units, lessons, content, standards, lesson prerequisites). Authors copy their own draft; any teacher can copy a published course (its published version) as a starting point. The copy belongs to whoever made it. |
| GET | `/courses/{id}/export?source=published\|draft` | The course as one JSON document (`"format": "lumina-course"`, `"format_version": 1`): the same shape as a published version. `draft` is for authors. |
| POST | `/courses/import` | `{document, slug?}`: a new draft course. The subject is matched by slug; standards by framework and code (unknown ones are skipped and reported in `warnings`); media must be uploads you can use on this server. |

## Standards, tags, difficulty, duration, prerequisites

- **Standards** (Common Core, NGSS, national curricula, …): `GET /standards/frameworks?country=US` and `GET /standards?framework=CCSS-MATH&grade_level=4&q=fraction`. Platform admins add frameworks (`POST /standards/frameworks`) and import their standards (`PUT /standards/frameworks/{code}/standards`, safe to repeat with a new release). Lessons link to them; published versions show the codes.
- **Tags**: up to 10 per course, lower case (`"Grade 4"` becomes `"grade 4"`), filter with `GET /courses?tag=fractions`.
- **Difficulty**: `beginner`, `intermediate`, `advanced`, on courses and lessons; filter with `?difficulty=`.
- **Language**: each course has a `language` (`en`, `es`, `fr-CA`, …); filter with `?language=`. It also decides how the course is searched.
- **Duration**: lessons have `estimated_minutes`; the course's `estimated_minutes` is their total, updated when a version is published.
- **Prerequisites**: between courses (`PUT /courses/{id}/prerequisites {course_ids}`) and between lessons of a course. Circles (A needs B needs A) are refused.

## Search

`GET /search?q=…` searches published courses and lessons (titles, summaries, and the words inside lessons: text, captions of images, transcripts, activities):

- Words are matched by their root in the course's language: `fraction` finds "fractions" in English courses, `gato` finds "gatos" in Spanish ones (other languages are matched word for word).
- Syntax: `"exact phrase"`, `or`, `-excluded`.
- Title matches rank above summary matches, which rank above body text.
- Filters: `grade_level`, `subject` (slug), `kind` (`course` or `lesson`). Results carry a plain-text `snippet` around the match.
- Drafts are never searchable. Publishing, unpublishing, archiving and editing a published course's details update the index at once. `make reindex` rebuilds it from the published versions.

## Learning paths and recommendations

- **Learning paths** are ordered sets of courses for a grade (and optionally a subject), curated by platform admins: `GET /learning-paths?grade_level=4&subject=mathematics`, `GET /learning-paths/{slug}`, and for admins `POST`, `PATCH` (`course_ids` replaces the order), `DELETE`. Unpublished paths, and draft courses inside a path, are only shown to admins.
- **Recommended courses**: `GET /courses/recommended?grade_level=4[&subject=…]` returns published courses of that grade, those in published learning paths first (in path order), then the rest. A signed-in student can leave out `grade_level` to use their own grade.

## Not built yet

| Item | Where it is tracked |
| --- | --- |
| Video processing (HLS streaming, thumbnails, automatic captions) through a managed service | BE-MEDIA-03 |
| Image sizes/variants for fast loading | BE-MEDIA-04 (partly: CDN URLs and accessibility checks exist) |
| Translations of course content | BE-CAT-09 |
| IMS Common Cartridge import/export (JSON is supported) | BE-CAT-10 |
| Each learner automatically staying on the version they started | Enrolments, BE-LRN-01 |
