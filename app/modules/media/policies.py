"""Who may upload and see media. Learners never list media: they get links through the
course content they are allowed to read."""

from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.identity.domain import Actor, Permission
from app.modules.media.models import MediaAsset


def ensure_can_upload(actor: Actor) -> None:
    if not actor.can(Permission.COURSES_AUTHOR):
        raise ForbiddenError("Only teachers and administrators can upload media.")


def is_media_admin(actor: Actor) -> bool:
    return actor.can(Permission.COURSES_MANAGE)


def ensure_owner(actor: Actor, asset: MediaAsset | None) -> MediaAsset:
    """Your own files only (admins: all). Someone else's file looks missing."""
    if asset is None or not (asset.owner_id == actor.user_id or is_media_admin(actor)):
        raise NotFoundError("Media not found.")
    return asset
