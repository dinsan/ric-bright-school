import uuid

from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory, make_course, make_subject


async def test_create_and_list_subjects(client: AsyncClient, teacher: Account) -> None:
    created = await make_subject(client, "English Language Arts", headers=teacher.headers)
    assert created["slug"] == "english-language-arts"

    response = await client.get("/api/v1/subjects")  # listing is public
    assert response.status_code == 200
    assert [s["name"] for s in response.json()] == ["English Language Arts"]


async def test_duplicate_subject_slug_conflicts(client: AsyncClient, admin: Account) -> None:
    await make_subject(client, "Science", headers=admin.headers)
    response = await client.post(
        "/api/v1/subjects", json={"name": "Science"}, headers=admin.headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_create_subject_validation_error(client: AsyncClient, admin: Account) -> None:
    response = await client.post("/api/v1/subjects", json={"name": ""}, headers=admin.headers)
    assert response.status_code == 422


async def test_create_subject_requires_teacher_or_admin(
    client: AsyncClient, make_account: AccountFactory
) -> None:
    anonymous = await client.post("/api/v1/subjects", json={"name": "Art"})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthorized"

    parent = await make_account(Role.PARENT)
    forbidden = await client.post("/api/v1/subjects", json={"name": "Art"}, headers=parent.headers)
    assert forbidden.status_code == 403

    assert (await client.get("/api/v1/subjects")).json() == []


async def test_admin_deletes_unused_subject(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, "Music", headers=admin.headers)

    response = await client.delete(f"/api/v1/subjects/{subject['id']}", headers=admin.headers)
    assert response.status_code == 204
    assert (await client.get("/api/v1/subjects")).json() == []

    audit = await client.get(
        "/api/v1/admin/audit-events",
        params={"action": "catalog.subject_deleted"},
        headers=admin.headers,
    )
    assert audit.json()["items"][0]["entity_id"] == subject["id"]


async def test_delete_subject_with_courses_conflicts(client: AsyncClient, admin: Account) -> None:
    subject = await make_subject(client, headers=admin.headers)
    course = await make_course(client, subject["id"], headers=admin.headers)
    # Archived courses still reference the subject, so they still block the delete.
    await client.delete(f"/api/v1/courses/{course['id']}", headers=admin.headers)

    response = await client.delete(f"/api/v1/subjects/{subject['id']}", headers=admin.headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_delete_unknown_subject_is_404(client: AsyncClient, admin: Account) -> None:
    response = await client.delete(f"/api/v1/subjects/{uuid.uuid4()}", headers=admin.headers)
    assert response.status_code == 404


async def test_only_admins_delete_subjects(client: AsyncClient, teacher: Account) -> None:
    subject = await make_subject(client, headers=teacher.headers)
    url = f"/api/v1/subjects/{subject['id']}"

    assert (await client.delete(url)).status_code == 401
    assert (await client.delete(url, headers=teacher.headers)).status_code == 403
    assert len((await client.get("/api/v1/subjects")).json()) == 1
