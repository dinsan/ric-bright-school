"""Import every module's models here so Alembic's autogenerate sees the full metadata."""

from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.classes import models as classes_models  # noqa: F401
from app.modules.curriculum import models as curriculum_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.media import models as media_models  # noqa: F401
from app.modules.standards import models as standards_models  # noqa: F401
