"""Daily YouTube public-caption digest for stock-focused video notes."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from .io import repo_root

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_ID = "UCFQsi7WaF5X41tcuOryDk8w"
DEFAULT_CHANNEL_TITLE = "视野环球财经"
DEFAULT_MODEL = "openai/gpt-4.1"
DEFAULT_SINCE_HOURS = 36
DEFAULT_MAX_NEW = 3
TRANSCRIPT_CHAR_LIMIT = 60000
GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REPORT_DIR = repo_root() / "data" / "youtube_digest"
STATE_PATH = REPORT_DIR / "state.json"
LATEST_MARKDOWN_PATH = REPORT_DIR / "latest.md"
LATEST_JSON_PATH = REPORT_DIR / "latest.json"

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


class DigestError(RuntimeError):
    """Raised when the digest cannot continue safely."""


class PublicTranscriptUnavailable(DigestError):
    """Raised when a video has no public caption track we can read."""


@dataclass(frozen=True)
class VideoEntry:
    video_id: str
    title: str
    url: str
    published_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class Transcript:
    text: str
    language_code: str | None
    language_name: str | None
    is_auto_generated: bool


@dataclass(frozen=True)
class VideoDigest:
    video: VideoEntry
    transcript: Transcript
    markdown: str
    report_path: str
    generated_at: str


def youtube_feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _format_date_for_filename(value: str | None, now: datetime) -> str:
    dt = _parse_datetime(value) or now
    return dt.strftime("%Y-%m-%d")


def _text_or_none(node: ET.Element, path: str) -> str | None:
    value = node.findtext(path, namespaces=ATOM_NS)
    return value.strip() if value else None


def parse_feed(xml_text: str) -> list[VideoEntry]:
    root = ET.fromstring(xml_text)
    videos: list[VideoEntry] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        video_id = _text_or_none(entry, "yt:videoId")
        title = _text_or_none(entry, "atom:title")
        if not video_id or not title:
            continue
        link_node = entry.find("atom:link[@rel='alternate']", ATOM_NS)
        url = (
            link_node.attrib.get("href", "").strip()
            if link_node is not None
            else f"https://www.youtube.com/watch?v={video_id}"
        )
        videos.append(
            VideoEntry(
                video_id=video_id,
                title=title,
                url=url or f"https://www.youtube.com/watch?v={video_id}",
                published_at=_text_or_none(entry, "atom:published"),
                updated_at=_text_or_none(entry, "atom:updated"),
            )
        )
    return videos


def fetch_feed(channel_id: str) -> list[VideoEntry]:
    response = requests.get(
        youtube_feed_url(channel_id),
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return parse_feed(response.text)


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"processed_video_ids": []}
    with path.open() as f:
        state = json.load(f)
    state.setdefault("processed_video_ids", [])
    return state


def write_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def select_new_videos(
    videos: list[VideoEntry],
    state: dict[str, Any],
    *,
    since_hours: int | None,
    max_new: int,
    now: datetime | None = None,
) -> list[VideoEntry]:
    current_time = now or _utc_now()
    processed = set(state.get("processed_video_ids", []))
    cutoff = (
        current_time - timedelta(hours=since_hours)
        if since_hours is not None
        else None
    )

    eligible: list[VideoEntry] = []
    for video in videos:
        if video.video_id in processed:
            continue
        published = _parse_datetime(video.published_at)
        if cutoff is not None and published is not None and published < cutoff:
            continue
        eligible.append(video)

    eligible.sort(key=lambda item: item.published_at or "")
    if max_new > 0:
        eligible = eligible[-max_new:]
    return eligible


def _extract_balanced_json(text: str, marker: str) -> dict[str, Any]:
    marker_index = text.find(marker)
    if marker_index < 0:
        raise PublicTranscriptUnavailable("caption metadata not found")
    start = text.find("{", marker_index)
    if start < 0:
        raise PublicTranscriptUnavailable("caption metadata JSON not found")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])

    raise PublicTranscriptUnavailable("caption metadata JSON is incomplete")


def _caption_tracks(player_response: dict[str, Any]) -> list[dict[str, Any]]:
    renderer = (
        player_response.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
    )
    tracks = renderer.get("captionTracks") or []
    return [track for track in tracks if track.get("baseUrl")]


def _caption_track_rank(track: dict[str, Any]) -> tuple[int, int]:
    lang = str(track.get("languageCode") or "").lower()
    language_rank = 99
    for index, prefix in enumerate(("zh-hant", "zh-tw", "zh", "zh-hans", "en")):
        if lang == prefix or lang.startswith(prefix + "-"):
            language_rank = index
            break
    auto_rank = 1 if track.get("kind") == "asr" else 0
    return (language_rank, auto_rank)


def choose_caption_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    if not tracks:
        raise PublicTranscriptUnavailable("no public caption tracks found")
    return sorted(tracks, key=_caption_track_rank)[0]


def _with_caption_format(base_url: str, fmt: str) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["fmt"] = fmt
    return urlunparse(parsed._replace(query=urlencode(query)))


def parse_json3_transcript(payload: str) -> str:
    data = json.loads(payload)
    chunks: list[str] = []
    for event in data.get("events", []):
        segs = event.get("segs") or []
        text = "".join(str(seg.get("utf8", "")) for seg in segs)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip()


def parse_xml_transcript(payload: str) -> str:
    root = ET.fromstring(payload)
    chunks = [
        re.sub(r"\s+", " ", html.unescape(node.text or "")).strip()
        for node in root.findall(".//text")
    ]
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _download_caption_text(base_url: str) -> str:
    json_response = requests.get(
        _with_caption_format(base_url, "json3"),
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    if json_response.ok:
        try:
            transcript = parse_json3_transcript(json_response.text)
            if transcript:
                return transcript
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.debug("json3 caption parse failed; falling back to XML")

    xml_response = requests.get(
        _with_caption_format(base_url, "srv3"),
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    xml_response.raise_for_status()
    transcript = parse_xml_transcript(xml_response.text)
    if not transcript:
        raise PublicTranscriptUnavailable("public caption track was empty")
    return transcript


def fetch_public_transcript(video_id: str) -> Transcript:
    response = requests.get(
        f"https://www.youtube.com/watch?v={video_id}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    player_response = _extract_balanced_json(
        response.text, "ytInitialPlayerResponse"
    )
    track = choose_caption_track(_caption_tracks(player_response))
    text = _download_caption_text(track["baseUrl"])
    name_node = track.get("name", {})
    language_name = None
    if isinstance(name_node, dict):
        language_name = name_node.get("simpleText")
        if not language_name:
            runs = name_node.get("runs") or []
            language_name = "".join(run.get("text", "") for run in runs) or None
    return Transcript(
        text=text,
        language_code=track.get("languageCode"),
        language_name=language_name,
        is_auto_generated=track.get("kind") == "asr",
    )


def _truncate_transcript(text: str, limit: int = TRANSCRIPT_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    marker = "\n\n[...逐字稿過長，中段已省略...]\n\n"
    head_len = max(0, limit // 2)
    tail_len = max(0, limit - head_len - len(marker))
    return text[:head_len].rstrip() + marker + text[-tail_len:].lstrip()


def _chat_completion_output_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        raise DigestError("GitHub Models response did not include choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        output = content.strip()
    elif isinstance(content, list):
        chunks = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        ]
        output = "\n".join(chunks).strip()
    else:
        output = ""
    if not output:
        raise DigestError("GitHub Models response did not include text output")
    return output


def _summary_prompt(video: VideoEntry, transcript: Transcript) -> str:
    return textwrap.dedent(
        f"""
        你會收到一支財經 YouTube 影片的公開字幕。請產出繁體中文 Markdown
        報告，重點放在單一個股或 ETF 的分析，不要把大盤雜訊寫成個股建議。

        嚴格規則：
        - 只能使用逐字稿明確提到的內容；不能自行補目標價、支撐壓力或買賣價。
        - 如果沒有明確講到目標買入價、目標賣出價、停利或停損，填「未提及」。
        - 價格請保留原影片幣別與單位，例如 USD、美元、台幣。
        - 區分「主持人觀點」和「明確價格」；不要把你的推論當成影片觀點。
        - 這是影片內容摘要，不是投資建議。

        請使用這個格式：
        # 影片精華：{{影片標題}}

        影片：{{URL}}
        發布時間：{{發布時間 UTC}}
        字幕：{{語言}}，自動字幕：{{是/否}}

        ## 個股重點
        | 個股/代號 | 方向 | 走勢觀點 | 目標買入價 | 目標賣出價/停利 | 停損/風險 | 依據 |
        |---|---|---|---|---|---|---|

        ## 觀察清單
        - 用 3-8 點列出後續值得追蹤的個股事件、財報、技術位、風險。

        ## 未明確給價的內容
        - 列出有討論但沒有給具體買入/賣出價的個股。

        ## 注意
        - 僅摘要原影片內容，非投資建議。

        影片標題：{video.title}
        URL：{video.url}
        發布時間：{video.published_at or "unknown"}
        字幕語言：{transcript.language_name or transcript.language_code or "unknown"}
        自動字幕：{"是" if transcript.is_auto_generated else "否"}

        逐字稿：
        {_truncate_transcript(transcript.text)}
        """
    ).strip()


def summarize_with_github_models(
    video: VideoEntry,
    transcript: Transcript,
    *,
    token: str,
    model: str = DEFAULT_MODEL,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是謹慎的財經影片摘要助手，只能根據使用者提供的逐字稿輸出。",
            },
            {"role": "user", "content": _summary_prompt(video, transcript)},
        ],
        "temperature": 0.2,
        "max_tokens": 3500,
    }
    response = requests.post(
        GITHUB_MODELS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=payload,
        timeout=90,
    )
    if not response.ok:
        raise DigestError(
            f"GitHub Models request failed: {response.status_code} {response.text}"
        )
    return _chat_completion_output_text(response.json())


def _daily_report_markdown(
    digests: list[VideoDigest],
    *,
    channel_title: str,
    generated_at: str,
) -> str:
    lines = [
        f"# {channel_title} 每日影片精華",
        "",
        f"產生時間：{generated_at} UTC",
        "",
        "> 僅根據影片公開字幕摘要；內容不是投資建議。",
        "",
    ]
    for index, digest in enumerate(digests, start=1):
        if index > 1:
            lines.extend(["", "---", ""])
        lines.append(digest.markdown.strip())
    return "\n".join(lines).rstrip() + "\n"


def _write_digest_files(
    digests: list[VideoDigest],
    *,
    channel_id: str,
    channel_title: str,
    generated_at: str,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    latest_markdown = _daily_report_markdown(
        digests, channel_title=channel_title, generated_at=generated_at
    )
    LATEST_MARKDOWN_PATH.write_text(latest_markdown, encoding="utf-8")

    latest = {
        "channel_id": channel_id,
        "channel_title": channel_title,
        "generated_at": generated_at,
        "reports": [
            {
                "video": asdict(digest.video),
                "transcript": {
                    "language_code": digest.transcript.language_code,
                    "language_name": digest.transcript.language_name,
                    "is_auto_generated": digest.transcript.is_auto_generated,
                },
                "report_path": digest.report_path,
            }
            for digest in digests
        ],
    }
    with LATEST_JSON_PATH.open("w") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _report_path(video: VideoEntry, now: datetime) -> Path:
    date_part = _format_date_for_filename(video.published_at, now)
    return REPORT_DIR / f"{date_part}_{video.video_id}.md"


def run_digest(
    *,
    channel_id: str,
    channel_title: str,
    since_hours: int | None,
    max_new: int,
    github_token: str | None,
    model: str,
    now: datetime | None = None,
) -> list[VideoDigest]:
    current_time = now or _utc_now()
    generated_at = current_time.isoformat(timespec="seconds")
    state = load_state()
    videos = fetch_feed(channel_id)
    new_videos = select_new_videos(
        videos,
        state,
        since_hours=since_hours,
        max_new=max_new,
        now=current_time,
    )
    if not new_videos:
        logger.info("No new YouTube videos found for %s", channel_id)
        return []
    if not github_token:
        raise DigestError("GITHUB_TOKEN is required to summarize new videos")

    digests: list[VideoDigest] = []
    skipped: list[dict[str, str]] = []
    for video in new_videos:
        logger.info("Processing YouTube video %s %s", video.video_id, video.title)
        try:
            transcript = fetch_public_transcript(video.video_id)
        except PublicTranscriptUnavailable as exc:
            logger.warning("Skipping %s: %s", video.video_id, exc)
            skipped.append({"video_id": video.video_id, "reason": str(exc)})
            continue

        markdown = summarize_with_github_models(
            video,
            transcript,
            token=github_token,
            model=model,
        )
        path = _report_path(video, current_time)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        digests.append(
            VideoDigest(
                video=video,
                transcript=transcript,
                markdown=markdown,
                report_path=str(path.relative_to(repo_root())),
                generated_at=generated_at,
            )
        )

    if not digests:
        state["last_checked_at"] = generated_at
        state["last_skipped"] = skipped
        write_state(state)
        return []

    processed_ids = list(dict.fromkeys(state.get("processed_video_ids", [])))
    for digest in digests:
        processed_ids.append(digest.video.video_id)
    state["processed_video_ids"] = list(dict.fromkeys(processed_ids))[-500:]
    state["last_checked_at"] = generated_at
    state["last_generated_at"] = generated_at
    state["last_skipped"] = skipped
    write_state(state)
    _write_digest_files(
        digests,
        channel_id=channel_id,
        channel_title=channel_title,
        generated_at=generated_at,
    )
    return digests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--channel-id",
        default=os.environ.get("YOUTUBE_CHANNEL_ID", DEFAULT_CHANNEL_ID),
    )
    parser.add_argument(
        "--channel-title",
        default=os.environ.get("YOUTUBE_CHANNEL_TITLE", DEFAULT_CHANNEL_TITLE),
    )
    parser.add_argument("--since-hours", type=int, default=DEFAULT_SINCE_HOURS)
    parser.add_argument("--max-new", type=int, default=DEFAULT_MAX_NEW)
    parser.add_argument(
        "--model",
        default=os.environ.get("GITHUB_MODEL", DEFAULT_MODEL),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    github_token = os.environ.get("GITHUB_TOKEN")
    run_digest(
        channel_id=args.channel_id,
        channel_title=args.channel_title,
        since_hours=args.since_hours,
        max_new=args.max_new,
        github_token=github_token,
        model=args.model,
    )


if __name__ == "__main__":
    main()
