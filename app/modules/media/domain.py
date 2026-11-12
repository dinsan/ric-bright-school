from enum import StrEnum

MB = 1024 * 1024


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    CAPTIONS = "captions"  # WebVTT subtitles for a video (accessibility)


class MediaStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"  # upload link issued, file not confirmed yet
    READY = "ready"  # uploaded, checked and scanned: usable in lessons
    REJECTED = "rejected"  # wrong size/type, or the scanner refused it


# Only these types are accepted. No SVG (it can carry scripts) and no HTML.
ALLOWED_TYPES: dict[MediaKind, frozenset[str]] = {
    MediaKind.IMAGE: frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"}),
    MediaKind.VIDEO: frozenset({"video/mp4", "video/webm"}),
    MediaKind.AUDIO: frozenset({"audio/mpeg", "audio/ogg", "audio/wav", "audio/mp4"}),
    MediaKind.DOCUMENT: frozenset(
        {
            "application/pdf",
            "text/plain",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    ),
    MediaKind.CAPTIONS: frozenset({"text/vtt"}),
}

MAX_BYTES: dict[MediaKind, int] = {
    MediaKind.IMAGE: 10 * MB,
    MediaKind.VIDEO: 1024 * MB,
    MediaKind.AUDIO: 100 * MB,
    MediaKind.DOCUMENT: 50 * MB,
    MediaKind.CAPTIONS: 1 * MB,
}
