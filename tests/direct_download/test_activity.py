"""Anna's Archive activity: parsing, grouping by file, caching and the endpoint."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.release_sources.direct_download import activity
from shelfmark.release_sources.direct_download.activity import (
    ActivityGroup,
    ActivityItem,
    parse_activity_page,
)
from shelfmark.release_sources.direct_download.common import DirectDownloadUnavailableError

MD5_A = "094ea6c3cd39c11415508d65740c0aa3"
MD5_B = "320a0de1d089958ca011b0974456c0b2"

# Trimmed from a real /activity page: a comment, a reaction on the same file, and a report.
ACTIVITY_HTML = f"""
<div class="main-inner">
  <div class="pb-2 mb-4 border-b">
    <div>
      <div class="text-black/64 text-sm" title="October 18, 2026, 2:48:24 PM UTC">48 minutes ago</div>
      Comment on <a href="/md5/{MD5_A}">{MD5_A}</a>
    </div>
    <div class="ml-8">
      <div><span class="font-bold " href="/profile/BauNC5f">#BauNC5f</span></div>
      <div class="whitespace-pre-line mb-1">Good scan.
Second line.</div>
    </div>
  </div>
  <div class="pb-2 mb-4 border-b">
    <div>
      <div class="text-black/64 text-sm" title="October 18, 2026, 2:44:38 PM UTC">1 hour ago</div>
      Reaction to <a href="/md5/{MD5_A}">{MD5_A}</a>
    </div>
    <div class="ml-8">
      <div>
        <span class="text-xl icon-[material-symbols--star-outline]"></span> Great file quality
        <span class="font-bold " href="/profile/4B5zSJK">#4B5zSJK</span>
      </div>
    </div>
  </div>
  <div class="pb-2 mb-4 border-b">
    <div>
      <div class="text-black/64 text-sm" title="October 18, 2026, 2:02:17 PM UTC">2 hours ago</div>
      <!-- File issue report on <a href="/md5/{MD5_B}">{MD5_B}</a> -->
    </div>
    <div class="flex pt-3 pb-3 border-b">
      <img class="w-full h-full object-cover" src="" alt="">
      <img class="w-full h-full object-cover" src="https://covers.z-lib.gd/x.jpg" alt="">
      <a href="/md5/{MD5_B}" class="js-vim-focus custom-a">Anästhesie (Zwölfte Ausgabe)</a>
      <a href="/search?q=Reinhard Larsen"><span class="icon-[mdi--user-edit]"></span> Reinhard Larsen</a>
      <div class="text-gray-800 font-semibold">German [de] · PDF · 0.1MB · 2022 ·&nbsp;Save</div>
    </div>
    <div>
      <div><span class="font-bold " href="/profile/9yV33zC">#9yV33zC</span></div>
      <div class="flex items-center">
        <span class="italic ml-1.5">File issue: Poor quality (e.g. missing pages)</span>
      </div>
      <div class="border-l-4 border-gray-300 pl-4">
        1 page only
      </div>
    </div>
  </div>
</div>
"""


def test_groups_entries_by_file_in_page_order():
    groups = parse_activity_page(ACTIVITY_HTML)

    assert [group.md5 for group in groups] == [MD5_A, MD5_B]
    assert groups[0].title is None
    assert groups[0].cover_url is None
    assert groups[0].items == [
        ActivityItem(
            kind="comment",
            user="#BauNC5f",
            text="Good scan.\nSecond line.",
            created_at="2026-10-18T14:48:24+00:00",
            relative_time="48 minutes ago",
        ),
        ActivityItem(
            kind="reaction",
            user="#4B5zSJK",
            text="Great file quality",
            created_at="2026-10-18T14:44:38+00:00",
            relative_time="1 hour ago",
        ),
    ]


def test_report_carries_file_details():
    report_group = parse_activity_page(ACTIVITY_HTML)[1]

    assert report_group.title == "Anästhesie (Zwölfte Ausgabe)"
    assert report_group.author == "Reinhard Larsen"
    assert report_group.details == ["German [de]", "PDF", "0.1MB", "2022"]
    # The first <img> has an empty src (no known cover), so the parser takes the next one.
    assert report_group.cover_url == "https://covers.z-lib.gd/x.jpg"
    assert report_group.items == [
        ActivityItem(
            kind="report",
            user="#9yV33zC",
            text="1 page only",
            report_type="Poor quality (e.g. missing pages)",
            created_at="2026-10-18T14:02:17+00:00",
            relative_time="2 hours ago",
        )
    ]


def test_skips_entries_without_a_file():
    html = '<div class="pb-2 mb-4 border-b"><div>Something else</div></div>'

    assert parse_activity_page(html) == []


def test_reaction_keeps_text_that_matches_the_user_name():
    html = f"""
    <div class="pb-2 mb-4 border-b">
      <div><div title="October 18, 2026, 2:44:38 PM UTC">now</div>
        Reaction to <a href="/md5/{MD5_A}">{MD5_A}</a></div>
      <div class="ml-8"><div>
        <span class="icon-[material-symbols--star-outline]"></span> Good
        <span class="font-bold" href="/profile/o">o</span>
      </div></div>
    </div>
    """

    (group,) = parse_activity_page(html)

    assert group.items[0].kind == "reaction"
    assert group.items[0].text == "Good"


@pytest.fixture
def no_latest_cache(monkeypatch):
    monkeypatch.setattr(activity, "_latest_cache", None)
    monkeypatch.setattr(activity, "ensure_available", lambda: None)
    monkeypatch.setattr(activity.network, "get_aa_base_url", lambda: "https://aa.example")


def test_latest_is_cached_until_refresh(monkeypatch, no_latest_cache):
    fetches: list[str] = []

    def fake_fetch(mode):
        fetches.append(mode)
        return ACTIVITY_HTML

    monkeypatch.setattr(activity, "_fetch_activity_html", fake_fetch)

    activity.get_activity("latest")
    activity.get_activity("latest")
    activity.get_activity("latest", refresh=True)

    assert fetches == ["latest", "latest"]


def test_groups_link_to_their_page_on_the_mirror(monkeypatch, no_latest_cache):
    monkeypatch.setattr(activity, "_fetch_activity_html", lambda mode: ACTIVITY_HTML)

    groups = activity.get_activity("random")

    assert [group.record_url for group in groups] == [
        f"https://aa.example/md5/{MD5_A}",
        f"https://aa.example/md5/{MD5_B}",
    ]


def test_random_is_never_cached(monkeypatch, no_latest_cache):
    fetches: list[str] = []

    def fake_fetch(mode):
        fetches.append(mode)
        return ACTIVITY_HTML

    monkeypatch.setattr(activity, "_fetch_activity_html", fake_fetch)

    activity.get_activity("random")
    activity.get_activity("random")

    assert fetches == ["random", "random"]


def test_unreachable_page_raises_unavailable(monkeypatch, no_latest_cache):
    monkeypatch.setattr(activity.downloader, "html_get_page", lambda *args, **kwargs: "")

    with pytest.raises(DirectDownloadUnavailableError, match="not reachable"):
        activity.get_activity("random")


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _get(main_module, client, url, **patches):
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch(
            "shelfmark.release_sources.direct_download.activity.get_activity",
            **patches,
        ),
    ):
        return client.get(url)


def test_endpoint_returns_groups(main_module, client):
    group = ActivityGroup(md5=MD5_A, items=[ActivityItem(kind="comment", text="Nice")])

    response = _get(
        main_module, client, "/api/annas-archive/activity?mode=random", return_value=[group]
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["mode"] == "random"
    assert body["groups"][0]["md5"] == MD5_A
    assert body["groups"][0]["items"][0]["text"] == "Nice"


def test_endpoint_proxies_covers(main_module, client):
    group = ActivityGroup(md5=MD5_B, cover_url="https://covers.z-lib.gd/x.jpg")

    with patch("shelfmark.core.utils.transform_cover_url", lambda url, cache_id: f"/p/{cache_id}"):
        response = _get(
            main_module, client, "/api/annas-archive/activity?mode=random", return_value=[group]
        )

    assert response.get_json()["groups"][0]["cover_url"] == f"/p/annas_{MD5_B}"


def test_endpoint_rejects_unknown_mode(main_module, client):
    response = _get(main_module, client, "/api/annas-archive/activity?mode=best", return_value=[])

    assert response.status_code == 400


def test_endpoint_reports_unavailable_source(main_module, client):
    response = _get(
        main_module,
        client,
        "/api/annas-archive/activity",
        side_effect=DirectDownloadUnavailableError("Direct Download is disabled."),
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "Direct Download is disabled."
