"""Hardcover reviews: query variables, parsing and the reviews endpoint."""

from __future__ import annotations

import hashlib
import importlib
from typing import Any
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import (
    BookReview,
    ReviewPage,
    ReviewQuery,
    ReviewSort,
)
from shelfmark.metadata_providers.hardcover import (
    HARDCOVER_PUBLIC_PRIVACY_ID,
    HardcoverProvider,
)


def _count(value: int) -> dict[str, Any]:
    return {"aggregate": {"count": value}}


def _payload(rows: list[dict[str, Any]], *, filtered: int) -> dict[str, Any]:
    return {
        "filtered": _count(filtered),
        "all": _count(40),
        "spoilers": _count(3),
        "stars_1": _count(1),
        "stars_2": _count(2),
        "stars_3": _count(5),
        "stars_4": _count(12),
        "stars_5": _count(20),
        "user_books": rows,
    }


def _row(review_id: int, text: str | None, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": review_id,
        "rating": 4.5,
        "reviewed_at": "2024-03-13T00:00:00",
        "likes_count": 7,
        "review_has_spoilers": False,
        "review_raw": text,
        "user": {"username": "lamb", "name": "Lamb", "image": {"url": "https://a/x.png"}},
    }
    row.update(overrides)
    return row


@pytest.fixture
def provider(monkeypatch):
    # Each test sends the same book id, so a cached page would hide the stubbed payload.
    monkeypatch.setattr(
        "shelfmark.core.config.config.get",
        lambda key, default=None: False if key == "METADATA_CACHE_ENABLED" else default,
    )
    return HardcoverProvider(api_key="test-token")


def _capture_query(monkeypatch, provider, payload):
    captured: dict[str, Any] = {}

    def fake_execute(query, variables, raise_on_error=False):
        captured["variables"] = variables
        captured["raise_on_error"] = raise_on_error
        return payload

    monkeypatch.setattr(provider, "_execute_query", fake_execute)
    return captured


class TestHardcoverReviews:
    def test_builds_filters_order_and_offset(self, monkeypatch, provider):
        captured = _capture_query(monkeypatch, provider, _payload([], filtered=0))

        provider.get_book_reviews(
            "123",
            ReviewQuery(sort=ReviewSort.NEWEST, stars=4, hide_spoilers=True, page=3, limit=5),
        )

        variables = captured["variables"]
        assert captured["raise_on_error"] is True
        assert variables["where"] == {
            "book_id": {"_eq": 123},
            "has_review": {"_eq": True},
            "privacy_setting_id": {"_eq": HARDCOVER_PUBLIC_PRIVACY_ID},
            "rating": {"_gte": 4, "_lt": 5},
            "review_has_spoilers": {"_eq": False},
        }
        assert "rating" not in variables["all"]
        assert variables["order_by"][0] == {"reviewed_at": "desc_nulls_last"}
        assert variables["limit"] == 5
        assert variables["offset"] == 10

    def test_star_buckets_round_half_stars_down(self, monkeypatch, provider):
        captured = _capture_query(monkeypatch, provider, _payload([], filtered=0))

        provider.get_book_reviews("123", ReviewQuery())

        variables = captured["variables"]
        assert variables["stars_1"]["rating"] == {"_gt": 0, "_lt": 2}
        assert variables["stars_3"]["rating"] == {"_gte": 3, "_lt": 4}
        assert variables["stars_5"]["rating"] == {"_gte": 5}

    def test_parses_reviews_and_counts(self, monkeypatch, provider):
        rows = [
            _row(1, "Great book."),
            _row(2, "   "),  # no text: skipped
            _row(3, "Twist ending!", rating=None, review_has_spoilers=True, user={}),
        ]
        _capture_query(monkeypatch, provider, _payload(rows, filtered=12))

        page = provider.get_book_reviews("123", ReviewQuery(limit=10))

        assert page.total == 12
        assert page.has_more is True
        assert page.review_count == 40
        assert page.spoiler_count == 3
        assert page.star_counts == {"5": 20, "4": 12, "3": 5, "2": 2, "1": 1}
        assert page.reviews[0] == BookReview(
            id="1",
            text="Great book.",
            rating=4.5,
            reviewed_at="2024-03-13T00:00:00",
            likes=7,
            has_spoilers=False,
            username="lamb",
            display_name="Lamb",
            avatar_url="https://a/x.png",
            profile_url="https://hardcover.app/@lamb",
        )
        assert [review.id for review in page.reviews] == ["1", "3"]
        assert page.reviews[1].rating is None
        assert page.reviews[1].has_spoilers is True
        assert page.reviews[1].profile_url is None

    def test_last_page_has_no_more(self, monkeypatch, provider):
        _capture_query(monkeypatch, provider, _payload([_row(1, "Fine.")], filtered=21))

        page = provider.get_book_reviews("123", ReviewQuery(page=3, limit=10))

        assert page.has_more is False

    def test_caps_page_size(self, monkeypatch, provider):
        captured = _capture_query(monkeypatch, provider, _payload([], filtered=0))

        provider.get_book_reviews("123", ReviewQuery(page=2, limit=500))

        assert captured["variables"]["limit"] == 25
        assert captured["variables"]["offset"] == 25

    def test_rejects_invalid_book_id(self, provider):
        with pytest.raises(ValueError, match="valid Hardcover book id"):
            provider.get_book_reviews("abc", ReviewQuery())

    def test_raises_when_hardcover_returns_nothing(self, monkeypatch, provider):
        _capture_query(monkeypatch, provider, None)

        with pytest.raises(RuntimeError, match="could not load reviews"):
            provider.get_book_reviews("123", ReviewQuery())


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


class _StubProvider:
    display_name = "Stub"

    def __init__(self) -> None:
        self.queries: list[ReviewQuery] = []

    def is_available(self) -> bool:
        return True

    def get_book_reviews(self, book_id: str, query: ReviewQuery) -> ReviewPage:
        self.queries.append(query)
        return ReviewPage(
            reviews=[
                BookReview(id="9", text="Loved it.", username="lamb", avatar_url="https://a/x.png")
            ],
            page=query.page,
            total=1,
            has_more=False,
            review_count=1,
            spoiler_count=0,
            star_counts={"5": 1, "4": 0, "3": 0, "2": 0, "1": 0},
        )


def _get(main_module, client, url, stub):
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch("shelfmark.metadata_providers.get_provider", return_value=stub),
        patch("shelfmark.core.utils.transform_cover_url", lambda url, cache_id: f"/p/{cache_id}"),
    ):
        return client.get(url)


class TestReviewsEndpoint:
    def test_passes_query_and_proxies_avatars(self, main_module, client):
        stub = _StubProvider()

        response = _get(
            main_module,
            client,
            "/api/metadata/book/stub/42/reviews?sort=highest&stars=5&spoilers=hide&page=2",
            stub,
        )

        assert response.status_code == 200, response.get_json()
        assert stub.queries == [
            ReviewQuery(sort=ReviewSort.HIGHEST, stars=5, hide_spoilers=True, page=2, limit=10)
        ]
        body = response.get_json()
        expected_digest = hashlib.sha256(b"https://a/x.png").hexdigest()[:32]
        assert body["reviews"][0]["avatar_url"] == f"/p/stub_avatar_{expected_digest}"
        assert body["star_counts"]["5"] == 1

    @pytest.mark.parametrize(
        ("query", "message"),
        [("sort=best", "Invalid review sort"), ("stars=6", "stars must be between 1 and 5")],
    )
    def test_rejects_bad_parameters(self, main_module, client, query, message):
        response = _get(
            main_module, client, f"/api/metadata/book/stub/42/reviews?{query}", _StubProvider()
        )

        assert response.status_code == 400
        assert response.get_json()["error"] == message
