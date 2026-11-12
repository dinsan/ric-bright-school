from fastapi import APIRouter

from app.modules.audit.router import router as audit_router
from app.modules.catalog.router import router as catalog_router
from app.modules.classes.router import class_sign_in_router, classes_router
from app.modules.curriculum.router import router as curriculum_router
from app.modules.identity.router import admin_router, auth_router, me_router
from app.modules.media.router import router as media_router
from app.modules.standards.router import router as standards_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(class_sign_in_router)
api_router.include_router(me_router)
# The curriculum routes come first: /courses/import must not be read as a course id.
api_router.include_router(curriculum_router)
api_router.include_router(catalog_router)
api_router.include_router(media_router)
api_router.include_router(standards_router)
api_router.include_router(classes_router)
api_router.include_router(admin_router)
api_router.include_router(audit_router)
