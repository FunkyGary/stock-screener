import json
from datetime import datetime, timezone

import pytest

from screener.youtube_digest import (
    DEFAULT_SINCE_HOURS,
    GITHUB_MODELS_URL,
    LATEST_JSON_PATH,
    LATEST_MARKDOWN_PATH,
    PublicTranscriptUnavailable,
    Transcript,
    VideoEntry,
    _chat_completion_output_text,
    _extract_balanced_json,
    choose_caption_track,
    parse_feed,
    parse_json3_transcript,
    prune_old_reports,
    run_digest,
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


def test_default_since_hours_is_seven_days():
    assert DEFAULT_SINCE_HOURS == 168


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


def test_run_digest_uses_audio_fallback_when_public_captions_are_missing(
    monkeypatch,
):
    video = VideoEntry(
        video_id="abc123",
        title="Apple target prices",
        url="https://www.youtube.com/watch?v=abc123",
        published_at="2026-05-26T10:00:00+00:00",
        updated_at=None,
    )
    captured = {}

    monkeypatch.setattr(
        "screener.youtube_digest.load_state",
        lambda: {"processed_video_ids": []},
    )
    monkeypatch.setattr("screener.youtube_digest.write_state", lambda state: None)
    monkeypatch.setattr("screener.youtube_digest.fetch_feed", lambda channel_id: [video])

    def missing_caption(video_id):
        raise PublicTranscriptUnavailable("no public caption tracks found")

    monkeypatch.setattr(
        "screener.youtube_digest.fetch_public_transcript", missing_caption
    )

    def fake_transcribe(video_id, *, whisper_model):
        captured["video_id"] = video_id
        captured["whisper_model"] = whisper_model
        return Transcript(
            text="Apple 目標買入價 180 美元。",
            language_code=None,
            language_name="Whisper audio transcript (tiny)",
            is_auto_generated=True,
        )

    monkeypatch.setattr(
        "screener.youtube_digest.transcribe_video_audio", fake_transcribe
    )
    monkeypatch.setattr(
        "screener.youtube_digest.summarize_with_github_models",
        lambda video, transcript, token, model: "# summary",
    )
    monkeypatch.setattr(
        "screener.youtube_digest._write_digest_files",
        lambda digests, channel_id, channel_title, generated_at: None,
    )

    digests = run_digest(
        channel_id="channel",
        channel_title="Channel",
        since_hours=36,
        max_new=1,
        github_token="ghs_test",
        model="openai/gpt-4.1",
        audio_fallback=True,
        whisper_model="tiny",
    )

    assert len(digests) == 1
    assert digests[0].markdown == "# summary"
    assert captured == {"video_id": "abc123", "whisper_model": "tiny"}


def test_prune_old_reports_removes_only_expired_per_video_markdown(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("screener.youtube_digest.REPORT_DIR", tmp_path)
    monkeypatch.setattr(
        "screener.youtube_digest.LATEST_MARKDOWN_PATH", tmp_path / "latest.md"
    )
    monkeypatch.setattr(
        "screener.youtube_digest.LATEST_JSON_PATH", tmp_path / "latest.json"
    )
    monkeypatch.setattr("screener.youtube_digest.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("screener.youtube_digest.repo_root", lambda: tmp_path.parent)

    old_report = tmp_path / "2026-05-18_oldvideo.md"
    fresh_report = tmp_path / "2026-05-24_newvideo.md"
    latest = tmp_path / LATEST_MARKDOWN_PATH.name
    latest_json = tmp_path / LATEST_JSON_PATH.name
    state = tmp_path / "state.json"
    for path in (old_report, fresh_report, latest, latest_json, state):
        path.write_text("content", encoding="utf-8")

    removed = prune_old_reports(
        now=datetime(2026, 5, 26, tzinfo=timezone.utc),
        retention_days=7,
    )

    assert removed == [str(old_report.relative_to(tmp_path.parent))]
    assert not old_report.exists()
    assert fresh_report.exists()
    assert latest.exists()
    assert latest_json.exists()
    assert state.exists()
