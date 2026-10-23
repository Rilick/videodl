import json
import mimetypes
import re
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import yt_dlp
from flask import Flask, Response, render_template, request

try:
    from .downloader_core import base_ydl_opts, format_selector
except ImportError:  # Support running app.py directly in the container.
    from downloader_core import base_ydl_opts, format_selector

app = Flask(__name__)
jobs: dict[str, dict] = {}

COOKIES_FILE = "/app/cookies.txt"


# ── URL resolution ──────────────────────────────────────────────────────────────

def _resolve_abola(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="ignore")
    m = re.search(r'data-jw-media="([^"]+)"', html) or re.search(r"videoId\s*=\s*['\"]([^'\"]+)['\"]", html)
    if not m:
        raise ValueError("Não foi possível encontrar o vídeo JWPlayer nesta página.")
    return f"https://cdn.jwplayer.com/v2/media/{m.group(1)}"


def resolve_url(url: str) -> str:
    if "abola.pt/" in url:
        try:
            return _resolve_abola(url)
        except Exception:
            pass  # fall through to yt-dlp
    return url


# ── yt-dlp base options ─────────────────────────────────────────────────────────

def _base_opts() -> dict:
    return base_ydl_opts(COOKIES_FILE)


# ── Error cleanup ───────────────────────────────────────────────────────────────

def _clean_error(msg: str) -> str:
    msg = re.sub(r"^ERROR:\s*", "", msg)
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg[:1200]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _fmt_speed(bps) -> str:
    if not bps:
        return ""
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{int(bps)} B/s"


def _fmt_eta(s) -> str:
    if s is None:
        return ""
    s = int(s)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _safe_name(title: str, ext: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", title or "video") + f".{ext}"


# ── Background download worker ──────────────────────────────────────────────────

def _worker(job_id: str, url: str, height: str | None) -> None:
    job = jobs[job_id]
    try:
        resolved = resolve_url(url)
        tmp = tempfile.mkdtemp()
        last_fname: list[str | None] = [None]
        stream_n: list[int] = [0]

        def hook(d: dict) -> None:
            if d["status"] == "downloading":
                fname = d.get("tmpfilename") or d.get("filename", "")
                if fname != last_fname[0]:
                    if last_fname[0] is not None:
                        stream_n[0] += 1
                    last_fname[0] = fname
                fi, fc = d.get("fragment_index"), d.get("fragment_count")
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                pct = round(fi / fc * 100, 1) if (fi and fc) else (round(done / tot * 100, 1) if tot else 0)
                job.update({
                    "status": "downloading",
                    "phase": "audio" if stream_n[0] else "video",
                    "progress": pct,
                    "speed": _fmt_speed(d.get("speed")),
                    "eta": _fmt_eta(d.get("eta")),
                })
            elif d["status"] == "finished":
                job.update({"status": "processing", "phase": "merge", "progress": 100, "speed": "", "eta": ""})

        opts = {
            **_base_opts(),
            "outtmpl": str(Path(tmp) / "video.%(ext)s"),
            "format": format_selector(height),
            "merge_output_format": "mkv",
            "progress_hooks": [hook],
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(resolved, download=True)

        files = list(Path(tmp).glob("video.*"))
        if not files:
            raise FileNotFoundError("Ficheiro de saída não encontrado.")
        out = files[0]
        title = (info.get("title") if info else None) or "video"
        job.update({
            "status": "done",
            "progress": 100,
            "filepath": str(out),
            "filename": _safe_name(title, out.suffix.lstrip(".")),
        })
    except Exception as exc:
        job.update({"status": "error", "error": _clean_error(str(exc))})


# ── Routes ───────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/info", methods=["POST"])
def info():
    url = (request.form.get("url") or "").strip()
    if not url:
        return {"error": "URL em falta."}, 400
    try:
        resolved = resolve_url(url)
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            data = ydl.extract_info(resolved, download=False)
        heights = sorted(
            {f["height"] for f in (data.get("formats") or [])
             if f.get("height") and f.get("vcodec") not in (None, "none")},
            reverse=True,
        )
        return {
            "title": data.get("title", ""),
            "thumbnail": data.get("thumbnail", ""),
            "duration": data.get("duration"),
            "resolutions": heights,
            "cookies_loaded": Path(COOKIES_FILE).is_file() and Path(COOKIES_FILE).stat().st_size > 10,
        }
    except Exception as e:
        err = _clean_error(str(e))
        needs_auth = any(k in str(e).lower() for k in ("login", "cookies", "authentication", "sign in", "logged"))
        return {"error": err, "needs_auth": needs_auth}, 400


@app.route("/start", methods=["POST"])
def start():
    url = (request.form.get("url") or "").strip()
    height = (request.form.get("height") or "").strip() or None
    if not url:
        return {"error": "URL em falta."}, 400
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "starting", "phase": "video", "progress": 0,
        "speed": "", "eta": "", "filepath": None, "filename": None, "error": None,
    }
    threading.Thread(target=_worker, args=(job_id, url, height), daemon=True).start()
    return {"job_id": job_id}


@app.route("/progress/<job_id>")
def progress_stream(job_id: str):
    def gen():
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'status': 'error', 'error': 'Job não encontrado'})}\n\n"
                break
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.4)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/file/<job_id>")
def get_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return "Ficheiro não disponível.", 404
    path = Path(job["filepath"])
    name = job["filename"]
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".mkv":
        mime = "video/x-matroska"

    def gen():
        try:
            with open(path, "rb") as f:
                while chunk := f.read(2 * 1024 * 1024):
                    yield chunk
        finally:
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            except Exception:
                pass
            jobs.pop(job_id, None)

    return Response(gen(), mimetype=mime, headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Length": str(path.stat().st_size),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
