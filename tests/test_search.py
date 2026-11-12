"""Full-text search of published courses and lessons (BE-CAT-07)."""

from typing import Any

from httpx import AsyncClient

from tests.conftest import Account, make_course, make_subject
from tests.test_content import save
from tests.test_publishing import publish
from tests.test_structure import add_lesson, add_unit


async def search(client: AsyncClient, q: str, **params: Any) -> list[tuple[str, str | None]]:
    response = await client.get("/api/v1/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return [(h["course_title"], h["lesson_title"]) for h in response.json()["items"]]


async def published_course(
    client: AsyncClient,
    author: Account,
    subject: dict[str, Any],
    title: str,
    lessons: dict[str, str],
    **fields: Any,
) -> dict[str, Any]:
    course = await make_course(
        client, subject["id"], headers=author.headers, title=title, status="draft", **fields
    )
    unit = (await add_unit(client, author, course["id"], "Unit"))["units"][0]["id"]
    for lesson_title, text in lessons.items():
        lesson = await add_lesson(client, author, course["id"], unit, lesson_title)
        await save(client, author, course["id"], lesson, [{"type": "text", "markdown": text}])
    assert (await publish(client, author, course["id"])).status_code == 200
    return course


async def test_finds_courses_and_lessons_with_stemming(
    client: AsyncClient, teacher: Account
) -> None:
    maths = await make_subject(client, "Mathematics", headers=teacher.headers)
    await published_course(
        client,
        teacher,
        maths,
        "Fractions Fundamentals",
        {
            "Equivalent fractions": "Two fractions can name the same amount.",
            "Comparing": "Use a number line to compare.",
        },
    )
    await published_course(
        client,
        teacher,
        maths,
        "Geometry",
        {"Angles": "An angle is formed by two rays."},
        summary="Shapes and lines.",
    )

    # "fraction" matches "Fractions" (English stemming); the title match ranks first.
    hits = await search(client, "fraction")
    assert hits[0] == ("Fractions Fundamentals", None)
    assert ("Fractions Fundamentals", "Equivalent fractions") in hits
    assert all(course == "Fractions Fundamentals" for course, _ in hits)

    # Words inside lesson content are found too.
    assert await search(client, "number line") == [("Fractions Fundamentals", "Comparing")]
    assert await search(client, "rays", kind="lesson") == [("Geometry", "Angles")]
    assert await search(client, "angle", kind="course") == []


async def test_each_course_is_searched_in_its_own_language(
    client: AsyncClient, teacher: Account
) -> None:
    maths = await make_subject(client, "Mathematics", headers=teacher.headers)
    await published_course(
        client,
        teacher,
        maths,
        "Los gatos",
        {"Gatos": "Los gatos duermen mucho."},
        language="es",
        summary="Un curso sobre gatos.",
    )
    # Spanish stemming: "gato" finds "gatos".
    assert ("Los gatos", None) in await search(client, "gato")


async def test_only_published_content_is_searchable(client: AsyncClient, teacher: Account) -> None:
    maths = await make_subject(client, "Mathematics", headers=teacher.headers)
    course = await published_course(
        client, teacher, maths, "Decimals", {"Tenths": "A tenth is 0.1."}
    )
    cid = course["id"]
    unit = (await client.get(f"/api/v1/courses/{cid}/draft", headers=teacher.headers)).json()[
        "units"
    ][0]["id"]
    lesson = await add_lesson(client, teacher, cid, unit, "Hundredths")
    await save(client, teacher, cid, lesson, [{"type": "text", "markdown": "A hundredth is tiny."}])
    assert await search(client, "hundredth") == []  # draft only
    await publish(client, teacher, cid)
    assert await search(client, "hundredth") == [("Decimals", "Hundredths")]

    await client.post(f"/api/v1/courses/{cid}/unpublish", headers=teacher.headers)
    assert await search(client, "decimals") == []
    await publish(client, teacher, cid)
    assert await search(client, "decimals")
    await client.delete(f"/api/v1/courses/{cid}", headers=teacher.headers)  # archive
    assert await search(client, "decimals") == []


async def test_renaming_a_published_course_updates_search(
    client: AsyncClient, teacher: Account
) -> None:
    maths = await make_subject(client, "Mathematics", headers=teacher.headers)
    course = await published_course(client, teacher, maths, "Old name", {})
    await client.patch(
        f"/api/v1/courses/{course['id']}", json={"title": "Probability"}, headers=teacher.headers
    )
    assert await search(client, "probability") == [("Probability", None)]


async def test_filters_syntax_and_plain_snippets(client: AsyncClient, teacher: Account) -> None:
    maths = await make_subject(client, "Mathematics", headers=teacher.headers)
    science = await make_subject(client, "Science", headers=teacher.headers)
    await published_course(
        client,
        teacher,
        maths,
        "Patterns",
        {"Sequences": "A <b>pattern</b> repeats."},
        grade_level=3,
    )
    await published_course(
        client,
        teacher,
        science,
        "Weather patterns",
        {"Clouds": "Clouds bring rain."},
        grade_level=5,
    )
    assert {c for c, _ in await search(client, "patterns", grade_level=5)} == {"Weather patterns"}
    assert {c for c, _ in await search(client, "patterns", subject="mathematics")} == {"Patterns"}
    assert {c for c, _ in await search(client, "patterns -weather")} == {"Patterns"}
    assert await search(client, '"bring rain"') == [("Weather patterns", "Clouds")]

    response = await client.get("/api/v1/search", params={"q": "repeats"})
    snippet = response.json()["items"][0]["snippet"]
    assert "repeats" in snippet and "[[" not in snippet  # plain text, no highlight markup
    assert response.json()["items"][0]["rank"] > 0

    assert (await client.get("/api/v1/search", params={"q": ""})).status_code == 422
    assert await search(client, "zzzz-nothing") == []
    assert await search(client, "patterns", subject="no-such-subject") == []
    # Odd input is not an error (websearch syntax never raises).
    assert isinstance(await search(client, "'); DROP TABLE courses; --"), list)
