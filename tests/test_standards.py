"""Curriculum standards (BE-CAT-05): frameworks per country, standards, lesson links."""

from typing import Any

from httpx import AsyncClient

from app.modules.identity.domain import Role
from tests.conftest import Account, AccountFactory
from tests.test_publishing import course_with_lesson, publish

CCSS = {"code": "ccss-math", "name": "Common Core Math", "country_code": "us"}
STANDARDS = [
    {
        "code": "4.NF.A.1",
        "description": "Explain why fractions are equivalent.",
        "grade_min": 4,
        "grade_max": 4,
    },
    {"code": "4.NF.A.2", "description": "Compare two fractions.", "grade_min": 4, "grade_max": 4},
    {
        "code": "6.EE.A.1",
        "description": "Write expressions with exponents.",
        "grade_min": 6,
        "grade_max": 8,
    },
]


async def setup_ccss(client: AsyncClient, admin: Account) -> None:
    created = await client.post("/api/v1/standards/frameworks", json=CCSS, headers=admin.headers)
    assert created.status_code == 201, created.text
    imported = await client.put(
        "/api/v1/standards/frameworks/CCSS-MATH/standards",
        json={"standards": STANDARDS},
        headers=admin.headers,
    )
    assert imported.json() == {"created": 3, "updated": 0}


async def test_frameworks_by_country(client: AsyncClient, admin: Account) -> None:
    await setup_ccss(client, admin)
    uk = {"code": "NC-MATHS", "name": "National Curriculum Maths", "country_code": "GB"}
    assert (
        await client.post("/api/v1/standards/frameworks", json=uk, headers=admin.headers)
    ).status_code == 201

    framework = (await client.get("/api/v1/standards/frameworks", params={"country": "us"})).json()
    assert [(f["code"], f["country_code"]) for f in framework] == [
        ("CCSS-MATH", "US")
    ]  # normalised
    assert len((await client.get("/api/v1/standards/frameworks")).json()) == 2

    duplicate = await client.post("/api/v1/standards/frameworks", json=CCSS, headers=admin.headers)
    assert duplicate.status_code == 409
    for bad in ({**uk, "code": "X Y"}, {**uk, "country_code": "GBR"}, {**uk, "name": ""}):
        assert (
            await client.post("/api/v1/standards/frameworks", json=bad, headers=admin.headers)
        ).status_code == 422, bad


async def test_search_standards(client: AsyncClient, admin: Account) -> None:
    await setup_ccss(client, admin)

    async def codes(**params: Any) -> list[str]:
        response = await client.get("/api/v1/standards", params=params)
        assert response.status_code == 200
        return [s["code"] for s in response.json()["items"]]

    assert await codes(framework="ccss-math") == ["4.NF.A.1", "4.NF.A.2", "6.EE.A.1"]
    assert await codes(grade_level=7) == ["6.EE.A.1"]  # within the 6-8 range
    assert await codes(q="compare") == ["4.NF.A.2"]
    assert await codes(q="4.NF") == ["4.NF.A.1", "4.NF.A.2"]
    assert await codes(country="GB") == []


async def test_reimport_updates_in_place(client: AsyncClient, admin: Account) -> None:
    await setup_ccss(client, admin)
    again = await client.put(
        "/api/v1/standards/frameworks/CCSS-MATH/standards",
        json={
            "standards": [
                {**STANDARDS[0], "description": "Revised wording."},
                {"code": "5.NF.A.1", "description": "Add fractions."},
            ]
        },
        headers=admin.headers,
    )
    assert again.json() == {"created": 1, "updated": 1}
    revised = (await client.get("/api/v1/standards", params={"q": "4.NF.A.1"})).json()["items"][0]
    assert revised["description"] == "Revised wording."
    missing = await client.put(
        "/api/v1/standards/frameworks/NOPE/standards",
        json={"standards": STANDARDS},
        headers=admin.headers,
    )
    assert missing.status_code == 404
    wrong_range = await client.put(
        "/api/v1/standards/frameworks/CCSS-MATH/standards",
        json={"standards": [{"code": "X", "description": "x", "grade_min": 8, "grade_max": 3}]},
        headers=admin.headers,
    )
    assert wrong_range.status_code == 422


async def test_only_admins_manage_standards(client: AsyncClient, teacher: Account) -> None:
    assert (await client.post("/api/v1/standards/frameworks", json=CCSS)).status_code == 401
    assert (
        await client.post("/api/v1/standards/frameworks", json=CCSS, headers=teacher.headers)
    ).status_code == 403
    assert (
        await client.put(
            "/api/v1/standards/frameworks/CCSS-MATH/standards",
            json={"standards": STANDARDS},
            headers=teacher.headers,
        )
    ).status_code == 403


async def test_lessons_link_to_standards_and_versions_keep_them(
    client: AsyncClient, admin: Account, teacher: Account, make_account: AccountFactory
) -> None:
    await setup_ccss(client, admin)
    items = (await client.get("/api/v1/standards", params={"framework": "CCSS-MATH"})).json()[
        "items"
    ]
    ids = [s["id"] for s in items[:2]]
    cid, lesson = await course_with_lesson(client, teacher, [])
    url = f"/api/v1/courses/{cid}/lessons/{lesson['id']}/standards"
    linked = await client.put(url, json={"standard_ids": ids + ids[:1]}, headers=teacher.headers)
    assert linked.status_code == 200
    assert sorted(linked.json()["standard_ids"]) == sorted(ids)  # duplicates dropped

    await publish(client, teacher, cid)
    outline = (await client.get(f"/api/v1/courses/{cid}/content")).json()
    published = outline["units"][0]["lessons"][0]["standards"]
    assert [(s["framework"], s["code"]) for s in published] == [
        ("CCSS-MATH", "4.NF.A.1"),
        ("CCSS-MATH", "4.NF.A.2"),
    ]

    unknown = await client.put(
        url,
        json={"standard_ids": ["00000000-0000-0000-0000-000000000000"]},
        headers=teacher.headers,
    )
    assert unknown.status_code == 404
    other = await make_account(Role.TEACHER, teacher_verified=True)
    assert (
        await client.put(url, json={"standard_ids": ids}, headers=other.headers)
    ).status_code == 403
