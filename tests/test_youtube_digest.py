import json
from datetime import datetime, timezone

import pytest

from screener.youtube_digest import (
    PublicTranscriptUnavailable,
    VideoEntry,
    _extract_balanced_json,
    choose_caption_track,
    parse_feed,
    parse_json3_transcript,
    select_new_videos,
)


def test_parse_feed_extracts_video_entries():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
          xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>yt:video:abc123</id>
        <yt:videoId>abc123</yt:videoId>
        <title>Apple and Microsoft analysis</title>
        <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
        <published>2026-05-26T10:00:00+00:00</published>
        <updated>2026-05-26T10:10:00+00:00</updated>
      </entry>
    </feed>
    """

    videos = parse_feed(xml)

    assert videos == [
        VideoEntry(
            video_id="abc123",
            title="Apple and Microsoft analysis",
            url="https://www.youtube.com/watch?v=abc123",
            published_at="2026-05-26T10:00:00+00:00",
            updated_at="2026-05-26T10:10:00+00:00",
        )
    ]


def test_select_new_videos_filters_processed_and_old_entries():
    now = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
    videos = [
        VideoEntry("old", "old", "url", "2026-05-20T12:00:00+00:00", None),
        VideoEntry("done", "done", "url", "2026-05-26T09:00:00+00:00", None),
        VideoEntry("new", "new", "url", "2026-05-26T10:00:00+00:00", None),
    ]

    selected = select_new_videos(
        videos,
        {"processed_video_ids": ["done"]},
        since_hours=36,
        max_new=3,
        now=now,
    )

    assert [video.video_id for video in selected] == ["new"]


def test_select_new_videos_caps_to_latest_items():
    now = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
    videos = [
        VideoEntry("one", "one", "url", "2026-05-26T08:00:00+00:00", None),
        VideoEntry("two", "two", "url", "2026-05-26T09:00:00+00:00", None),
        VideoEntry("three", "three", "url", "2026-05-26T10:00:00+00:00", None),
    ]

    selected = select_new_videos(
        videos,
        {"processed_video_ids": []},
        since_hours=36,
        max_new=2,
        now=now,
    )

    assert [video.video_id for video in selected] == ["two", "three"]


def test_choose_caption_track_prefers_chinese_then_manual():
    tracks = [
        {"baseUrl": "https://example.test/en", "languageCode": "en"},
        {
            "baseUrl": "https://example.test/zh-auto",
            "languageCode": "zh-Hant",
            "kind": "asr",
        },
        {"baseUrl": "https://example.test/zh", "languageCode": "zh-Hant"},
    ]

    chosen = choose_caption_track(tracks)

    assert chosen["baseUrl"] == "https://example.test/zh"


def test_choose_caption_track_rejects_empty_list():
    with pytest.raises(PublicTranscriptUnavailable):
        choose_caption_track([])


def test_extract_balanced_json_handles_nested_strings():
    html = (
        'before ytInitialPlayerResponse = {"captions":{"name":"{not brace}"},'
        '"nested":{"ok":true}}; after'
    )

    data = _extract_balanced_json(html, "ytInitialPlayerResponse")

    assert data["captions"]["name"] == "{not brace}"
    assert data["nested"]["ok"] is True


def test_parse_json3_transcript_compacts_segments():
    payload = {
        "events": [
            {"segs": [{"utf8": "Apple "}, {"utf8": "突破 200 美元"}]},
            {"segs": [{"utf8": "\nMicrosoft 回測支撐"}]},
            {"aAppend": 1},
        ]
    }

    transcript = parse_json3_transcript(json.dumps(payload))

    assert transcript == "Apple 突破 200 美元\nMicrosoft 回測支撐"
