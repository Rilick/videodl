#!/usr/bin/env python3
"""
Downloader para sites de streaming portugueses.
Suporta RTP Play, TVI Player, e outros suportados pelo yt-dlp.

Uso:
  python download.py <url>
  python download.py <url1> <url2> <url3>
  python download.py -f urls.txt
  python download.py <url> -o /caminho/para/pasta
  python download.py --list-formats <url>

Formato do ficheiro de saída:
  Serie.Nome.Titulo.Episodio.S01E01.1080p.RPLAY.WEB-DL.AAC.2.0.H.264.mkv
"""

import argparse
import re
import sys
import urllib.request
from pathlib import Path

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from web.downloader_core import base_ydl_opts, format_selector
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

console = Console()

DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "streaming"

# ---------------------------------------------------------------------------
# Service tag mapping
# ---------------------------------------------------------------------------

_EXTRACTOR_TAGS: dict[str, str] = {
    "rtpplay":      "RPLAY",
    "RTPPlay":      "RPLAY",
    "tviplayer":    "TVIP",
    "TVIPlayer":    "TVIP",
}

_URL_TAGS: list[tuple[str, str]] = [
    ("rtp.pt",             "RPLAY"),
    ("tviplayer.iol.pt",   "TVIP"),
    ("iol.pt",             "TVIP"),
    ("sic.pt",             "SIC"),
    ("sicnoticias.pt",     "SICN"),
    ("tvcine.pt",          "TVCINE"),
]


def _service_tag(info: dict) -> str:
    extractor = info.get("extractor_key", "") or info.get("extractor", "")
    if extractor in _EXTRACTOR_TAGS:
        return _EXTRACTOR_TAGS[extractor]
    url = info.get("webpage_url", "") or info.get("url", "")
    for pattern, tag in _URL_TAGS:
        if pattern in url:
            return tag
    return "WEB"


# ---------------------------------------------------------------------------
# Codec normalisation
# ---------------------------------------------------------------------------

def _norm_vcodec(raw: str) -> str:
    if not raw or raw == "none":
        return ""
    v = raw.lower()
    if v.startswith("avc") or "h264" in v or "x264" in v:
        return "H.264"
    if v.startswith("hev") or v.startswith("hvc") or "hevc" in v or "h265" in v or "x265" in v:
        return "H.265"
    if v.startswith("av0") or v == "av1":
        return "AV1"
    if v.startswith("vp9"):
        return "VP9"
    if v.startswith("vp8"):
        return "VP8"
    return raw.upper()


def _norm_acodec(raw: str) -> str:
    if not raw or raw == "none":
        return ""
    a = raw.lower()
    if "mp4a" in a or a.startswith("aac"):
        return "AAC"
    if a in ("ac-3", "ac3") or a.startswith("ac3"):
        return "AC3"
    if a in ("ec-3", "eac3") or a.startswith("eac"):
        return "EAC3"
    if a == "opus":
        return "Opus"
    if a.startswith("mp3"):
        return "MP3"
    if a == "flac":
        return "FLAC"
    if a == "vorbis":
        return "Vorbis"
    return raw.upper()


def _norm_channels(ch) -> str:
    _map = {1: "1.0", 2: "2.0", 3: "3.0", 4: "4.0", 6: "5.1", 7: "6.1", 8: "7.1"}
    if ch is None:
        return ""
    try:
        return _map.get(int(ch), f"{ch}ch")
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Filename building
# ---------------------------------------------------------------------------

def _sanitize(text: str) -> str:
    """Replace spaces/separators with dots and strip invalid filesystem chars."""
    if not text:
        return ""
    # Normalize unicode separators and whitespace
    text = re.sub(r"[\s\-–—_]+", ".", text)
    # Remove characters not allowed in filenames
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    # Collapse multiple dots
    text = re.sub(r"\.{2,}", ".", text)
    return text.strip(".")


def _get_codecs(info: dict) -> tuple[str, str, str | None]:
    """
    Return (vcodec_raw, acodec_raw, channels) preferring info from the
    individually selected video/audio formats when available.
    """
    vcodec = ""
    acodec = ""
    channels = None

    for fmt in info.get("requested_formats") or []:
        v = fmt.get("vcodec", "")
        a = fmt.get("acodec", "")
        if v and v != "none" and not vcodec:
            vcodec = v
        if a and a != "none" and not acodec:
            acodec = a
            channels = fmt.get("audio_channels")

    # Fallbacks from the top-level info dict
    if not vcodec:
        vcodec = info.get("vcodec", "")
    if not acodec:
        acodec = info.get("acodec", "")
    if channels is None:
        channels = info.get("audio_channels")

    return vcodec, acodec, channels


def build_scene_filename(info: dict) -> str:
    """
    Build a scene-style filename:
      Serie.Nome.Titulo.Episodio.S01E01.1080p.RPLAY.WEB-DL.AAC.2.0.H.264.mkv
    """
    # ---- show / series name ------------------------------------------------
    series = (
        info.get("series")
        or info.get("playlist_title")
        or info.get("uploader")
        or "Unknown"
    )

    # ---- episode title -----------------------------------------------------
    title = info.get("title", "")
    # Avoid repeating the series name inside the episode title
    if series and title.lower().startswith(series.lower()):
        title = title[len(series):].lstrip(" :-")

    # ---- season / episode numbers ------------------------------------------
    season  = info.get("season_number")
    episode = info.get("episode_number")

    if season and episode:
        se_tag = f"S{int(season):02d}E{int(episode):02d}"
    elif episode:
        se_tag = f"E{int(episode):02d}"
    else:
        se_tag = ""

    # ---- resolution --------------------------------------------------------
    height = info.get("height")
    resolution = f"{height}p" if height else ""

    # ---- service / source --------------------------------------------------
    service = _service_tag(info)

    # ---- codecs ------------------------------------------------------------
    vcodec_raw, acodec_raw, channels = _get_codecs(info)
    vcodec   = _norm_vcodec(vcodec_raw)
    acodec   = _norm_acodec(acodec_raw)
    ch_str   = _norm_channels(channels)

    # ---- extension ---------------------------------------------------------
    ext = info.get("ext", "mp4")

    # ---- assemble ----------------------------------------------------------
    parts = []

    series_clean = _sanitize(series)
    if series_clean:
        parts.append(series_clean)

    title_clean = _sanitize(title)
    if title_clean and title_clean != series_clean:
        parts.append(title_clean)

    if se_tag:
        parts.append(se_tag)

    if resolution:
        parts.append(resolution)

    parts.append(service)
    parts.append("WEB-DL")

    if acodec:
        parts.append(acodec)
    if ch_str:
        parts.append(ch_str)
    if vcodec:
        parts.append(vcodec)

    return ".".join(filter(None, parts)) + f".{ext}"


# ---------------------------------------------------------------------------
# Post-processor: rename to scene filename
# ---------------------------------------------------------------------------

class SceneNamingPP(PostProcessor):
    """Renames the downloaded file to the scene naming convention."""

    def __init__(self, output_dir: Path):
        super().__init__()
        self.output_dir = output_dir

    def run(self, info: dict):
        src = Path(info.get("filepath", ""))
        if not src.exists():
            return [], info

        filename = build_scene_filename(info)
        dst = self.output_dir / filename

        if src == dst:
            return [], info

        if dst.exists():
            self._downloader.to_screen(
                f"[scene-naming] Já existe, a saltar renomeação: {dst.name}"
            )
            return [], info

        src.rename(dst)
        info["filepath"] = str(dst)
        console.print(f"  [bold green]→[/bold green] {filename}")
        return [], info


# ---------------------------------------------------------------------------
# Progress & logging
# ---------------------------------------------------------------------------

class _RichLogger:
    def debug(self, msg):
        if msg.startswith("[debug]") or msg.startswith("[download]"):
            return
        console.print(f"[dim]{msg}[/dim]")

    def info(self, msg):
        console.print(f"[dim]{msg}[/dim]")

    def warning(self, msg):
        console.print(f"[yellow]AVISO:[/yellow] {msg}")

    def error(self, msg):
        console.print(f"[red]ERRO:[/red] {msg}")


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    )


def _make_ydl_opts(output_dir: Path, scene_pp: SceneNamingPP) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    progress = _make_progress()
    task_id: list = [None]  # mutable container for nonlocal-like usage in nested fn

    def progress_hook(d: dict) -> None:
        status = d.get("status")

        if status == "downloading":
            total     = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            fragment  = d.get("fragment_index")
            frag_count = d.get("fragment_count")

            desc = Path(d.get("filename", "")).name[:55]
            if frag_count:
                desc = f"{desc} [{fragment}/{frag_count}]"

            if task_id[0] is None:
                task_id[0] = progress.add_task(desc, total=total or None)
                progress.start()
            else:
                progress.update(task_id[0], description=desc, completed=downloaded, total=total or None)

        elif status == "finished":
            if task_id[0] is not None:
                t = progress.tasks[task_id[0]]
                progress.update(task_id[0], completed=t.total or 1)
                progress.stop()
                task_id[0] = None

        elif status == "error":
            if task_id[0] is not None:
                progress.stop()
                task_id[0] = None

    return {
        **base_ydl_opts(Path(__file__).resolve().parent / "cookies.txt"),
        # Temp name during download — postprocessor will rename
        "outtmpl": str(output_dir / ".%(extractor_key)s.%(id)s.%(ext)s"),
        "format": format_selector(),
        "merge_output_format": "mkv",
        "nooverwrites": True,
        "continuedl": True,
        "quiet": True,
        "no_warnings": False,
        "logger": _RichLogger(),
        "progress_hooks": [progress_hook],
        "postprocessors": [
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "post_hooks": [],
    }


# ---------------------------------------------------------------------------
# Download orchestration
# ---------------------------------------------------------------------------

def _resolve_abola_url(url: str) -> str:
    """Extrai o ID do JWPlayer de uma página abola.pt e devolve o URL da API do JWPlayer."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="ignore")

    m = re.search(r'data-jw-media="([^"]+)"', html)
    if not m:
        m = re.search(r"videoId\s*=\s*['\"]([^'\"]+)['\"]", html)
    if not m:
        raise ValueError(f"Não foi possível encontrar o ID do vídeo JWPlayer em: {url}")

    media_id = m.group(1)
    return f"https://cdn.jwplayer.com/v2/media/{media_id}"


def resolve_url(url: str) -> str:
    """Resolve URLs de sites que requerem extracção prévia do URL do vídeo."""
    if "abola.pt/" in url:
        resolved = _resolve_abola_url(url)
        console.print(f"  [dim]abola.pt → JWPlayer media {resolved.split('/')[-1]}[/dim]")
        return resolved
    return url


def load_urls_from_file(path: Path) -> list[str]:
    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def download(urls: list[str], output_dir: Path) -> None:
    console.print(
        Panel(
            f"[bold]A descarregar [cyan]{len(urls)}[/cyan] "
            f"{'URL' if len(urls) == 1 else 'URLs'}[/bold]\n"
            f"Destino: [dim]{output_dir}[/dim]",
            title="[bold blue]Downloader PT[/bold blue]",
            expand=False,
        )
    )

    scene_pp = SceneNamingPP(output_dir)
    opts = _make_ydl_opts(output_dir, scene_pp)

    ok: list[str] = []
    failed: list[tuple[str, str]] = []

    with yt_dlp.YoutubeDL(opts) as ydl:
        # Register the rename postprocessor after FFmpegMerger
        ydl.add_post_processor(scene_pp, when="after_move")

        for i, url in enumerate(urls, 1):
            console.rule(f"[dim]({i}/{len(urls)})[/dim] {url[:90]}")
            try:
                resolved = resolve_url(url)
                ret = ydl.download([resolved])
                if ret == 0:
                    ok.append(url)
                else:
                    failed.append((url, "yt-dlp terminou com erro."))
            except Exception as exc:
                console.print(f"[red]Falhou:[/red] {exc}")
                failed.append((url, str(exc)))

    _print_summary(ok, failed)


def _print_summary(ok: list[str], failed: list[tuple[str, str]]) -> None:
    console.line()
    table = Table(title="Resumo", show_header=False, expand=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("[green]Concluídos[/green]", str(len(ok)))
    table.add_row("[red]Falhados[/red]", str(len(failed)))
    console.print(table)

    if failed:
        console.print("\n[yellow]URLs que falharam:[/yellow]")
        for url, error in failed:
            console.print(f"  [dim]{url}[/dim]")
            if error:
                console.print(f"    [red]{error}[/red]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Descarrega vídeos de sites suportados pelo yt-dlp, incluindo RTP Play e TVI Player.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "urls",
        nargs="*",
        metavar="URL",
        help="Um ou mais URLs para descarregar",
    )
    parser.add_argument(
        "-f", "--file",
        metavar="FICHEIRO",
        help="Ficheiro .txt com um URL por linha (# para comentários)",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="PASTA",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Pasta de destino (padrão: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--list-formats",
        metavar="URL",
        help="Listar formatos disponíveis para um URL sem descarregar",
    )
    parser.add_argument(
        "--dry-run",
        metavar="URL",
        help="Mostrar o nome de ficheiro que seria gerado, sem descarregar",
    )

    args = parser.parse_args()

    if args.list_formats:
        opts = base_ydl_opts(Path(__file__).resolve().parent / "cookies.txt")
        opts.update({"listformats": True, "quiet": False})
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(resolve_url(args.list_formats), download=False)
        return

    if args.dry_run:
        opts = base_ydl_opts(Path(__file__).resolve().parent / "cookies.txt")
        opts.update({"quiet": True, "logger": _RichLogger()})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(resolve_url(args.dry_run), download=False)
            if info:
                name = build_scene_filename(info)
                console.print(f"\n[bold]Nome gerado:[/bold] [cyan]{name}[/cyan]")
        return

    all_urls = list(args.urls)

    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            console.print(f"[red]Ficheiro não encontrado:[/red] {args.file}")
            sys.exit(1)
        all_urls.extend(load_urls_from_file(file_path))

    if not all_urls:
        parser.print_help()
        sys.exit(1)

    download(all_urls, Path(args.output))


if __name__ == "__main__":
    main()
