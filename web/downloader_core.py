"""Shared yt-dlp configuration for the CLI and web interface."""

from pathlib import Path


def base_ydl_opts(cookie_file: str | Path | None = None) -> dict:
    """Return common network and retry options for yt-dlp."""
    opts = {
        "quiet": True,
        "no_warnings": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "geo_bypass": True,
        "ignoreerrors": False,
    }

    if cookie_file:
        cookies = Path(cookie_file)
        if cookies.is_file() and cookies.stat().st_size > 10:
            opts["cookiefile"] = str(cookies)
    return opts


def format_selector(height: str | int | None = None) -> str:
    """Prefer separate video/audio streams and accept other containers/codecs."""
    if height is not None and str(height).strip():
        try:
            max_height = int(height)
        except (TypeError, ValueError):
            max_height = 0
        if max_height > 0:
            return (
                f"bestvideo[height<={max_height}]+bestaudio/"
                f"best[height<={max_height}]"
            )
    return "bestvideo+bestaudio/best"
