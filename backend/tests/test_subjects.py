from httpx import AsyncClient

from tests.conftest import make_subject


async def test_create_and_list_subjects(client: AsyncClient) -> None:
    created = await make_subject(client, "English Language Arts")
    assert created["slug"] == "english-language-arts"

    response = await client.get("/api/v1/subjects")
    assert response.status_code == 200
    assert [s["name"] for s in response.json()] == ["English Language Arts"]


async def test_duplicate_subject_slug_conflicts(client: AsyncClient) -> None:
    await make_subject(client, "Science")
    response = await client.post("/api/v1/subjects", json={"name": "Science"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
