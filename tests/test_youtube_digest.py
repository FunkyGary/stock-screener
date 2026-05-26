import json
from datetime import datetime, timezone

import pytest

from screener.youtube_digest import (
    GITHUB_MODELS_URL,
    PublicTranscriptUnavailable,
    Transcript,
    VideoEntry,
    _chat_completion_output_text,
    _extract_balanced_json,
    choose_caption_track,
    parse_feed,
    parse_json3_transcript,
    select_new_videos,
    summarize_with_github_models,
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


def test_chat_completion_output_text_extracts_message_content():
    payload = {"choices": [{"message": {"content": "# 影片精華\n\nApple 重點"}}]}

    output = _chat_completion_output_text(payload)

    assert output == "# 影片精華\n\nApple 重點"


def test_summarize_with_github_models_posts_chat_completion(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True

        def json(self):
            return {"choices": [{"message": {"content": "# summary"}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("screener.youtube_digest.requests.post", fake_post)
    video = VideoEntry(
        video_id="abc123",
        title="Apple target prices",
        url="https://www.youtube.com/watch?v=abc123",
        published_at="2026-05-26T10:00:00+00:00",
        updated_at=None,
    )
    transcript = Transcript(
        text="Apple 目標買入價 180 美元，目標賣出價 220 美元。",
        language_code="zh-Hant",
        language_name="Chinese",
        is_auto_generated=False,
    )

    output = summarize_with_github_models(
        video,
        transcript,
        token="ghs_test",
        model="openai/gpt-4.1",
    )

    assert output == "# summary"
    assert captured["url"] == GITHUB_MODELS_URL
    assert captured["headers"]["Authorization"] == "Bearer ghs_test"
    assert captured["json"]["model"] == "openai/gpt-4.1"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert "Apple 目標買入價 180 美元" in captured["json"]["messages"][1]["content"]
