"""Lesson content: an ordered list of typed blocks, stored as JSON (BE-CAT-02).

    {"schema_version": 1, "blocks": [{"id": "...", "type": "text", ...}, ...]}

Every write is validated here. Each block has a stable ``id`` (progress tracking and
comments can point at it). ``schema_version`` lets the format evolve: stored content is
upgraded on read by ``upgrade_content``.

Block types:
    text         markdown (rendered by the client with a sanitising renderer, no raw HTML)
    image        an uploaded image, with alt text unless decorative
    video        an uploaded video or a YouTube/Vimeo link, with captions or a transcript
    file         a downloadable document
    embed        a page from an allowed site (GeoGebra, Desmos, PhET, ...), with a title
    interactive  built-in activities: flashcards, sorting, matching, or a simulation
    quiz         a reference to a quiz (quizzes arrive in Phase 5)
"""

import uuid
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

CURRENT_SCHEMA_VERSION = 1
MAX_BLOCKS = 200
VIDEO_HOSTS = frozenset(
    {
        "www.youtube.com",
        "youtube.com",
        "youtu.be",
        "www.youtube-nocookie.com",
        "vimeo.com",
        "player.vimeo.com",
    }
)


def empty_content() -> dict[str, Any]:
    return {"schema_version": CURRENT_SCHEMA_VERSION, "blocks": []}


def _https(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only https links are allowed.")
    return url


HttpsUrl = Annotated[str, Field(max_length=2000)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)


class TextBlock(_Block):
    type: Literal["text"]
    format: Literal["markdown"] = "markdown"
    markdown: str = Field(min_length=1, max_length=20_000)


class ImageBlock(_Block):
    type: Literal["image"]
    media_id: uuid.UUID
    # Overrides the image's own alt text for this use; a lesson-specific description.
    alt: str | None = Field(default=None, max_length=500)
    caption: str | None = Field(default=None, max_length=500)


class VideoBlock(_Block):
    type: Literal["video"]
    title: ShortText
    media_id: uuid.UUID | None = None  # an uploaded video
    url: HttpsUrl | None = None  # or a YouTube / Vimeo link
    captions_media_id: uuid.UUID | None = None  # WebVTT captions (uploaded)
    transcript: str | None = Field(default=None, max_length=50_000)

    @field_validator("url")
    @classmethod
    def _video_host(cls, value: str | None) -> str | None:
        if value is None:
            return value
        _https(value)
        if urlparse(value).hostname not in VIDEO_HOSTS:
            raise ValueError("Video links must be YouTube or Vimeo; upload other videos.")
        return value

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.media_id is None) == (self.url is None):
            raise ValueError("A video needs exactly one of `media_id` or `url`.")
        return self


class FileBlock(_Block):
    type: Literal["file"]
    media_id: uuid.UUID
    title: ShortText


class EmbedBlock(_Block):
    type: Literal["embed"]
    url: HttpsUrl  # the allowed sites are checked by the service (settings)
    title: ShortText = Field(description="Read by screen readers: what the frame shows.")
    height: int = Field(default=480, ge=100, le=2000)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _https(value)


# --- interactive activities ------------------------------------------------------------


class Flashcard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    front: str = Field(min_length=1, max_length=500)
    back: str = Field(min_length=1, max_length=500)


class FlashcardsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget: Literal["flashcards"]
    cards: list[Flashcard] = Field(min_length=1, max_length=100)


class SortingCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    items: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=30
    )


class SortingConfig(BaseModel):
    """Drag each item into its category."""

    model_config = ConfigDict(extra="forbid")
    widget: Literal["sorting"]
    categories: list[SortingCategory] = Field(min_length=2, max_length=8)


class MatchingPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: str = Field(min_length=1, max_length=200)
    right: str = Field(min_length=1, max_length=200)


class MatchingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget: Literal["matching"]
    pairs: list[MatchingPair] = Field(min_length=2, max_length=30)


class SimulationConfig(BaseModel):
    """An external simulation (PhET, GeoGebra, ...): same allowed sites as embeds."""

    model_config = ConfigDict(extra="forbid")
    widget: Literal["simulation"]
    url: HttpsUrl

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _https(value)


InteractiveConfig = Annotated[
    FlashcardsConfig | SortingConfig | MatchingConfig | SimulationConfig,
    Field(discriminator="widget"),
]


class InteractiveBlock(_Block):
    type: Literal["interactive"]
    title: ShortText
    instructions: str | None = Field(default=None, max_length=1000)
    activity: InteractiveConfig


class QuizBlock(_Block):
    type: Literal["quiz"]
    quiz_id: uuid.UUID  # checked against the question bank once quizzes exist (Phase 5)
    title: ShortText


Block = Annotated[
    TextBlock | ImageBlock | VideoBlock | FileBlock | EmbedBlock | InteractiveBlock | QuizBlock,
    Field(discriminator="type"),
]


class LessonContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    blocks: list[Block] = Field(default_factory=list, max_length=MAX_BLOCKS)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [b.id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("Block ids must be unique within a lesson.")
        return self

    def media_ids(self) -> set[uuid.UUID]:
        found: set[uuid.UUID] = set()
        for block in self.blocks:
            for attr in ("media_id", "captions_media_id"):
                value = getattr(block, attr, None)
                if value is not None:
                    found.add(value)
        return found

    def external_urls(self) -> list[str]:
        """Links that must be on the embed allowlist (embeds and simulations)."""
        urls: list[str] = []
        for block in self.blocks:
            if isinstance(block, EmbedBlock):
                urls.append(block.url)
            elif isinstance(block, InteractiveBlock) and isinstance(
                block.activity, SimulationConfig
            ):
                urls.append(block.activity.url)
        return urls

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


_content_adapter = TypeAdapter(LessonContent)

# schema_version -> function upgrading stored content from that version to the next.
# Add an entry whenever the format changes; old rows are upgraded when read.
_UPGRADES: dict[int, Any] = {}


def upgrade_content(raw: dict[str, Any]) -> dict[str, Any]:
    """Bring stored content up to CURRENT_SCHEMA_VERSION."""
    version = int(raw.get("schema_version", 1))
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"Content schema_version {version} is newer than this server.")
    while version < CURRENT_SCHEMA_VERSION:
        raw = _UPGRADES[version](raw)
        version += 1
    return raw


def parse_content(raw: dict[str, Any]) -> LessonContent:
    return _content_adapter.validate_python(upgrade_content(raw))


def plain_text(content: LessonContent) -> str:
    """What search indexes for a lesson: its words, not its markup or URLs."""
    parts: list[str] = []
    for block in content.blocks:
        if isinstance(block, TextBlock):
            parts.append(block.markdown)
        elif isinstance(block, ImageBlock):
            parts.extend(p for p in (block.alt, block.caption) if p)
        elif isinstance(block, VideoBlock):
            parts.extend(p for p in (block.title, block.transcript) if p)
        elif isinstance(block, (FileBlock, EmbedBlock, QuizBlock)):
            parts.append(block.title)
        elif isinstance(block, InteractiveBlock):
            parts.extend(p for p in (block.title, block.instructions) if p)
            activity = block.activity
            if isinstance(activity, FlashcardsConfig):
                parts.extend(f"{c.front} {c.back}" for c in activity.cards)
            elif isinstance(activity, MatchingConfig):
                parts.extend(f"{p.left} {p.right}" for p in activity.pairs)
            elif isinstance(activity, SortingConfig):
                parts.extend(f"{c.name} {' '.join(c.items)}" for c in activity.categories)
    return "\n".join(parts)
