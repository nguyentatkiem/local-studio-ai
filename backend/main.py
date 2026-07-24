"""
Local Studio - Backend AI service
100% local: FastAPI + FFmpeg + faster-whisper + auto-editor + Real-ESRGAN (ncnn-vulkan)
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Request, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------- paths
BACKEND_DIR = Path(__file__).resolve().parent
ROOT = BACKEND_DIR.parent
WORKSPACE = ROOT / "workspace"
UPLOADS = WORKSPACE / "uploads"
OUTPUTS = WORKSPACE / "outputs"
TMP = WORKSPACE / "tmp"
BINARIES = ROOT / "binaries"
FRONTEND_DIST = ROOT / "frontend" / "dist"
REALESRGAN_EXE = BINARIES / "realesrgan" / "realesrgan-ncnn-vulkan.exe"

for d in (UPLOADS, OUTPUTS, TMP):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- app
app = FastAPI(title="Local Studio API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765",
                   "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---------------------------------------------------------------- auth (admin)
AUTH_FILE = BACKEND_DIR / "auth_config.json"
SESSION_TTL = 7 * 24 * 3600
SESSIONS: dict = {}          # token -> expiry epoch
LOGIN_FAILS: list = []       # timestamps of failed attempts (rate limit)


def _load_auth() -> dict:
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    salt = secrets.token_hex(16)
    pw = secrets.token_urlsafe(12)
    cfg = {"salt": salt,
           "hash": hashlib.sha256((salt + pw).encode()).hexdigest()}
    AUTH_FILE.write_text(json.dumps(cfg), encoding="utf-8")
    (BACKEND_DIR / "ADMIN-PASSWORD.txt").write_text(
        "Mat khau admin Local Studio (giu kin, chi hien 1 lan o day):\n\n"
        + pw + "\n", encoding="utf-8")
    return cfg


AUTH = _load_auth()


def _check_password(pw: str) -> bool:
    digest = hashlib.sha256((AUTH["salt"] + pw).encode()).hexdigest()
    return hmac.compare_digest(digest, AUTH["hash"])


def _session_ok(request: Request) -> bool:
    tok = request.cookies.get("ls_session")
    return bool(tok and SESSIONS.get(tok, 0) > time.time())


PUBLIC_PATHS = {"/", "/index.html", "/api/login", "/api/auth-status", "/favicon.ico"}


@app.middleware("http")
async def auth_and_headers(request: Request, call_next):
    p = request.url.path
    is_public = p in PUBLIC_PATHS or p.startswith("/assets/")
    if not is_public and not _session_ok(request):
        resp = JSONResponse({"detail": "Chưa đăng nhập"}, status_code=401)
    else:
        resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if p.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


class LoginReq(BaseModel):
    password: str


@app.post("/api/login")
def login(req: LoginReq):
    now = time.time()
    LOGIN_FAILS[:] = [t for t in LOGIN_FAILS if now - t < 300]
    if len(LOGIN_FAILS) >= 10:
        raise HTTPException(429, "Quá nhiều lần thử sai — đợi 5 phút")
    if not _check_password(req.password):
        LOGIN_FAILS.append(now)
        raise HTTPException(401, "Sai mật khẩu")
    # purge expired sessions
    for k in [k for k, v in SESSIONS.items() if v < now]:
        SESSIONS.pop(k, None)
    tok = secrets.token_urlsafe(32)
    SESSIONS[tok] = now + SESSION_TTL
    resp = JSONResponse({"ok": True})
    resp.set_cookie("ls_session", tok, httponly=True, samesite="lax",
                    max_age=SESSION_TTL, path="/")
    return resp


@app.get("/api/auth-status")
def auth_status(request: Request):
    return {"auth_required": True, "authenticated": _session_ok(request)}


@app.post("/api/logout")
def logout(request: Request):
    tok = request.cookies.get("ls_session")
    if tok:
        SESSIONS.pop(tok, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("ls_session", path="/")
    return resp


def safe_upload_path(name: str) -> Path:
    """Chặn path traversal: tên tệp phải nằm gọn trong UPLOADS."""
    if not name or name in (".", "..") or re.search(r"[\\/]", name):
        raise HTTPException(400, "Tên tệp không hợp lệ")
    p = UPLOADS / name
    try:
        ok = p.resolve().parent == UPLOADS.resolve()
    except OSError:
        ok = False
    if not ok:
        raise HTTPException(400, "Tên tệp không hợp lệ")
    return p

# ---------------------------------------------------------------- helpers
def _run(cmd, **kw):
    """Run a subprocess, capture text output safely on Windows."""
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", **kw
    )


def ffprobe(path: Path) -> dict:
    r = _run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def media_info(path: Path) -> dict:
    p = ffprobe(path)
    fmt = p.get("format", {})
    v = next((s for s in p.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in p.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 30.0
    if v and v.get("r_frame_rate") and "/" in v["r_frame_rate"]:
        num, den = v["r_frame_rate"].split("/")
        if float(den) > 0:
            fps = float(num) / float(den)
    return {
        "duration": float(fmt.get("duration", 0) or 0),
        "size": int(fmt.get("size", 0) or 0),
        "width": v.get("width") if v else None,
        "height": v.get("height") if v else None,
        "fps": round(fps, 3),
        "has_audio": a is not None,
        "vcodec": v.get("codec_name") if v else None,
    }


def gpu_info() -> Optional[dict]:
    try:
        r = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                  "--format=csv,noheader,nounits"])
        if r.returncode == 0 and r.stdout.strip():
            name, total, used = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            return {"name": name, "vram_total_mb": int(total), "vram_used_mb": int(used)}
    except FileNotFoundError:
        pass
    return None


def run_ffmpeg_progress(args: list, duration: float, on_progress):
    """Run ffmpeg with -progress pipe:1 and report percent via callback."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats"] + args
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms=") and duration > 0:
            try:
                ms = int(line.split("=")[1]) / 1_000_000
                on_progress(min(99, ms / duration * 100))
            except ValueError:
                pass
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err[-800:]}")


def unique_out(stem: str, suffix: str) -> Path:
    """Collision-free output path in OUTPUTS."""
    base = re.sub(r"[^\w\-.]+", "_", stem)
    p = OUTPUTS / f"{base}{suffix}"
    i = 1
    while p.exists():
        p = OUTPUTS / f"{base}_{i}{suffix}"
        i += 1
    return p


def srt_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ---------------------------------------------------------------- whisper (lazy)
_whisper_models: dict = {}
_whisper_lock = threading.Lock()


def get_whisper(size: str):
    from faster_whisper import WhisperModel
    with _whisper_lock:
        if size not in _whisper_models:
            _whisper_models[size] = WhisperModel(size, device="cpu", compute_type="int8")
        return _whisper_models[size]


# ---------------------------------------------------------------- job queue
JOBS: dict = {}
JOBS_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _set(job_id: str, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw)


def _job_wrapper(job_id: str, fn, *args):
    _set(job_id, status="running", started=time.time())
    try:
        outputs = fn(job_id, *args)
        _set(job_id, status="done", progress=100, outputs=outputs,
             finished=time.time(), message="Hoàn thành")
    except Exception as e:  # noqa: BLE001 - surface any job error to UI
        _set(job_id, status="error", error=str(e)[-1000:], finished=time.time())


def submit_job(jtype: str, input_name: str, fn, *args) -> dict:
    job_id = uuid.uuid4().hex[:10]
    job = {
        "id": job_id, "type": jtype, "input": input_name,
        "status": "queued", "progress": 0, "message": "Đang chờ...",
        "outputs": [], "error": None, "created": time.time(),
        "started": None, "finished": None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    EXECUTOR.submit(_job_wrapper, job_id, fn, *args)
    return job


def out_entry(p: Path) -> dict:
    return {"name": p.name, "url": f"/files/outputs/{p.name}",
            "size": p.stat().st_size if p.exists() else 0}


# ---------------------------------------------------------------- subtitles: styles
# Màu ASS = &HAABBGGRR (thứ tự BGR!)
SUB_EFFECTS = {
    "classic": {  # trắng viền đen cơ bản
        "primary": "&H00FFFFFF", "outline_c": "&H00000000", "back": "&H80000000",
        "bold": 0, "borderstyle": 1, "outline": 2.0, "shadow": 0.5, "prefix": "",
    },
    "bold": {  # kiểu MrBeast: chữ khối dày, bóng đậm
        "primary": "&H00FFFFFF", "outline_c": "&H00000000", "back": "&H80000000",
        "bold": 1, "borderstyle": 1, "outline": 3.5, "shadow": 1.5, "prefix": "",
    },
    "yellow": {  # kiểu Hormozi: vàng đậm nổi bật
        "primary": "&H0000FFFF", "outline_c": "&H00000000", "back": "&H80000000",
        "bold": 1, "borderstyle": 1, "outline": 3.0, "shadow": 1.0, "prefix": "",
    },
    "karaoke": {  # từng từ sáng vàng theo giọng nói
        "primary": "&H00FFFFFF", "outline_c": "&H00000000", "back": "&H80000000",
        "bold": 1, "borderstyle": 1, "outline": 2.5, "shadow": 1.0, "prefix": "",
        "highlight": "&H0000FFFF",
    },
    "neon": {  # phát sáng cyan
        "primary": "&H00FFFFFF", "outline_c": "&H00FFFF00", "back": "&H80000000",
        "bold": 1, "borderstyle": 1, "outline": 1.5, "shadow": 0, "prefix": r"{\blur4}",
    },
    "box": {  # chữ trắng trên hộp đen
        "primary": "&H00FFFFFF", "outline_c": "&H00000000", "back": "&H00000000",
        "bold": 0, "borderstyle": 3, "outline": 1.0, "shadow": 0, "prefix": "",
    },
}
SUB_SIZES = {"S": 0.045, "M": 0.065, "L": 0.085, "XL": 0.115}
SUB_ALIGN = {"bottom": 2, "center": 5, "top": 8}
SUB_FONTS = ["Arial", "Arial Black", "Impact", "Segoe UI", "Verdana", "Tahoma",
             "Georgia", "Times New Roman", "Comic Sans MS", "Consolas", "Bahnschrift"]


def ass_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    cs = int((sec - int(sec)) * 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def ass_escape(t: str) -> str:
    return t.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def chunk_words(segments, max_words: int, upper: bool):
    """Gom word-level timestamps thành các cụm hiển thị.
    Trả về list chunk: {"start","end","words":[(start,end,text),...]}"""
    words = []
    for seg in segments:
        if getattr(seg, "words", None):
            for w in seg.words:
                t = ass_escape(w.word)
                if t:
                    words.append((w.start, w.end, t.upper() if upper else t))
        else:  # model không trả words -> coi cả câu là 1 "từ"
            t = ass_escape(seg.text)
            if t:
                words.append((seg.start, seg.end, t.upper() if upper else t))
    chunks = []
    cur = []
    for i, w in enumerate(words):
        cur.append(w)
        gap_next = words[i + 1][0] - w[1] if i + 1 < len(words) else 99
        full = max_words > 0 and len(cur) >= max_words
        sentence_end = w[2].rstrip().endswith((".", "?", "!", "…"))
        if full or gap_next > 0.8 or sentence_end:
            chunks.append({"start": cur[0][0], "end": cur[-1][1], "words": cur})
            cur = []
    if cur:
        chunks.append({"start": cur[0][0], "end": cur[-1][1], "words": cur})
    return chunks


def build_ass(chunks, w: int, h: int, font: str, size_key: str,
              effect: str, position: str) -> str:
    fx = SUB_EFFECTS.get(effect, SUB_EFFECTS["classic"])
    size = max(14, int(h * SUB_SIZES.get(size_key, 0.065)))
    align = SUB_ALIGN.get(position, 2)
    marginv = int(h * 0.06)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{fx['primary']},{fx['primary']},"
        f"{fx['outline_c']},{fx['back']},{fx['bold']},0,0,0,100,100,0,0,"
        f"{fx['borderstyle']},{fx['outline']},{fx['shadow']},"
        f"{align},40,40,{marginv},163\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    prefix = fx.get("prefix", "")
    if effect == "karaoke":
        hl = fx["highlight"]
        base = fx["primary"]
        for c in chunks:
            ws = c["words"]
            for i, (ws_i, _we_i, _t) in enumerate(ws):
                ev_start = ws_i
                ev_end = ws[i + 1][0] if i + 1 < len(ws) else c["end"]
                if ev_end <= ev_start:
                    ev_end = ev_start + 0.05
                parts = []
                for j, (_s, _e, t) in enumerate(ws):
                    if j == i:
                        parts.append(rf"{{\c{hl}}}{t}{{\c{base}}}")
                    else:
                        parts.append(t)
                text = prefix + " ".join(parts)
                lines.append(f"Dialogue: 0,{ass_ts(ev_start)},{ass_ts(ev_end)},"
                             f"Default,,0,0,0,,{text}")
    else:
        for c in chunks:
            text = prefix + " ".join(t for _s, _e, t in c["words"])
            lines.append(f"Dialogue: 0,{ass_ts(c['start'])},{ass_ts(c['end'])},"
                         f"Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- job: transcribe
def job_transcribe(job_id: str, src: Path, p: dict):
    model_size = p.get("model", "base")
    language = p.get("language") or None
    burn = bool(p.get("burn", True))
    max_words = int(p.get("max_words", 0))
    font = p.get("font", "Arial")
    if font not in SUB_FONTS:
        font = "Arial"
    effect = p.get("effect", "classic")
    size_key = p.get("size", "M")
    position = p.get("position", "bottom")
    upper = bool(p.get("uppercase", False))

    info = media_info(src)
    dur = info["duration"] or 1
    vw, vh = info["width"] or 1280, info["height"] or 720

    _set(job_id, message=f"Đang nạp model Whisper ({model_size})...")
    model = get_whisper(model_size)

    _set(job_id, message="Đang nhận dạng giọng nói (word-level)...")
    segments, tr_info = model.transcribe(
        str(src), language=language, vad_filter=True, beam_size=5,
        word_timestamps=True,
    )

    seg_list = []
    lines = []
    for seg in segments:  # generator -> chạy thật ở đây
        seg_list.append(seg)
        lines.append(seg.text.strip())
        _set(job_id, progress=min(90, seg.end / dur * 100),
             message=f"Nghe: {seg.text.strip()[:60]}")

    chunks = chunk_words(seg_list, max_words, upper)

    # .srt (phổ thông) + .ass (đầy đủ style) + .txt
    srt_path = unique_out(src.stem, ".srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        if chunks:
            for n, c in enumerate(chunks, 1):
                text = " ".join(t for _s, _e, t in c["words"])
                f.write(f"{n}\n{srt_ts(c['start'])} --> {srt_ts(c['end'])}\n{text}\n\n")
        else:
            f.write("1\n00:00:00,000 --> 00:00:02,000\n[Không phát hiện giọng nói]\n\n")

    ass_path = srt_path.with_suffix(".ass")
    ass_path.write_text(
        build_ass(chunks, vw, vh, font, size_key, effect, position),
        encoding="utf-8",
    )
    txt_path = srt_path.with_suffix(".txt")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    outputs = [out_entry(srt_path), out_entry(ass_path), out_entry(txt_path)]

    if burn and chunks:
        _set(job_id, message="Đang ghi phụ đề vào video...", progress=93)
        burned = unique_out(src.stem + "_sub", ".mp4")
        # cwd trick: tên file .ass tương đối để né escape đường dẫn Windows
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(src), "-vf", f"subtitles={ass_path.name}",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium",
               "-c:a", "copy", str(burned)]
        r = _run(cmd, cwd=str(OUTPUTS))
        if r.returncode != 0:
            raise RuntimeError(f"Burn subtitle failed: {r.stderr[-500:]}")
        outputs.append(out_entry(burned))

    lang = getattr(tr_info, "language", "?")
    _set(job_id, message=f"Xong — ngôn ngữ: {lang}, {len(chunks)} cụm phụ đề "
                         f"({effect}, {font}, tối đa {max_words or 'cả câu'} từ)")
    return outputs


# ---------------------------------------------------------------- job: silence cut
def job_silence_cut(job_id: str, src: Path, margin: float):
    _set(job_id, progress=-1, message="auto-editor đang phân tích & cắt khoảng lặng...")
    out = unique_out(src.stem + "_cut", ".mp4")
    cmd = [sys.executable, "-m", "auto_editor", str(src),
           "--margin", f"{margin}sec", "--no-open", "--output", str(out)]
    r = _run(cmd, cwd=str(TMP))
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"auto-editor failed: {(r.stderr or r.stdout)[-800:]}")
    before = media_info(src)["duration"]
    after = media_info(out)["duration"]
    _set(job_id, message=f"Xong — {before:.1f}s → {after:.1f}s "
                         f"(tiết kiệm {max(0, before - after):.1f}s)")
    return [out_entry(out)]


# ---------------------------------------------------------------- job: upscale
def job_upscale(job_id: str, src: Path, mode: str, scale: int, model: str):
    info = media_info(src)
    dur = info["duration"] or 1

    if mode == "fast" or not REALESRGAN_EXE.exists():
        if mode == "ai" and not REALESRGAN_EXE.exists():
            _set(job_id, message="Không thấy Real-ESRGAN, dùng chế độ nhanh (lanczos)")
        out = unique_out(f"{src.stem}_x{scale}", ".mp4")
        _set(job_id, message=f"Upscale nhanh x{scale} (lanczos)...")
        args = ["-i", str(src),
                "-vf", f"scale=iw*{scale}:ih*{scale}:flags=lanczos",
                "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)]
        run_ffmpeg_progress(args, dur, lambda p: _set(job_id, progress=p))
        return [out_entry(out)]

    # ---- AI mode: frames -> realesrgan-ncnn-vulkan (GPU) -> reassemble
    frames_in = TMP / f"fr_in_{job_id}"
    frames_out = TMP / f"fr_out_{job_id}"
    frames_in.mkdir(exist_ok=True)
    frames_out.mkdir(exist_ok=True)
    try:
        _set(job_id, progress=2, message="Tách khung hình...")
        r = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                  "-i", str(src), str(frames_in / "f_%08d.png")])
        if r.returncode != 0:
            raise RuntimeError(f"Frame extract failed: {r.stderr[-400:]}")
        total = len(list(frames_in.glob("*.png")))
        if total == 0:
            raise RuntimeError("Không tách được khung hình nào")

        _set(job_id, progress=5, message=f"AI upscale {total} khung hình trên GPU ({model} x{scale})...")
        # stderr -> file (KHONG dung PIPE: realesrgan in progress lien tuc,
        # buffer day se lam process treo vinh vien)
        err_log = TMP / f"esrgan_{job_id}.log"
        with open(err_log, "w", encoding="utf-8", errors="replace") as ef:
            proc = subprocess.Popen(
                [str(REALESRGAN_EXE), "-i", str(frames_in), "-o", str(frames_out),
                 "-n", model, "-s", str(scale), "-f", "png"],
                stdout=subprocess.DEVNULL, stderr=ef,
            )
            while proc.poll() is None:
                done = len(list(frames_out.glob("*.png")))
                _set(job_id, progress=5 + done / total * 85,
                     message=f"AI upscale: {done}/{total} khung hình")
                time.sleep(1.5)
        if proc.returncode != 0:
            err = err_log.read_text(encoding="utf-8", errors="replace") if err_log.exists() else ""
            raise RuntimeError(f"Real-ESRGAN failed: {err[-500:]}")
        err_log.unlink(missing_ok=True)

        _set(job_id, progress=92, message="Ghép video + âm thanh...")
        out = unique_out(f"{src.stem}_ai_x{scale}", ".mp4")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-framerate", str(info["fps"]),
               "-i", str(frames_out / "f_%08d.png")]
        if info["has_audio"]:
            cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", str(out)]
        r = _run(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"Reassemble failed: {r.stderr[-400:]}")
        return [out_entry(out)]
    finally:
        shutil.rmtree(frames_in, ignore_errors=True)
        shutil.rmtree(frames_out, ignore_errors=True)


# ---------------------------------------------------------------- job: export preset
PRESETS = {
    "tiktok": {"w": 1080, "h": 1920, "label": "TikTok/Reels/Shorts 9:16"},
    "youtube": {"w": 1920, "h": 1080, "label": "YouTube 16:9 1080p"},
    "square": {"w": 1080, "h": 1080, "label": "Instagram 1:1"},
}


def job_export(job_id: str, src: Path, preset: str):
    p = PRESETS[preset]
    w, h = p["w"], p["h"]
    dur = media_info(src)["duration"] or 1
    out = unique_out(f"{src.stem}_{preset}", ".mp4")
    _set(job_id, message=f"Xuất {p['label']}...")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    args = ["-i", str(src), "-vf", vf, "-c:v", "libx264", "-crf", "20",
            "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr))
    return [out_entry(out)]


# ---------------------------------------------------------------- API
@app.get("/api/health")
def health():
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    try:
        import faster_whisper  # noqa: F401
        whisper_ok = True
    except ImportError:
        whisper_ok = False
    try:
        import auto_editor  # noqa: F401
        ae_ok = True
    except ImportError:
        ae_ok = False
    return {
        "status": "ok",
        "app": "Local Studio",
        "version": "0.1.0-mvp",
        "python": sys.version.split()[0],
        "gpu": gpu_info(),
        "features": {
            "ffmpeg": ffmpeg_ok,
            "transcribe": whisper_ok,
            "silence_cut": ae_ok,
            "upscale_ai": REALESRGAN_EXE.exists(),
            "upscale_fast": ffmpeg_ok,
            "export": ffmpeg_ok,
        },
    }


ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
               ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
MAX_UPLOAD = 2 * 1024 ** 3  # 2 GB


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    safe = re.sub(r"[^\w\-.]+", "_", file.filename or "video.mp4").lstrip(".")
    if Path(safe).suffix.lower() not in ALLOWED_EXT:
        raise HTTPException(400, f"Định dạng không hỗ trợ: {Path(safe).suffix}")
    dest = UPLOADS / safe
    i = 1
    while dest.exists():
        dest = UPLOADS / f"{Path(safe).stem}_{i}{Path(safe).suffix}"
        i += 1
    written = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "Tệp vượt giới hạn 2 GB")
            f.write(chunk)
    return {"name": dest.name, "url": f"/files/uploads/{dest.name}",
            "info": media_info(dest)}


@app.get("/api/media")
def list_media():
    items = []
    for p in sorted(UPLOADS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            items.append({"name": p.name, "url": f"/files/uploads/{p.name}",
                          "info": media_info(p)})
    return items


class JobRequest(BaseModel):
    type: str
    file: str
    params: dict = {}


MAX_PENDING_JOBS = 12
WHISPER_MODELS = {"tiny", "base", "small"}
ESRGAN_MODELS = {"realesr-animevideov3", "realesrgan-x4plus"}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    with JOBS_LOCK:
        pending = sum(1 for j in JOBS.values() if j["status"] in ("queued", "running"))
    if pending >= MAX_PENDING_JOBS:
        raise HTTPException(429, f"Hàng đợi đầy ({MAX_PENDING_JOBS} việc) — đợi bớt rồi thêm tiếp")
    src = safe_upload_path(req.file)
    if not src.is_file():
        raise HTTPException(404, f"Không tìm thấy file: {req.file}")
    p = req.params
    if req.type == "transcribe":
        p = dict(p)
        if p.get("model") not in WHISPER_MODELS:
            p["model"] = "base"
        if p.get("language") not in (None, "", "vi", "en"):
            p["language"] = None
        return submit_job("transcribe", src.name, job_transcribe, src, p)
    if req.type == "silence_cut":
        margin = min(2.0, max(0.0, float(p.get("margin", 0.2))))
        return submit_job("silence_cut", src.name, job_silence_cut, src, margin)
    if req.type == "upscale":
        mode = p.get("mode", "ai")
        if mode not in ("ai", "fast"):
            mode = "ai"
        scale = int(p.get("scale", 2))
        if scale not in (2, 3, 4):
            scale = 2
        model = p.get("model", "realesr-animevideov3")
        if model not in ESRGAN_MODELS:
            model = "realesr-animevideov3"
        return submit_job("upscale", src.name, job_upscale, src, mode, scale, model)
    if req.type == "export":
        preset = p.get("preset", "tiktok")
        if preset not in PRESETS:
            raise HTTPException(400, f"Preset không hợp lệ: {preset}")
        return submit_job("export", src.name, job_export, src, preset)
    raise HTTPException(400, f"Loại job không hỗ trợ: {req.type}")


@app.get("/api/jobs")
def list_jobs():
    with JOBS_LOCK:
        return sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)


THUMBS = TMP / "thumbs"
THUMBS.mkdir(exist_ok=True)


@app.get("/api/thumb/{name}")
def thumb(name: str):
    src = safe_upload_path(name)
    if not src.is_file():
        raise HTTPException(404, "not found")
    t = THUMBS / (name + ".jpg")
    if not t.exists() or t.stat().st_mtime < src.stat().st_mtime:
        info = media_info(src)
        ss = max(0.0, (info["duration"] or 0) * 0.25)
        r = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                  "-ss", str(ss), "-i", str(src), "-frames:v", "1",
                  "-vf", "scale=320:-2", str(t)])
        if r.returncode != 0 or not t.exists():
            raise HTTPException(404, "no thumbnail")
    return FileResponse(str(t), media_type="image/jpeg")


@app.post("/api/open-outputs")
def open_outputs():
    os.startfile(str(OUTPUTS))  # noqa: S606 - intentional, local desktop app
    return {"ok": True}


# ---------------------------------------------------------------- static
# Chỉ mở uploads + outputs — KHÔNG mở tmp/ (log, frame trung gian)
app.mount("/files/uploads", StaticFiles(directory=str(UPLOADS)), name="uploads")
app.mount("/files/outputs", StaticFiles(directory=str(OUTPUTS)), name="outputs")
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="app")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
