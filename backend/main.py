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

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def _ncnn_bin(folder: str, name: str) -> Path:
    """Đường dẫn binary ncnn theo hệ điều hành (.exe chỉ có trên Windows)."""
    return BINARIES / folder / (name + (".exe" if IS_WIN else ""))


REALESRGAN_EXE = _ncnn_bin("realesrgan", "realesrgan-ncnn-vulkan")
RIFE_EXE = _ncnn_bin("rife", "rife-ncnn-vulkan")
RIFE_MODEL = "rife-v4.6"
RVM_ONNX = BINARIES / "rvm" / "rvm_mobilenetv3_fp32.onnx"
PIPER_VOICES_DIR = BINARIES / "piper" / "voices"

# Ưu tiên ffmpeg đóng gói trong binaries/ (bản đầy đủ libass) rồi mới tới hệ thống
_local_ffmpeg = BINARIES / "ffmpeg" / ("ffmpeg.exe" if IS_WIN else "ffmpeg")
_local_ffprobe = BINARIES / "ffmpeg" / ("ffprobe.exe" if IS_WIN else "ffprobe")
FFMPEG_BIN = str(_local_ffmpeg) if _local_ffmpeg.exists() else "ffmpeg"
FFPROBE_BIN = str(_local_ffprobe) if _local_ffprobe.exists() else "ffprobe"
# mlx-whisper và một số lib gọi "ffmpeg" từ PATH — đưa bản đóng gói lên đầu
if _local_ffmpeg.exists():
    os.environ["PATH"] = str(_local_ffmpeg.parent) + os.pathsep + os.environ.get("PATH", "")

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
        FFPROBE_BIN, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


_PROBE_CACHE: dict = {}


def media_info(path: Path) -> dict:
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key and key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    p = ffprobe(path)
    fmt = p.get("format", {})
    v = next((s for s in p.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in p.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 30.0
    if v and v.get("r_frame_rate") and "/" in v["r_frame_rate"]:
        num, den = v["r_frame_rate"].split("/")
        if float(den) > 0:
            fps = float(num) / float(den)
    info = {
        "duration": float(fmt.get("duration", 0) or 0),
        "size": int(fmt.get("size", 0) or 0),
        "width": v.get("width") if v else None,
        "height": v.get("height") if v else None,
        "fps": round(fps, 3),
        "has_audio": a is not None,
        "vcodec": v.get("codec_name") if v else None,
    }
    if key:
        if len(_PROBE_CACHE) > 500:
            _PROBE_CACHE.clear()
        _PROBE_CACHE[key] = info
    return info


def gpu_info() -> Optional[dict]:
    try:
        r = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                  "--format=csv,noheader,nounits"])
        if r.returncode == 0 and r.stdout.strip():
            name, total, used = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            return {"name": name, "vram_total_mb": int(total), "vram_used_mb": int(used)}
    except FileNotFoundError:
        pass
    if IS_MAC:
        try:
            chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).stdout.strip()
            mem = int(_run(["sysctl", "-n", "hw.memsize"]).stdout.strip() or 0)
            if chip:
                return {"name": chip, "vram_total_mb": mem // 2 ** 20,
                        "vram_used_mb": 0, "type": "apple"}
        except (OSError, ValueError):
            pass
    return None


def run_ffmpeg_progress(args: list, duration: float, on_progress, job_id: str = None):
    """Run ffmpeg with -progress pipe:1 and report percent via callback."""
    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats"] + args
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    if job_id:
        _track_proc(job_id, proc)
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms=") and duration > 0:
            try:
                ms = int(line.split("=")[1]) / 1_000_000
                on_progress(min(99, ms / duration * 100))
            except ValueError:
                pass
    proc.wait()
    if job_id and job_id in CANCEL_REQUESTED:
        raise JobCancelled()
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


MLX_WHISPER_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
}


def mlx_whisper_available() -> bool:
    if not IS_MAC:
        return False
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


class _MlxWord:
    """Bọc dict word của mlx-whisper thành object cùng dạng faster-whisper."""
    __slots__ = ("word", "start", "end")

    def __init__(self, d):
        self.word, self.start, self.end = d["word"], d["start"], d["end"]


class _MlxSeg:
    __slots__ = ("text", "start", "end", "words")

    def __init__(self, d):
        self.text, self.start, self.end = d["text"], d["start"], d["end"]
        self.words = [_MlxWord(w) for w in d.get("words", [])]


# ---------------------------------------------------------------- job queue
JOBS: dict = {}
JOBS_LOCK = threading.Lock()
EXECUTOR = ThreadPoolExecutor(max_workers=2)

JOB_PROCS: dict = {}           # job_id -> subprocess đang chạy (để kill khi hủy)
CANCEL_REQUESTED: set = set()  # job_id đã yêu cầu hủy


class JobCancelled(Exception):
    pass


def _cancel_point(job_id: str):
    if job_id in CANCEL_REQUESTED:
        raise JobCancelled()


def _track_proc(job_id: str, proc):
    with JOBS_LOCK:
        JOB_PROCS[job_id] = proc


def _run_tracked(job_id: str, cmd, **kw):
    """Như _run nhưng kill được khi hủy job."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", **kw
    )
    _track_proc(job_id, proc)
    stdout, stderr = proc.communicate()
    _cancel_point(job_id)
    proc.stdout_text, proc.stderr_text = stdout, stderr
    return proc


def _set(job_id: str, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw)


def _job_wrapper(job_id: str, fn, *args):
    with JOBS_LOCK:
        if JOBS[job_id]["status"] == "cancelled":
            return
    _set(job_id, status="running", started=time.time())
    try:
        outputs = fn(job_id, *args)
        _set(job_id, status="done", progress=100, outputs=outputs,
             finished=time.time(), message="Hoàn thành")
    except JobCancelled:
        _set(job_id, status="cancelled", finished=time.time(), message="Đã hủy")
    except Exception as e:  # noqa: BLE001 - surface any job error to UI
        if job_id in CANCEL_REQUESTED:
            _set(job_id, status="cancelled", finished=time.time(), message="Đã hủy")
        else:
            _set(job_id, status="error", error=str(e)[-1000:], finished=time.time())
    finally:
        CANCEL_REQUESTED.discard(job_id)
        with JOBS_LOCK:
            JOB_PROCS.pop(job_id, None)


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


def _speech_recognize(job_id: str, src: Path, model_size: str, language,
                      engine, dur: float, lo: float = 0, hi: float = 90):
    """Chạy Whisper (MLX Metal hoặc faster-whisper CPU) → (seg_list, lines, lang).
    Tiến trình được ánh xạ vào khoảng [lo, hi]."""
    engine = engine or ("mlx" if mlx_whisper_available() else "faster")
    if engine == "mlx" and not mlx_whisper_available():
        engine = "faster"

    if engine == "mlx":
        _set(job_id, progress=lo,
             message=f"Whisper MLX trên GPU Metal (model {model_size})...")
        import mlx_whisper
        res = mlx_whisper.transcribe(
            str(src), path_or_hf_repo=MLX_WHISPER_REPOS[model_size],
            language=language, word_timestamps=True, verbose=None,
        )
        _cancel_point(job_id)
        seg_list = [_MlxSeg(s) for s in res["segments"]]
        lines = [s.text.strip() for s in seg_list]
        lang = res.get("language", "?")
        _set(job_id, progress=hi)
    else:
        _set(job_id, progress=lo, message=f"Đang nạp model Whisper ({model_size})...")
        model = get_whisper(model_size)

        _set(job_id, message="Đang nhận dạng giọng nói (word-level)...")
        segments, tr_info = model.transcribe(
            str(src), language=language, vad_filter=True, beam_size=5,
            word_timestamps=True,
        )

        seg_list = []
        lines = []
        for seg in segments:  # generator -> chạy thật ở đây
            _cancel_point(job_id)
            seg_list.append(seg)
            lines.append(seg.text.strip())
            _set(job_id, progress=lo + min(hi - lo, seg.end / dur * (hi - lo)),
                 message=f"Nghe: {seg.text.strip()[:60]}")
        lang = getattr(tr_info, "language", "?")
    return seg_list, lines, lang


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

    seg_list, lines, lang = _speech_recognize(
        job_id, src, model_size, language, p.get("engine"), dur, 0, 90)

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
        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(src), "-vf", f"subtitles={ass_path.name}",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium",
               "-c:a", "copy", str(burned)]
        r = _run_tracked(job_id, cmd, cwd=str(OUTPUTS))
        if r.returncode != 0:
            raise RuntimeError(f"Burn subtitle failed: {r.stderr_text[-500:]}")
        outputs.append(out_entry(burned))

    _set(job_id, message=f"Xong — ngôn ngữ: {lang}, {len(chunks)} cụm phụ đề "
                         f"({effect}, {font}, tối đa {max_words or 'cả câu'} từ)")
    return outputs


# ---------------------------------------------------------------- job: silence cut
def job_silence_cut(job_id: str, src: Path, margin: float):
    _set(job_id, progress=-1, message="auto-editor đang phân tích & cắt khoảng lặng...")
    out = unique_out(src.stem + "_cut", ".mp4")
    cmd = [sys.executable, "-m", "auto_editor", str(src),
           "--margin", f"{margin}sec", "--no-open", "--output", str(out)]
    r = _run_tracked(job_id, cmd, cwd=str(TMP))
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"auto-editor failed: {(r.stderr_text or r.stdout_text)[-800:]}")
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
        run_ffmpeg_progress(args, dur, lambda p: _set(job_id, progress=p), job_id)
        return [out_entry(out)]

    # ---- AI mode: frames -> realesrgan-ncnn-vulkan (GPU) -> reassemble
    frames_in = TMP / f"fr_in_{job_id}"
    frames_out = TMP / f"fr_out_{job_id}"
    frames_in.mkdir(exist_ok=True)
    frames_out.mkdir(exist_ok=True)
    try:
        _set(job_id, progress=2, message="Tách khung hình...")
        r = _run_tracked(job_id, [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                                  "-i", str(src), str(frames_in / "f_%08d.png")])
        if r.returncode != 0:
            raise RuntimeError(f"Frame extract failed: {r.stderr_text[-400:]}")
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
                cwd=str(REALESRGAN_EXE.parent),  # tìm thư mục models/ cạnh binary
                stdout=subprocess.DEVNULL, stderr=ef,
            )
            _track_proc(job_id, proc)
            while proc.poll() is None:
                done = len(list(frames_out.glob("*.png")))
                _set(job_id, progress=5 + done / total * 85,
                     message=f"AI upscale: {done}/{total} khung hình")
                time.sleep(1.5)
            _cancel_point(job_id)
        if proc.returncode != 0:
            err = err_log.read_text(encoding="utf-8", errors="replace") if err_log.exists() else ""
            raise RuntimeError(f"Real-ESRGAN failed: {err[-500:]}")
        err_log.unlink(missing_ok=True)

        _set(job_id, progress=92, message="Ghép video + âm thanh...")
        out = unique_out(f"{src.stem}_ai_x{scale}", ".mp4")
        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
               "-framerate", str(info["fps"]),
               "-i", str(frames_out / "f_%08d.png")]
        if info["has_audio"]:
            cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", str(out)]
        r = _run_tracked(job_id, cmd)
        if r.returncode != 0:
            raise RuntimeError(f"Reassemble failed: {r.stderr_text[-400:]}")
        return [out_entry(out)]
    finally:
        shutil.rmtree(frames_in, ignore_errors=True)
        shutil.rmtree(frames_out, ignore_errors=True)


# ---------------------------------------------------------------- job: export preset
PRESETS = {
    "tiktok": {"w": 1080, "h": 1920, "label": "TikTok/Reels/Shorts 9:16"},
    "youtube": {"w": 1920, "h": 1080, "label": "YouTube 16:9 1080p"},
    "square": {"w": 1080, "h": 1080, "label": "Instagram 1:1"},
    "reels45": {"w": 1080, "h": 1350, "label": "Reels/Feed dọc 4:5"},
    "gif": {"label": "GIF chia sẻ 480p"},
    "mp3": {"label": "Tách âm thanh MP3"},
}


def job_export(job_id: str, src: Path, preset: str):
    info = media_info(src)
    dur = info["duration"] or 1

    if preset == "mp3":
        if not info["has_audio"]:
            raise RuntimeError("Video này không có âm thanh để tách")
        out = unique_out(src.stem, ".mp3")
        _set(job_id, message="Tách âm thanh MP3 320k...")
        args = ["-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "320k", str(out)]
        run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
        return [out_entry(out)]

    if preset == "gif":
        out = unique_out(src.stem, ".gif")
        _set(job_id, message="Xuất GIF 480p 12fps (palette 2 lượt)...")
        vf = ("fps=12,scale=480:-2:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];"
              "[b][p]paletteuse=dither=bayer:bayer_scale=4")
        args = ["-i", str(src), "-filter_complex", vf, "-loop", "0", str(out)]
        run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
        return [out_entry(out)]

    p = PRESETS[preset]
    w, h = p["w"], p["h"]
    out = unique_out(f"{src.stem}_{preset}", ".mp4")
    _set(job_id, message=f"Xuất {p['label']}...")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    args = ["-i", str(src), "-vf", vf, "-c:v", "libx264", "-crf", "20",
            "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- job: RIFE interpolation
def job_rife(job_id: str, src: Path, mode: str):
    """Nội suy khung hình ×2 bằng RIFE — 'smooth' tăng fps gấp đôi,
    'slowmo' giữ fps và kéo dài video gấp đôi (âm thanh chậm 0.5×)."""
    if not RIFE_EXE.exists():
        raise RuntimeError("Chưa có rife-ncnn-vulkan trong binaries/rife")
    info = media_info(src)
    if not info["width"]:
        raise RuntimeError("Tệp không có luồng video")
    fps = info["fps"] or 30

    frames_in = TMP / f"rf_in_{job_id}"
    frames_out = TMP / f"rf_out_{job_id}"
    frames_in.mkdir(exist_ok=True)
    frames_out.mkdir(exist_ok=True)
    try:
        _set(job_id, progress=2, message="Tách khung hình...")
        r = _run_tracked(job_id, [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                                  "-i", str(src), str(frames_in / "%08d.png")])
        if r.returncode != 0:
            raise RuntimeError(f"Frame extract failed: {r.stderr_text[-400:]}")
        total = len(list(frames_in.glob("*.png")))
        if total == 0:
            raise RuntimeError("Không tách được khung hình nào")
        total_out = total * 2

        _set(job_id, progress=5, message=f"RIFE nội suy {total} → {total_out} khung hình (GPU)...")
        err_log = TMP / f"rife_{job_id}.log"
        with open(err_log, "w", encoding="utf-8", errors="replace") as ef:
            proc = subprocess.Popen(
                [str(RIFE_EXE), "-i", str(frames_in), "-o", str(frames_out),
                 "-m", RIFE_MODEL, "-n", str(total_out)],
                cwd=str(RIFE_EXE.parent),
                stdout=subprocess.DEVNULL, stderr=ef,
            )
            _track_proc(job_id, proc)
            while proc.poll() is None:
                done = len(list(frames_out.glob("*.png")))
                _set(job_id, progress=5 + done / total_out * 80,
                     message=f"RIFE: {done}/{total_out} khung hình")
                time.sleep(1.5)
            _cancel_point(job_id)
        if proc.returncode != 0:
            err = err_log.read_text(encoding="utf-8", errors="replace") if err_log.exists() else ""
            raise RuntimeError(f"RIFE failed: {err[-500:]}")
        err_log.unlink(missing_ok=True)

        _set(job_id, progress=88, message="Ghép video + âm thanh...")
        if mode == "slowmo":
            out = unique_out(f"{src.stem}_slowmo2x", ".mp4")
            out_fps = fps
        else:
            out = unique_out(f"{src.stem}_{round(fps * 2)}fps", ".mp4")
            out_fps = fps * 2
        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
               "-framerate", str(out_fps), "-i", str(frames_out / "%08d.png")]
        if info["has_audio"]:
            cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a"]
            if mode == "slowmo":
                cmd += ["-af", "atempo=0.5"]
            cmd += ["-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", str(out)]
        r = _run_tracked(job_id, cmd)
        if r.returncode != 0:
            raise RuntimeError(f"Reassemble failed: {r.stderr_text[-400:]}")
        return [out_entry(out)]
    finally:
        shutil.rmtree(frames_in, ignore_errors=True)
        shutil.rmtree(frames_out, ignore_errors=True)


# ---------------------------------------------------------------- job: background removal (RVM)
BG_COLORS = {"green": (0, 255, 0), "black": (0, 0, 0), "white": (255, 255, 255)}


def job_bg_remove(job_id: str, src: Path, bg: str):
    """Tách nền người nói bằng Robust Video Matting (ONNX).
    bg: green/black/white = nền màu (mp4) · alpha = nền trong suốt (webm VP9)."""
    import numpy as np
    import onnxruntime as ort

    if not RVM_ONNX.exists():
        raise RuntimeError("Chưa có model RVM trong binaries/rvm")
    info = media_info(src)
    w, h = info["width"], info["height"]
    if not w or not h:
        raise RuntimeError("Tệp không có luồng video")
    fps = info["fps"] or 30
    dur = info["duration"] or 1
    total = max(1, int(dur * fps))

    _set(job_id, progress=1, message="Nạp model RVM...")
    avail = ort.get_available_providers()
    providers = [p for p in ("CoreMLExecutionProvider", "CPUExecutionProvider") if p in avail]
    sess = ort.InferenceSession(str(RVM_ONNX), providers=providers)
    # RVM khuyến nghị downsample sao cho cạnh dài ~512px khi phân tích
    dsr = np.array([min(1.0, 512 / max(w, h))], dtype=np.float32)
    rec = [np.zeros([1, 1, 1, 1], dtype=np.float32)] * 4

    dec = subprocess.Popen(
        [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if bg == "alpha":
        out = unique_out(f"{src.stem}_alpha", ".webm")
        enc_cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                   "-r", str(fps), "-i", "pipe:0"]
        if info["has_audio"]:
            enc_cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a", "-c:a", "libopus"]
        enc_cmd += ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "28",
                    "-b:v", "0", "-row-mt", "1", str(out)]
        channels = 4
    else:
        out = unique_out(f"{src.stem}_{bg}bg", ".mp4")
        enc_cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                   "-r", str(fps), "-i", "pipe:0"]
        if info["has_audio"]:
            enc_cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a",
                        "-c:a", "aac", "-shortest"]
        enc_cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                    "-pix_fmt", "yuv420p", str(out)]
        channels = 3
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
    _track_proc(job_id, enc)

    bg_arr = None
    if bg in BG_COLORS:
        bg_arr = np.array(BG_COLORS[bg], dtype=np.float32).reshape(3, 1, 1) / 255.0

    ep = "CoreML" if providers and providers[0].startswith("CoreML") else "CPU"
    frame_bytes = w * h * 3
    i = 0
    try:
        while True:
            if job_id in CANCEL_REQUESTED:
                dec.kill()
                enc.kill()
                raise JobCancelled()
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            srcT = (frame.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
            fgr, pha, *rec = sess.run(None, {
                "src": srcT, "r1i": rec[0], "r2i": rec[1],
                "r3i": rec[2], "r4i": rec[3], "downsample_ratio": dsr,
            })
            if channels == 4:
                rgba = np.empty((h, w, 4), dtype=np.uint8)
                rgba[..., :3] = (fgr[0].transpose(1, 2, 0) * 255).clip(0, 255).astype(np.uint8)
                rgba[..., 3] = (pha[0, 0] * 255).clip(0, 255).astype(np.uint8)
                enc.stdin.write(rgba.tobytes())
            else:
                com = fgr[0] * pha[0] + bg_arr * (1 - pha[0])
                enc.stdin.write((com.transpose(1, 2, 0) * 255)
                                .clip(0, 255).astype(np.uint8).tobytes())
            i += 1
            if i % 10 == 0:
                _set(job_id, progress=min(99, i / total * 100),
                     message=f"Tách nền ({ep}): {i}/{total} khung hình")
    finally:
        dec.stdout.close()
        dec.wait()
        if enc.stdin:
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
        enc.wait()
    _cancel_point(job_id)
    if enc.returncode != 0 or not out.exists():
        err = enc.stderr.read().decode("utf-8", "replace") if enc.stderr else ""
        raise RuntimeError(f"Encode failed: {err[-500:]}")
    return [out_entry(out)]


# ---------------------------------------------------------------- job: TTS (Piper)
PIPER_VOICE_MAP = {
    "vi": "vi_VN-vais1000-medium.onnx",
    "en": "en_US-lessac-medium.onnx",
}


def piper_available() -> bool:
    try:
        import piper  # noqa: F401
        return any((PIPER_VOICES_DIR / v).exists() for v in PIPER_VOICE_MAP.values())
    except ImportError:
        return False


def job_tts(job_id: str, text: str, voice: str, speed: float):
    onnx = PIPER_VOICES_DIR / PIPER_VOICE_MAP[voice]
    if not onnx.exists():
        raise RuntimeError(f"Chưa tải giọng '{voice}' vào binaries/piper/voices")
    out = unique_out(f"tts_{voice}", ".wav")
    _set(job_id, progress=-1, message="Piper đang tổng hợp giọng nói (local)...")
    cmd = [sys.executable, "-m", "piper", "-m", str(onnx), "-f", str(out)]
    if abs(speed - 1.0) > 0.01:
        cmd += ["--length-scale", str(round(1.0 / speed, 2))]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    _track_proc(job_id, proc)
    _, se = proc.communicate(input=text)
    _cancel_point(job_id)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"Piper TTS failed: {(se or '')[-500:]}")

    _set(job_id, progress=90, message="Chuyển thêm bản MP3...")
    mp3 = out.with_suffix(".mp3")
    r = _run([FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
              "-i", str(out), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)])
    outputs = [out_entry(out)]
    if r.returncode == 0 and mp3.exists():
        outputs.append(out_entry(mp3))
    _set(job_id, message=f"Xong — giọng {voice}, {len(text)} ký tự")
    return outputs


# ---------------------------------------------------------------- job: auto edit (AI 1 chạm)
def job_auto_edit(job_id: str, src: Path, p: dict):
    """Pipeline AI tự động: cắt khoảng lặng → caption karaoke → xuất preset.
    Mỗi bước bật/tắt được; chạy hàng loạt để 'render qua đêm'."""
    do_cut = bool(p.get("cut", True))
    do_caption = bool(p.get("caption", True))
    preset = p.get("preset") or ""
    cur = src
    tmp_files = []
    try:
        if do_cut:
            _set(job_id, progress=2, message="Bước 1/3 — cắt khoảng lặng...")
            margin = min(2.0, max(0.0, float(p.get("margin", 0.2))))
            cut_out = TMP / f"auto_cut_{job_id}.mp4"
            cmd = [sys.executable, "-m", "auto_editor", str(src),
                   "--margin", f"{margin}sec", "--no-open", "--output", str(cut_out)]
            r = _run_tracked(job_id, cmd, cwd=str(TMP))
            if r.returncode != 0 or not cut_out.exists():
                raise RuntimeError(f"auto-editor failed: {(r.stderr_text or r.stdout_text)[-500:]}")
            tmp_files.append(cut_out)
            cur = cut_out
        _cancel_point(job_id)

        if do_caption:
            _set(job_id, progress=28, message="Bước 2/3 — nhận dạng giọng nói...")
            info = media_info(cur)
            dur = info["duration"] or 1
            vw, vh = info["width"] or 1280, info["height"] or 720
            model_size = p.get("model") if p.get("model") in WHISPER_MODELS else "base"
            language = p.get("language") if p.get("language") in ("vi", "en") else None
            seg_list, _lines, _lang = _speech_recognize(
                job_id, cur, model_size, language, p.get("engine"), dur, 30, 70)
            chunks = chunk_words(seg_list, int(p.get("max_words", 3)),
                                 bool(p.get("uppercase", False)))
            if chunks:
                font = p.get("font") if p.get("font") in SUB_FONTS else "Arial"
                ass_path = TMP / f"auto_{job_id}.ass"
                ass_path.write_text(
                    build_ass(chunks, vw, vh, font, p.get("size", "M"),
                              p.get("effect", "karaoke"), p.get("position", "bottom")),
                    encoding="utf-8")
                tmp_files.append(ass_path)
                _set(job_id, progress=72, message="Bước 2/3 — ghi phụ đề vào video...")
                burned = TMP / f"auto_sub_{job_id}.mp4"
                cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                       "-i", str(cur), "-vf", f"subtitles={ass_path.name}",
                       "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                       "-c:a", "copy", str(burned)]
                r = _run_tracked(job_id, cmd, cwd=str(TMP))
                if r.returncode != 0:
                    raise RuntimeError(f"Burn subtitle failed: {r.stderr_text[-500:]}")
                tmp_files.append(burned)
                cur = burned
        _cancel_point(job_id)

        _set(job_id, progress=80, message="Bước 3/3 — xuất bản...")
        dur = media_info(cur)["duration"] or 1
        out = unique_out(f"{src.stem}_auto", ".mp4")
        prog = lambda pr: _set(job_id, progress=80 + pr * 0.19)  # noqa: E731
        if preset == "reframe916":
            fc = ("[0:v]split[a][b];"
                  "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
                  "crop=1080:1920,boxblur=24:6[bg];"
                  "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                  "[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]")
            args = ["-i", str(cur), "-filter_complex", fc,
                    "-map", "[vout]", "-map", "0:a?",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
            run_ffmpeg_progress(args, dur, prog, job_id)
        elif preset in ("tiktok", "youtube", "square", "reels45"):
            w, h = PRESETS[preset]["w"], PRESETS[preset]["h"]
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
            args = ["-i", str(cur), "-vf", vf, "-c:v", "libx264", "-crf", "20",
                    "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", str(out)]
            run_ffmpeg_progress(args, dur, prog, job_id)
        else:
            r = _run_tracked(job_id, [FFMPEG_BIN, "-y", "-hide_banner",
                                      "-loglevel", "error", "-i", str(cur),
                                      "-c", "copy", str(out)])
            if r.returncode != 0:
                raise RuntimeError(f"Finalize failed: {r.stderr_text[-400:]}")
        steps = [s for s, on in (("cắt lặng", do_cut), ("caption", do_caption),
                                 ("preset " + preset, bool(preset))) if on]
        _set(job_id, message="Xong — " + " → ".join(steps))
        return [out_entry(out)]
    finally:
        for t in tmp_files:
            t.unlink(missing_ok=True)


# ---------------------------------------------------------------- job: reframe 9:16
def job_reframe(job_id: str, src: Path, mode: str):
    """Đổi khung dọc 9:16 kiểu CapCut: 'blur' = video giữa + nền chính nó phóng to
    làm mờ; 'crop' = cắt giữa tràn khung."""
    info = media_info(src)
    if not info["width"]:
        raise RuntimeError("Tệp không có luồng video")
    dur = info["duration"] or 1
    out = unique_out(f"{src.stem}_916{mode}", ".mp4")
    _set(job_id, message=f"Đổi khung 9:16 ({'nền mờ' if mode == 'blur' else 'cắt giữa'})...")
    if mode == "crop":
        args = ["-i", str(src),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
    else:
        fc = ("[0:v]split[a][b];"
              "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=24:6[bg];"
              "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]")
        args = ["-i", str(src), "-filter_complex", fc,
                "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- job: speed
def _atempo_chain(factor: float) -> str:
    """atempo chỉ nhận 0.5–2.0 → ghép chuỗi để đạt hệ số bất kỳ."""
    parts = []
    f = factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f *= 2.0
    parts.append(f"atempo={f:.4f}")
    return ",".join(parts)


def job_speed(job_id: str, src: Path, factor: float):
    info = media_info(src)
    dur = (info["duration"] or 1) / factor
    lab = f"{factor:g}x".replace(".", "_")
    out = unique_out(f"{src.stem}_{lab}", ".mp4")
    _set(job_id, message=f"Đổi tốc độ ×{factor:g} (giữ cao độ âm thanh)...")
    args = ["-i", str(src), "-vf", f"setpts=PTS/{factor}"]
    if info["has_audio"]:
        args += ["-af", _atempo_chain(factor)]
    args += ["-c:v", "libx264", "-crf", "19", "-preset", "medium",
             "-pix_fmt", "yuv420p", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- job: color filter
COLOR_FILTERS = {
    "vivid": ("Rực rỡ", "eq=saturation=1.35:contrast=1.1:brightness=0.01"),
    "warm": ("Ấm áp", "colortemperature=temperature=4600,eq=saturation=1.12"),
    "cool": ("Lạnh", "colortemperature=temperature=8600,eq=saturation=1.05"),
    "bw": ("Đen trắng", "hue=s=0,eq=contrast=1.18:brightness=0.02"),
    "film": ("Film cổ điển", "curves=preset=vintage,vignette=PI/5,noise=alls=5:allf=t+u"),
    "sharp": ("Nét căng", "unsharp=5:5:0.9:5:5:0.0,eq=saturation=1.08"),
}


def job_color(job_id: str, src: Path, name: str):
    label, vf = COLOR_FILTERS[name]
    dur = media_info(src)["duration"] or 1
    out = unique_out(f"{src.stem}_{name}", ".mp4")
    _set(job_id, message=f"Áp filter màu '{label}'...")
    args = ["-i", str(src), "-vf", vf, "-c:v", "libx264", "-crf", "19",
            "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- job: background music
def job_music(job_id: str, src: Path, music: Path, vol: float, duck: bool):
    info = media_info(src)
    dur = info["duration"] or 1
    out = unique_out(f"{src.stem}_music", ".mp4")
    _set(job_id, message="Trộn nhạc nền" + (" + tự nén khi có giọng nói (ducking)" if duck else "") + "...")
    if info["has_audio"] and duck:
        fc = (f"[1:a]volume={vol}[m];"
              "[m][0:a]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=400[dk];"
              "[0:a][dk]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    elif info["has_audio"]:
        fc = (f"[1:a]volume={vol}[m];"
              "[0:a][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]")
    else:
        fc = f"[1:a]volume={vol}[aout]"
    args = ["-i", str(src), "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- job: stabilize
def job_stabilize(job_id: str, src: Path):
    dur = media_info(src)["duration"] or 1
    out = unique_out(f"{src.stem}_stab", ".mp4")
    _set(job_id, message="Chống rung (deshake)...")
    args = ["-i", str(src), "-vf", "deshake=rx=32:ry=32:edge=mirror",
            "-c:v", "libx264", "-crf", "19", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- API
@app.get("/api/health")
def health():
    ffmpeg_ok = FFMPEG_BIN != "ffmpeg" or shutil.which("ffmpeg") is not None
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
    try:
        import onnxruntime  # noqa: F401
        rvm_ok = RVM_ONNX.exists()
    except ImportError:
        rvm_ok = False
    return {
        "status": "ok",
        "app": "Local Studio",
        "version": "0.4.0",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "gpu": gpu_info(),
        "features": {
            "ffmpeg": ffmpeg_ok,
            "transcribe": whisper_ok,
            "whisper_mlx": mlx_whisper_available(),
            "silence_cut": ae_ok,
            "upscale_ai": REALESRGAN_EXE.exists(),
            "upscale_fast": ffmpeg_ok,
            "export": ffmpeg_ok,
            "rife": RIFE_EXE.exists(),
            "bg_remove": rvm_ok,
            "tts": piper_available(),
            "auto_edit": ae_ok and whisper_ok and ffmpeg_ok,
            "reframe": ffmpeg_ok,
            "speed": ffmpeg_ok,
            "color": ffmpeg_ok,
            "music": ffmpeg_ok,
            "stabilize": ffmpeg_ok,
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
    file: str = ""
    params: dict = {}


MAX_PENDING_JOBS = 40  # đủ cho batch "render qua đêm"
WHISPER_MODELS = {"tiny", "base", "small", "medium"}
ESRGAN_MODELS = {"realesr-animevideov3", "realesrgan-x4plus"}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    with JOBS_LOCK:
        pending = sum(1 for j in JOBS.values() if j["status"] in ("queued", "running"))
    if pending >= MAX_PENDING_JOBS:
        raise HTTPException(429, f"Hàng đợi đầy ({MAX_PENDING_JOBS} việc) — đợi bớt rồi thêm tiếp")
    p = req.params

    if req.type == "tts":  # không cần file nguồn
        text = (p.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Thiếu văn bản cần đọc")
        if len(text) > 5000:
            raise HTTPException(400, "Văn bản quá dài (tối đa 5000 ký tự)")
        voice = p.get("voice") if p.get("voice") in PIPER_VOICE_MAP else "vi"
        speed = min(1.5, max(0.6, float(p.get("speed", 1.0))))
        label = text[:40] + ("…" if len(text) > 40 else "")
        return submit_job("tts", label, job_tts, text, voice, speed)

    src = safe_upload_path(req.file)
    if not src.is_file():
        raise HTTPException(404, f"Không tìm thấy file: {req.file}")
    if req.type == "transcribe":
        p = dict(p)
        if p.get("model") not in WHISPER_MODELS:
            p["model"] = "base"
        if p.get("language") not in (None, "", "vi", "en"):
            p["language"] = None
        if p.get("engine") not in (None, "", "faster", "mlx"):
            p["engine"] = None
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
    if req.type == "rife":
        mode = p.get("mode", "smooth")
        if mode not in ("smooth", "slowmo"):
            mode = "smooth"
        return submit_job("rife", src.name, job_rife, src, mode)
    if req.type == "bg_remove":
        bg = p.get("bg", "green")
        if bg not in ("green", "black", "white", "alpha"):
            bg = "green"
        return submit_job("bg_remove", src.name, job_bg_remove, src, bg)
    if req.type == "auto_edit":
        return submit_job("auto_edit", src.name, job_auto_edit, src, dict(p))
    if req.type == "reframe":
        mode = p.get("mode", "blur")
        if mode not in ("blur", "crop"):
            mode = "blur"
        return submit_job("reframe", src.name, job_reframe, src, mode)
    if req.type == "speed":
        factor = min(4.0, max(0.25, float(p.get("factor", 2.0))))
        return submit_job("speed", src.name, job_speed, src, factor)
    if req.type == "color":
        name = p.get("filter", "vivid")
        if name not in COLOR_FILTERS:
            name = "vivid"
        return submit_job("color", src.name, job_color, src, name)
    if req.type == "music":
        music = safe_upload_path(str(p.get("music", "")))
        if not music.is_file():
            raise HTTPException(404, "Không tìm thấy file nhạc trong kho media")
        vol = min(1.0, max(0.05, float(p.get("volume", 0.25))))
        duck = bool(p.get("duck", True))
        return submit_job("music", src.name, job_music, src, music, vol, duck)
    if req.type == "stabilize":
        return submit_job("stabilize", src.name, job_stabilize, src)
    raise HTTPException(400, f"Loại job không hỗ trợ: {req.type}")


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy job")
    if job["status"] == "queued":
        _set(job_id, status="cancelled", finished=time.time(), message="Đã hủy")
        return JOBS[job_id]
    if job["status"] == "running":
        CANCEL_REQUESTED.add(job_id)
        with JOBS_LOCK:
            proc = JOB_PROCS.get(job_id)
        if proc and proc.poll() is None:
            proc.kill()
        _set(job_id, message="Đang hủy...")
    return JOBS[job_id]


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
        r = _run([FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                  "-ss", str(ss), "-i", str(src), "-frames:v", "1",
                  "-vf", "scale=320:-2", str(t)])
        if r.returncode != 0 or not t.exists():
            raise HTTPException(404, "no thumbnail")
    return FileResponse(str(t), media_type="image/jpeg")


@app.post("/api/open-outputs")
def open_outputs():
    if IS_WIN:
        os.startfile(str(OUTPUTS))  # noqa: S606 - intentional, local desktop app
    elif IS_MAC:
        subprocess.Popen(["open", str(OUTPUTS)])
    else:
        subprocess.Popen(["xdg-open", str(OUTPUTS)])
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
