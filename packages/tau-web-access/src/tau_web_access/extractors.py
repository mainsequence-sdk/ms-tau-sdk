"""Readable content extraction for URLs, repositories, documents, and videos."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import shutil
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from pypdf import PdfReader
from youtube_transcript_api import YouTubeTranscriptApi

from .models import ExtractedContent
from .security import resolve_local_path, validate_public_url
from .settings import WebAccessSettings

TEXT_SUFFIXES = {
    ".c",
    ".cc",
    ".cfg",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    data: str
    mime_type: str
    label: str


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    extracted: ExtractedContent
    images: tuple[ExtractedImage, ...] = field(default_factory=tuple)
    duration: float | None = None


def _youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0] or None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            return parsed.path.split("/")[2]
    return None


def _github_repo(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or (len(parts) > 2 and parts[2] in {"blob", "tree"}):
        return None
    return parts[0], parts[1].removesuffix(".git")


def _extract_html(body: bytes, *, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    for element in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        element.decompose()
    content_root = soup.find("main") or soup.find("article") or soup.body or soup
    rendered = markdownify(str(content_root), heading_style="ATX")
    lines = [line.rstrip() for line in rendered.splitlines()]
    compact = "\n".join(line for line in lines if line.strip()).strip()
    if not compact:
        compact = content_root.get_text("\n", strip=True)
    return title, compact


def _extract_pdf(body: bytes) -> tuple[str, str]:
    reader = PdfReader(BytesIO(body))
    title = str((reader.metadata or {}).get("/Title") or "PDF document")
    pages = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append(f"## Page {index + 1}\n\n{text.strip()}")
    return title, "\n\n".join(pages)


def _read_local_text(path: Path) -> str:
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Local text file exceeds the 10MB extraction limit")
    return path.read_text(encoding="utf-8")


def _collect_repository_content(target: Path, *, max_chars: int) -> str:
    sections: list[str] = []
    size = 0
    for path in sorted(target.rglob("*")):
        if (
            not path.is_file()
            or ".git" in path.parts
            or path.suffix.lower() not in TEXT_SUFFIXES
        ):
            continue
        if path.stat().st_size > 512 * 1024:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        section = f"## {path.relative_to(target).as_posix()}\n\n```\n{content}\n```"
        if size + len(section) > max_chars:
            break
        sections.append(section)
        size += len(section)
    return "\n\n".join(sections)


def _encode_frames(temp_dir: str) -> tuple[ExtractedImage, ...]:
    return tuple(
        ExtractedImage(
            data=base64.b64encode(frame.read_bytes()).decode("ascii"),
            mime_type=mimetypes.guess_type(frame)[0] or "image/jpeg",
            label=f"Frame {index + 1}",
        )
        for index, frame in enumerate(sorted(Path(temp_dir).glob("frame-*.jpg")))
    )


async def _communicate(process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    communication = asyncio.create_task(process.communicate())
    try:
        return await asyncio.shield(communication)
    except BaseException:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(communication, timeout=1)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await communication
        raise


class ContentExtractor:
    def __init__(
        self,
        *,
        settings: WebAccessSettings,
        client: httpx.AsyncClient,
        cwd: Path,
    ) -> None:
        self.settings = settings
        self.client = client
        self.cwd = cwd.resolve()

    async def fetch(
        self,
        value: str,
        *,
        force_clone: bool = False,
        prompt: str | None = None,
        timestamp: str | None = None,
        frames: int | None = None,
        model: str | None = None,
    ) -> FetchOutcome:
        del model
        local_path = resolve_local_path(
            value,
            cwd=self.cwd,
            allow_local_files=self.settings.allow_local_files,
        )
        if local_path is not None:
            return await self._local_file(
                local_path,
                source=value,
                prompt=prompt,
                timestamp=timestamp,
                frames=frames,
            )

        video_id = _youtube_video_id(value)
        if video_id:
            return await self._youtube(
                value,
                video_id,
                prompt=prompt,
                timestamp=timestamp,
                frames=frames,
            )

        repo = _github_repo(value)
        if repo:
            return await self._github_repository(value, repo, force_clone=force_clone)

        response = await self._safe_get(value)
        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type or urlparse(value).path.lower().endswith(".pdf"):
            title, content = await asyncio.to_thread(_extract_pdf, response.content)
        elif (
            content_type.startswith("video/")
            or Path(urlparse(value).path).suffix in VIDEO_SUFFIXES
        ):
            return await self._downloaded_video(
                value,
                response.content,
                prompt=prompt,
                timestamp=timestamp,
                frames=frames,
            )
        else:
            title, content = await asyncio.to_thread(_extract_html, response.content, url=value)
        return FetchOutcome(
            extracted=ExtractedContent(
                url=value,
                title=title,
                content=content[: self.settings.max_stored_content],
            )
        )

    async def _safe_get(self, url: str) -> httpx.Response:
        current = url
        for _ in range(self.settings.max_redirects + 1):
            await validate_public_url(
                current,
                allow_private_networks=self.settings.allow_private_networks,
            )
            response = await self.client.get(current, follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("Redirect response did not include a location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            if len(response.content) > self.settings.max_response_bytes:
                raise ValueError(
                    f"Response exceeds {self.settings.max_response_bytes} byte limit"
                )
            return response
        raise RuntimeError(f"Too many redirects (maximum {self.settings.max_redirects})")

    async def _local_file(
        self,
        path: Path,
        *,
        source: str,
        prompt: str | None,
        timestamp: str | None,
        frames: int | None,
    ) -> FetchOutcome:
        exists, is_file = await asyncio.to_thread(lambda: (path.exists(), path.is_file()))
        if not exists or not is_file:
            raise ValueError(f"File not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            body = await asyncio.to_thread(path.read_bytes)
            title, content = await asyncio.to_thread(_extract_pdf, body)
            return FetchOutcome(
                extracted=ExtractedContent(url=source, title=title, content=content)
            )
        if suffix in VIDEO_SUFFIXES:
            return await self._video_file(
                path,
                source=source,
                prompt=prompt,
                timestamp=timestamp,
                frames=frames,
            )
        if suffix not in TEXT_SUFFIXES:
            raise ValueError(f"Unsupported local file type: {suffix or '(none)'}")
        content = await asyncio.to_thread(_read_local_text, path)
        return FetchOutcome(
            extracted=ExtractedContent(url=source, title=path.name, content=content)
        )

    async def _youtube(
        self,
        url: str,
        video_id: str,
        *,
        prompt: str | None,
        timestamp: str | None,
        frames: int | None,
    ) -> FetchOutcome:
        def transcript() -> str:
            fetched = YouTubeTranscriptApi().fetch(video_id)
            return "\n".join(item.text for item in fetched)

        try:
            text = await asyncio.to_thread(transcript)
        except Exception as error:
            text = f"Transcript unavailable: {error}"
        heading = f"Requested focus: {prompt}\n\n" if prompt else ""
        images: tuple[ExtractedImage, ...] = ()
        duration = None
        if timestamp or frames:
            binary = shutil.which("yt-dlp")
            if binary is None:
                raise RuntimeError("yt-dlp is required for YouTube frame extraction")
            with tempfile.TemporaryDirectory(prefix="tau-web-youtube-") as temp_dir:
                output = Path(temp_dir) / "video.%(ext)s"
                process = await asyncio.create_subprocess_exec(
                    binary,
                    "--no-playlist",
                    "-f",
                    "bestvideo[height<=720]+bestaudio/best[height<=720]",
                    "-o",
                    str(output),
                    url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await _communicate(process)
                if process.returncode != 0:
                    raise RuntimeError(
                        f"yt-dlp failed: {stderr.decode(errors='replace')[-500:]}"
                    )
                video_paths = await asyncio.to_thread(
                    lambda: list(Path(temp_dir).glob("video.*"))
                )
                if not video_paths:
                    raise RuntimeError("yt-dlp did not create a video file")
                video_path = video_paths[0]
                images, duration = await self._video_images(
                    video_path,
                    timestamp=timestamp,
                    frames=frames,
                )
        return FetchOutcome(
            extracted=ExtractedContent(
                url=url,
                title=f"YouTube video {video_id}",
                content=f"{heading}# Transcript\n\n{text}"[
                    : self.settings.max_stored_content
                ],
            ),
            images=images,
            duration=duration,
        )

    async def _github_repository(
        self,
        url: str,
        repo: tuple[str, str],
        *,
        force_clone: bool,
    ) -> FetchOutcome:
        del force_clone
        git = shutil.which("git")
        if git is None:
            raise RuntimeError("git is required for GitHub repository extraction")
        with tempfile.TemporaryDirectory(prefix="tau-web-github-") as temp_dir:
            target = Path(temp_dir) / "repo"
            process = await asyncio.create_subprocess_exec(
                git,
                "clone",
                "--depth",
                "1",
                "--filter=blob:limit=1m",
                "--single-branch",
                url,
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await _communicate(process)
            if process.returncode != 0:
                raise RuntimeError(f"git clone failed: {stderr.decode(errors='replace')[-500:]}")
            content = await asyncio.to_thread(
                _collect_repository_content,
                target,
                max_chars=self.settings.max_stored_content,
            )
        owner, name = repo
        return FetchOutcome(
            extracted=ExtractedContent(
                url=url,
                title=f"{owner}/{name}",
                content=content,
            )
        )

    async def _downloaded_video(
        self,
        url: str,
        body: bytes,
        *,
        prompt: str | None,
        timestamp: str | None,
        frames: int | None,
    ) -> FetchOutcome:
        suffix = Path(urlparse(url).path).suffix or ".mp4"
        with tempfile.TemporaryDirectory(prefix="tau-web-video-") as temp_dir:
            path = Path(temp_dir) / f"video{suffix}"
            path.write_bytes(body)
            return await self._video_file(
                path,
                source=url,
                prompt=prompt,
                timestamp=timestamp,
                frames=frames,
            )

    async def _video_file(
        self,
        path: Path,
        *,
        source: str,
        prompt: str | None,
        timestamp: str | None,
        frames: int | None,
    ) -> FetchOutcome:
        images, duration = await self._video_images(path, timestamp=timestamp, frames=frames)
        focus = f"\nRequested focus: {prompt}" if prompt else ""
        return FetchOutcome(
            extracted=ExtractedContent(
                url=source,
                title=path.name,
                content=(
                    f"Video file: {path.name}\n"
                    f"Duration: {duration or 'unknown'} seconds{focus}"
                ),
            ),
            images=images,
            duration=duration,
        )

    async def _video_images(
        self,
        path: Path,
        *,
        timestamp: str | None,
        frames: int | None,
    ) -> tuple[tuple[ExtractedImage, ...], float | None]:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            raise RuntimeError("ffmpeg and ffprobe are required for video extraction")
        probe = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await _communicate(probe)
        try:
            duration = float(stdout.decode().strip())
        except ValueError:
            duration = None

        requested_frames = frames or 1
        seek = timestamp.split("-", 1)[0] if timestamp else None
        with tempfile.TemporaryDirectory(prefix="tau-web-frames-") as temp_dir:
            pattern = Path(temp_dir) / "frame-%03d.jpg"
            command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
            if seek:
                command.extend(["-ss", seek])
            command.extend(["-i", str(path)])
            if not seek and duration and requested_frames > 1:
                command.extend(["-vf", f"fps={requested_frames / max(duration, 1):.8f}"])
            elif requested_frames > 1:
                command.extend(["-vf", "fps=1/5"])
            command.extend(["-frames:v", str(requested_frames), "-q:v", "3", str(pattern)])
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await _communicate(process)
            if process.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[-500:]}")
            images = await asyncio.to_thread(_encode_frames, temp_dir)
        return images, duration


__all__ = ["ContentExtractor", "ExtractedImage", "FetchOutcome"]
