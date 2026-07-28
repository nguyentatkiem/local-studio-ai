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

# nạp backend/.env (KEY=VALUE) vào môi trường — nơi để PEXELS_API_KEY, LS_CLAUDE_MODEL...
_ENV_FILE = BACKEND_DIR / ".env"
if _ENV_FILE.exists():
    for _ln in _ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


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
    """auth_config.json chứa {"disabled": true} → app mở thẳng không cần mật khẩu
    (an toàn vì server chỉ bind 127.0.0.1). Mặc định vẫn sinh mật khẩu admin."""
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


AUTH_DISABLED = bool(AUTH.get("disabled"))


def _check_password(pw: str) -> bool:
    if AUTH_DISABLED:
        return True
    digest = hashlib.sha256((AUTH["salt"] + pw).encode()).hexdigest()
    return hmac.compare_digest(digest, AUTH["hash"])


def _session_ok(request: Request) -> bool:
    if AUTH_DISABLED:
        return True
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
    return {"auth_required": not AUTH_DISABLED, "authenticated": _session_ok(request)}


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
        "acodec": a.get("codec_name") if a else None,
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


def run_ffmpeg_progress(args: list, duration: float, on_progress,
                        job_id: str = None, cwd: str = None):
    """Run ffmpeg with -progress pipe:1 and report percent via callback.
    stderr ghi ra file tạm (KHÔNG PIPE — lỗi lặp mỗi frame làm đầy buffer → treo)."""
    import tempfile
    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats"] + args
    with tempfile.TemporaryFile() as ef:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=ef,
            text=True, encoding="utf-8", errors="replace", cwd=cwd,
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
            ef.seek(0)
            err = ef.read().decode("utf-8", "replace")
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err[-800:]}")


def _ff_escape_path(p) -> str:
    """Thoát đường dẫn để dùng trong giá trị filter ffmpeg (subtitles/fontsdir):
    escape \\ , : và ' — tránh vỡ chuỗi filter."""
    s = str(p)
    s = s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return s


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
    if size == "large-v3-turbo":  # faster-whisper không nhận tên turbo → dùng large-v3
        size = "large-v3"
    with _whisper_lock:
        if size not in _whisper_models:
            _whisper_models[size] = WhisperModel(size, device="cpu", compute_type="int8")
        return _whisper_models[size]


MLX_WHISPER_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
FONTS_DIR = BINARIES / "fonts"   # font đóng gói (Be Vietnam Pro) cho libass — không phụ thuộc hệ thống


def whisper_available_any() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return mlx_whisper_available()


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
            _set(job_id, status="error", error=str(e)[-1000:], finished=time.time(),
                 message=("Lỗi: " + str(e))[:160])
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
    # stderr encoder ra file — PIPE không được drain sẽ deadlock khi vp9/x264 in cảnh báo mỗi frame
    enc_log = TMP / f"bgenc_{job_id}.log"
    enc_ef = open(enc_log, "wb")
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                           stderr=enc_ef)
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
        enc_ef.close()
    _cancel_point(job_id)
    if enc.returncode != 0 or not out.exists():
        err = enc_log.read_text(encoding="utf-8", errors="replace") if enc_log.exists() else ""
        raise RuntimeError(f"Encode failed: {err[-500:]}")
    enc_log.unlink(missing_ok=True)
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


# ================================================================ wave 3 (v0.5)
FRAME_SIZES = {"169": (1920, 1080), "916": (1080, 1920), "11": (1080, 1080)}
XFADE_TRANSITIONS = {"fade", "dissolve", "wipeleft", "wiperight", "slideleft",
                     "slideright", "circleopen", "circleclose", "pixelize", "radial"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _extract_norm_clip(job_id: str, src: Path, t0: float, t_dur: float,
                       size: tuple, fps: int, out_path: Path, keep_audio: bool):
    """Cắt 1 đoạn và chuẩn hoá cùng khung/fps (48kHz stereo nếu giữ audio) để concat."""
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps},setsar=1")
    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{max(0.0, t0):.3f}", "-t", f"{t_dur:.3f}", "-i", str(src)]
    if keep_audio:
        if media_info(src)["has_audio"]:
            cmd += ["-map", "0:v:0", "-map", "0:a:0", "-ar", "48000", "-ac", "2",
                    "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-f", "lavfi", "-t", f"{t_dur:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo",
                    "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-vf", vf, "-c:v", "libx264", "-crf", "19", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", str(out_path)]
    r = _run_tracked(job_id, cmd)
    if r.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Cắt/chuẩn hoá đoạn thất bại: {r.stderr_text[-300:]}")


# ---------------------------------------------------------------- job: merge + xfade
def job_merge(job_id: str, files: list, transition: str, tdur: float,
              target: str, music, music_vol: float):
    """Ghép nhiều clip thành một với chuyển cảnh xfade; tuỳ chọn thay nhạc nền."""
    size = FRAME_SIZES[target]
    fps = 30
    tmp_clips = []
    try:
        durs = []
        for i, f in enumerate(files):
            _cancel_point(job_id)
            _set(job_id, progress=i / len(files) * 55,
                 message=f"Chuẩn hoá clip {i + 1}/{len(files)}: {f.name}")
            info = media_info(f)
            if not info["width"]:
                raise RuntimeError(f"'{f.name}' không có luồng video")
            d = info["duration"] or 1
            tc = TMP / f"mg_{job_id}_{i:02d}.mp4"
            tmp_clips.append(tc)  # append TRƯỚC khi cắt để finally dọn cả file dở
            _extract_norm_clip(job_id, f, 0, d, size, fps, tc, keep_audio=music is None)
            durs.append(media_info(tc)["duration"] or d)
        # xfade không được dài hơn nửa clip ngắn nhất (floor 0.05s, KHÔNG kéo ngược lên)
        tdur = max(0.05, min(tdur, min(durs) / 2 - 0.05))

        _set(job_id, progress=58, message=f"Ghép {len(files)} clip (xfade {transition})...")
        out = unique_out(f"{files[0].stem}_merge{len(files)}", ".mp4")
        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error"]
        for tc in tmp_clips:
            cmd += ["-i", str(tc)]
        if music is not None:
            cmd += ["-stream_loop", "-1", "-i", str(music)]
        fc_parts = []
        # chuỗi video xfade
        prev = "[0:v]"
        offset = 0.0
        for i in range(1, len(tmp_clips)):
            offset += durs[i - 1] - tdur
            lab = f"[vx{i}]" if i < len(tmp_clips) - 1 else "[vout]"
            fc_parts.append(f"{prev}[{i}:v]xfade=transition={transition}:"
                            f"duration={tdur:.3f}:offset={offset:.3f}{lab}")
            prev = lab
        total_dur = sum(durs) - tdur * (len(durs) - 1)
        # audio
        if music is not None:
            mi = len(tmp_clips)
            fc_parts.append(f"[{mi}:a]volume={music_vol},"
                            f"atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS[aout]")
        else:
            prev_a = "[0:a]"
            for i in range(1, len(tmp_clips)):
                lab = f"[ax{i}]" if i < len(tmp_clips) - 1 else "[aout]"
                fc_parts.append(f"{prev_a}[{i}:a]acrossfade=d={tdur:.3f}{lab}")
                prev_a = lab
        cmd += ["-filter_complex", ";".join(fc_parts),
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _track_proc(job_id, proc)
        _, se = proc.communicate()
        _cancel_point(job_id)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"xfade thất bại: {se.decode('utf-8', 'replace')[-400:]}")
        _set(job_id, message=f"Xong — {len(files)} clip → {total_dur:.1f}s ({transition})")
        return [out_entry(out)]
    finally:
        for tc in tmp_clips:
            tc.unlink(missing_ok=True)


# ---------------------------------------------------------------- job: beat-sync
def librosa_available() -> bool:
    try:
        import librosa  # noqa: F401
        return True
    except ImportError:
        return False


def job_beatsync(job_id: str, files: list, music: Path, target: str, max_seg: int):
    """Cắt video theo nhịp nhạc (kiểu template CapCut): mỗi nhịp một đoạn,
    xoay vòng qua các clip đã chọn, nhạc làm âm thanh chính."""
    import librosa

    import numpy as np

    _set(job_id, progress=2, message="Phân tích nhịp bài nhạc (librosa)...")
    # cap 240s: đủ cho 120 đoạn, tránh nuốt RAM khi chọn nhầm mix vài giờ
    y, sr = librosa.load(str(music), mono=True, duration=240)
    _cancel_point(job_id)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    _cancel_point(job_id)
    # numpy 2.x: tempo có thể là ndarray shape (1,) — ép về scalar
    tempo = float(np.atleast_1d(tempo)[0] or 0)
    beat_times = [float(t) for t in librosa.frames_to_time(beat_frames, sr=sr)]
    music_dur = len(y) / sr
    if len(beat_times) < 4:
        raise RuntimeError("Không dò được nhịp — thử bài nhạc khác (wav/mp3 có trống rõ)")

    size = FRAME_SIZES[target]
    fps = 30
    infos = [media_info(f) for f in files]
    for f, inf in zip(files, infos):
        if not inf["width"]:
            raise RuntimeError(f"'{f.name}' không có luồng video")
    max_src = max((inf["duration"] or 1) for inf in infos)

    # ghép các nhịp thành đoạn >= 0.35s, tối đa max_seg đoạn
    MIN_SEG = 0.35
    cuts = [0.0]
    for t in beat_times:
        if t - cuts[-1] >= MIN_SEG:
            cuts.append(float(t))
        if len(cuts) > max_seg:
            break
    # đoạn đuôi không dài quá clip dài nhất (tránh video ngắn hơn -t)
    tail = min(4.0, max(MIN_SEG, max_src - 0.1))
    if cuts[-1] < music_dur and len(cuts) <= max_seg and music_dur - cuts[-1] >= MIN_SEG:
        cuts.append(min(music_dur, cuts[-1] + tail))

    cursors = [0.0] * len(files)
    tmp_clips = []
    try:
        actual_end = 0.0  # tổng thời lượng THẬT đã cắt — bù trừ lệch lượng tử 30fps
        n_segs = len(cuts) - 1
        for i in range(n_segs):
            _cancel_point(job_id)
            si = i % len(files)
            src_dur = infos[si]["duration"] or 1
            # thời lượng yêu cầu = mốc nhịp kế tiếp trừ vị trí thật hiện tại
            d = max(0.15, cuts[i + 1] - actual_end)
            d = min(d, src_dur - 0.05)  # không cắt quá độ dài clip nguồn
            if cursors[si] + d > src_dur - 0.05:
                cursors[si] = 0.0  # hết clip -> quay lại đầu
            tc = TMP / f"bs_{job_id}_{i:03d}.mp4"
            tmp_clips.append(tc)  # append TRƯỚC khi cắt để finally dọn cả file dở
            _extract_norm_clip(job_id, files[si], cursors[si], d, size, fps, tc,
                               keep_audio=False)
            cursors[si] += d
            actual_end += media_info(tc)["duration"] or d
            _set(job_id, progress=8 + (i + 1) / n_segs * 72,
                 message=f"Cắt theo nhịp: {i + 1}/{n_segs} đoạn (~{tempo:.0f} BPM)")

        _set(job_id, progress=82, message="Nối đoạn + ghép nhạc...")
        lst = TMP / f"bs_{job_id}.txt"
        lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in tmp_clips),
                       encoding="utf-8")
        tmp_clips.append(lst)
        out = unique_out(f"{files[0].stem}_beatsync", ".mp4")
        total = actual_end
        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(music),
               "-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]
        r = _run_tracked(job_id, cmd)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Nối đoạn thất bại: {r.stderr_text[-400:]}")
        _set(job_id, message=f"Xong — {n_segs} đoạn theo ~{tempo:.0f} BPM, {total:.1f}s")
        return [out_entry(out)]
    finally:
        for tc in tmp_clips:
            tc.unlink(missing_ok=True)


# ---------------------------------------------------------------- job: audio enhance
def job_audio_enhance(job_id: str, src: Path, denoise: bool, loudness: bool):
    """Khử ồn + chuẩn hoá âm lượng chuẩn mạng xã hội (loudnorm -16 LUFS)."""
    info = media_info(src)
    if not info["has_audio"]:
        raise RuntimeError("Tệp không có âm thanh")
    dur = info["duration"] or 1
    af = ["highpass=f=70"]
    if denoise:
        af.append("afftdn=nf=-28")
    if loudness:
        af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    _set(job_id, message="Xử lý âm thanh: " +
         " + ".join(x for x, on in (("khử ồn", denoise), ("chuẩn hoá -16 LUFS", loudness)) if on))
    if info["width"]:
        out = unique_out(f"{src.stem}_audio", ".mp4")
        # video copy nếu codec hợp mp4; vp8/vp9/khác → re-encode x264
        vcopy = (info.get("vcodec") or "") in ("h264", "hevc", "mpeg4", "av1")
        vopts = ["-c:v", "copy"] if vcopy else \
            ["-c:v", "libx264", "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p"]
        args = ["-i", str(src), "-af", ",".join(af)] + vopts + \
               ["-c:a", "aac", "-b:a", "192k", str(out)]
    else:
        out = unique_out(f"{src.stem}_clean", ".mp3")
        args = ["-i", str(src), "-af", ",".join(af),
                "-c:a", "libmp3lame", "-b:a", "320k", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr), job_id)
    return [out_entry(out)]


# ---------------------------------------------------------------- job: brand (title + logo)
def _title_ass(text: str, w: int, h: int, dur: float, persist: str = "") -> str:
    """ASS tiêu đề lớn giữa màn hình, fade in/out; persist = dòng chữ ký nhỏ góc dưới."""
    size = max(28, int(h * 0.075))
    small = max(16, int(h * 0.028))
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Title,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,1,3.5,1.5,5,60,60,40,163\n"
        f"Style: Sign,Arial,{small},&H60FFFFFF,&H60FFFFFF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,1.2,0,3,30,30,26,163\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    if text:
        lines.append(f"Dialogue: 0,{ass_ts(0.2)},{ass_ts(0.2 + dur)},Title,,0,0,0,,"
                     rf"{{\fad(350,350)}}{ass_escape(text)}")
    if persist:
        lines.append(f"Dialogue: 0,{ass_ts(0)},{ass_ts(359999)},Sign,,0,0,0,,"
                     f"{ass_escape(persist)}")
    return head + "\n".join(lines) + "\n"


LOGO_CORNERS = {
    "tl": "24:24", "tr": "main_w-overlay_w-24:24",
    "bl": "24:main_h-overlay_h-24", "br": "main_w-overlay_w-24:main_h-overlay_h-24",
}


def job_brand(job_id: str, src: Path, title: str, sign: str, logo,
              corner: str, opacity: float, title_dur: float):
    """Đóng dấu thương hiệu: tiêu đề mở đầu + chữ ký + logo watermark PNG."""
    info = media_info(src)
    if not info["width"]:
        raise RuntimeError("Tệp không có luồng video")
    dur = info["duration"] or 1
    vw, vh = info["width"], info["height"]
    out = unique_out(f"{src.stem}_brand", ".mp4")
    _set(job_id, message="Ghi tiêu đề / logo vào video...")

    ass_path = None
    try:
        cmd = ["-i", str(src)]
        filters = []
        vin = "[0:v]"
        if title or sign:
            ass_path = TMP / f"brand_{job_id}.ass"
            ass_path.write_text(_title_ass(title, vw, vh, title_dur, sign),
                                encoding="utf-8")
            filters.append(f"{vin}subtitles={ass_path.name}[vt]")
            vin = "[vt]"
        if logo is not None:
            cmd += ["-i", str(logo)]
            logo_w = max(48, int(vw * 0.14))
            filters.append(f"[1:v]format=rgba,colorchannelmixer=aa={opacity},"
                           f"scale={logo_w}:-1[lg]")
            filters.append(f"{vin}[lg]overlay={LOGO_CORNERS[corner]}[vout]")
            vin = "[vout]"
        if not filters:
            raise RuntimeError("Cần ít nhất tiêu đề, chữ ký hoặc logo")
        # nếu chỉ có subtitles (không logo) thì nhãn cuối là [vt]
        # audio: copy nếu codec hợp mp4, không thì re-encode aac (webm/opus, pcm...)
        acopy = (info.get("acodec") or "") in ("aac", "mp3")
        args = cmd + ["-filter_complex", ";".join(filters),
                      "-map", vin, "-map", "0:a?",
                      "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                      "-pix_fmt", "yuv420p"] + \
            (["-c:a", "copy"] if acopy else ["-c:a", "aac", "-b:a", "192k"]) + [str(out)]
        # subtitles= cần cwd chứa file .ass (cwd trick, không dùng os.chdir)
        run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr),
                            job_id, cwd=str(TMP))
        return [out_entry(out)]
    finally:
        if ass_path:
            ass_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- job: audiogram
def job_audiogram(job_id: str, src: Path, target: str, title: str):
    """Biến audio (hoặc audio của video) thành video sóng nhạc để đăng MXH."""
    info = media_info(src)
    if not info["has_audio"]:
        raise RuntimeError("Tệp không có âm thanh")
    dur = info["duration"] or 1
    w, h = FRAME_SIZES[target]
    wave_h = int(h * 0.26)
    out = unique_out(f"{src.stem}_audiogram", ".mp4")
    _set(job_id, message="Dựng audiogram sóng nhạc...")

    fc = (f"color=c=0x0F0F12:s={w}x{h}:d={dur:.3f}:r=25[bg];"
          f"[0:a]showwaves=s={w}x{wave_h}:mode=cline:rate=25:colors=0xFFB23F[wv];"
          f"[bg][wv]overlay=0:{int(h * 0.52)}:shortest=1[v1]")
    ass_path = None
    try:
        vin = "[v1]"
        if title:
            ass_path = TMP / f"ag_{job_id}.ass"
            ass_path.write_text(_title_ass(title, w, h, dur), encoding="utf-8")
            fc += f";[v1]subtitles={ass_path.name}[vout]"
            vin = "[vout]"
        args = ["-i", str(src), "-filter_complex", fc,
                "-map", vin, "-map", "0:a",
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-t", f"{dur:.3f}", str(out)]
        run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=pr),
                            job_id, cwd=str(TMP))
        return [out_entry(out)]
    finally:
        if ass_path:
            ass_path.unlink(missing_ok=True)


# ================================================================ wave 4 (v0.6) — LLM local + face AI
LLM_REPO = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
YUNET_ONNX = BINARIES / "yunet" / "face_detection_yunet_2023mar.onnx"

_llm_cache = None
_llm_lock = threading.Lock()


def llm_available() -> bool:
    if not IS_MAC:
        return False
    try:
        import mlx_lm  # noqa: F401
        return True
    except ImportError:
        return False


# --- Claude qua gói subscription (Claude Code CLI headless — như AI-LMS chấm bài)
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
_CLAUDE_SEM = threading.Semaphore(2)  # trần số lệnh Claude song song


def claude_available() -> bool:
    return bool(CLAUDE_BIN) and Path(CLAUDE_BIN).exists()


def claude_generate(prompt: str, job_id: str = None, model: str = None,
                    timeout: int = 420) -> str:
    """Gọi Claude bằng gói sub của người dùng (claude -p, prompt qua stdin).
    Video không rời máy — chỉ văn bản trong prompt được gửi đi."""
    if not claude_available():
        raise RuntimeError("Chưa cài / chưa đăng nhập Claude Code CLI trên máy")
    model = model or os.environ.get("LS_CLAUDE_MODEL", "sonnet")
    # BẢO MẬT: --tools "" vô hiệu hoá MỌI tool của Claude Code — prompt chứa
    # input người dùng + tên file (không tin cậy), nên agent chỉ được sinh text,
    # KHÔNG được chạy Bash/Read/Write dù prompt có bị tiêm chỉ thị độc.
    with _CLAUDE_SEM:  # chặn tối đa 2 lệnh Claude song song (khỏi nghẽn FastAPI)
        proc = subprocess.Popen(
            [CLAUDE_BIN, "-p", "--output-format", "json", "--model", model,
             "--tools", "", "--disallowedTools", "Bash", "Read", "Write", "Edit",
             "WebFetch", "WebSearch"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        if job_id:
            _track_proc(job_id, proc)
        try:
            so, se = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"Claude CLI không phản hồi sau {timeout}s")
    if job_id:
        _cancel_point(job_id)
    if proc.returncode != 0:
        raise RuntimeError(f"Claude CLI lỗi: {(se or so)[-400:]}")
    try:
        env = json.loads(so)
    except json.JSONDecodeError:
        return so  # CLI đổi định dạng — trả thô, _extract_json phía trên tự lo
    if not isinstance(env, dict):
        raise RuntimeError("Claude CLI trả định dạng lạ (không phải object)")
    if env.get("is_error"):
        raise RuntimeError(f"Claude trả lỗi: {str(env.get('result'))[:300]}")
    return env.get("result") or ""


def llm_generate(prompt: str, max_tokens: int = 1500, job_id: str = None,
                 engine: str = "local") -> str:
    """Router não AI: 'claude' → gói sub qua CLI; 'local' → Qwen3 4B MLX.
    Qwen: nạp model 1 lần, khoá tuần tự; trong lúc chờ khoá vẫn phản hồi hủy job."""
    # Chọn engine khả dụng: nếu engine yêu cầu không có, thử engine kia
    if engine == "claude" and not claude_available():
        engine = "local"
    if engine == "local" and not llm_available() and claude_available():
        engine = "claude"  # máy chỉ có Claude (vd không cài mlx-lm) → khỏi crash
    if engine == "claude":
        return claude_generate(prompt, job_id=job_id)
    global _llm_cache
    from mlx_lm import load, generate
    while not _llm_lock.acquire(timeout=1.0):
        if job_id and job_id in CANCEL_REQUESTED:
            raise JobCancelled()
    try:
        if job_id and job_id in CANCEL_REQUESTED:
            raise JobCancelled()
        if _llm_cache is None:
            _llm_cache = load(LLM_REPO)
        model, tokenizer = _llm_cache
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            add_generation_prompt=True, tokenize=False)
        out = generate(model, tokenizer, prompt=text,
                       max_tokens=max_tokens, verbose=False)
        try:  # trả lại RAM buffer KV cho hệ (giữ weights)
            import mlx.core as mx
            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass
        return out
    finally:
        _llm_lock.release()


def _extract_json(text: str):
    """Lấy khối JSON đầu tiên trong output LLM — quét cân bằng ngoặc (không dùng
    regex tham lam vì lời dẫn/chú thích quanh JSON hay chứa ngoặc lạc)."""
    text = re.sub(r"```(?:json)?", "", text)
    i = 0
    while i < len(text):
        ch = text[i]
        if ch not in "[{":
            i += 1
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except json.JSONDecodeError:
                        break  # khối này hỏng — thử khối bắt đầu muộn hơn
        i += 1
    raise ValueError("LLM không trả về JSON hợp lệ")


def cv2_available() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _transcript_lines(seg_list) -> list:
    return [f"[{s.start:.1f}-{s.end:.1f}] {s.text.strip()}"
            for s in seg_list if s.text.strip()]


# ---------------------------------------------------------------- job: AI highlights → shorts
class _ShiftSeg:
    """Segment con trong cửa sổ highlight, timestamps dời về 0 để burn caption."""
    __slots__ = ("text", "start", "end", "words")

    def __init__(self, text, start, end, words):
        self.text, self.start, self.end, self.words = text, start, end, words


class _ShiftWord:
    __slots__ = ("word", "start", "end")

    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


def _window_segments(seg_list, t0: float, t1: float):
    """Lọc các từ trong [t0,t1] từ transcript gốc, dời mốc thời gian về 0."""
    out = []
    for s in seg_list:
        if s.end < t0 or s.start > t1:
            continue
        words = [_ShiftWord(w.word, max(0.0, w.start - t0), max(0.05, w.end - t0))
                 for w in (getattr(s, "words", None) or [])
                 if not (w.end < t0 or w.start > t1)]
        if words or (s.start >= t0 and s.end <= t1):
            out.append(_ShiftSeg(s.text, max(0.0, s.start - t0),
                                 max(0.1, min(s.end, t1) - t0), words))
    return out


def _pick_highlights(lines: list, dur: float, count: int, smin: float,
                     smax: float, job_id: str = None, engine: str = "local") -> list:
    """LLM chọn các khoảnh khắc đáng làm shorts. Transcript dài thì chia phần
    (Claude đọc 1 lần không cần chia nhờ context lớn)."""
    def ask(sub_lines, n):
        prompt = (
            "Bạn là biên tập viên video ngắn chuyên nghiệp. Dưới đây là transcript "
            "có mốc thời gian (giây) của một video dài.\n\n"
            + "\n".join(sub_lines) +
            f"\n\nChọn {n} khoảnh khắc HAY NHẤT để cắt thành video ngắn đăng "
            f"TikTok/Shorts: ưu tiên đoạn có hook mạnh, thông tin trọn vẹn, gây tò mò. "
            f"Mỗi đoạn dài {smin:.0f}-{smax:.0f} giây, KHÔNG chồng lấn nhau, "
            f"start/end phải nằm trong transcript.\n"
            'Trả về DUY NHẤT mảng JSON, không giải thích: '
            '[{"start": <giây>, "end": <giây>, "title": "<tiêu đề ngắn hấp dẫn>", '
            '"hook": "<1 câu vì sao đoạn này hay>"}]'
        )
        try:
            data = _extract_json(llm_generate(prompt, max_tokens=1200,
                                              job_id=job_id, engine=engine))
            return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
        except (ValueError, json.JSONDecodeError):
            return []

    # transcript quá dài → chọn sơ bộ theo từng phần rồi chung kết.
    # Claude (sonnet ~200k token) đọc phần lớn hơn Qwen; giữ dưới ~120k ký tự để
    # chừa cho prompt+output (tiếng Việt ~0.5+ token/ký tự) — quá dài thì tự chia.
    CHUNK = 11000 if engine != "claude" else 120000
    text_all = "\n".join(lines)
    if len(text_all) <= CHUNK:
        cands = ask(lines, count)
    else:
        cands = []
        parts, part, size = [], [], 0
        for ln in lines:
            part.append(ln)
            size += len(ln)
            if size >= CHUNK:
                parts.append(part)
                part, size = [], 0
        if part:
            parts.append(part)
        parts = parts[:8]  # chặn trần số lượt gọi LLM với video rất dài
        for pt in parts:
            if job_id:
                _cancel_point(job_id)
            cands += ask(pt, max(2, count // 2 + 1))
        if len(cands) > count:
            brief = [f'{i}: [{c.get("start")}-{c.get("end")}] '
                     f'{c.get("title", "")} — {c.get("hook", "")}'
                     for i, c in enumerate(cands)]
            prompt = ("Các đoạn ứng viên cho video ngắn:\n" + "\n".join(brief) +
                      f"\n\nChọn {count} đoạn hay nhất, đa dạng chủ đề. "
                      'Trả về DUY NHẤT mảng JSON các chỉ số: [0, 3, ...]')
            try:
                idx = _extract_json(llm_generate(prompt, max_tokens=200,
                                                 job_id=job_id, engine=engine))
                sel = []
                for i in (idx if isinstance(idx, list) else []):
                    try:
                        sel.append(cands[int(i)])
                    except (ValueError, TypeError, IndexError):
                        continue
                cands = sel or cands[:count]  # LLM trả rác → giữ ứng viên đầu
            except (ValueError, json.JSONDecodeError):
                cands = cands[:count]

    # validate: ép số, clamp vào video, đúng độ dài, bỏ chồng lấn
    picked = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e <= s or e - s < smin * 0.5:  # LLM chọn đoạn rác quá ngắn — bỏ TRƯỚC khi clamp
            continue
        s = max(0.0, min(s, dur - smin))
        e = min(dur, max(e, s + smin))
        if e - s > smax:
            e = s + smax
        if e - s < smin - 0.5:
            continue
        if any(not (e <= q["start"] or s >= q["end"]) for q in picked):
            continue
        picked.append({"start": round(s, 2), "end": round(e, 2),
                       "title": str(c.get("title", ""))[:80],
                       "hook": str(c.get("hook", ""))[:160]})
        if len(picked) >= count:
            break
    picked.sort(key=lambda x: x["start"])
    return picked


REFRAME916_FC = ("[0:v]split[a][b];"
                 "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
                 "crop=1080:1920,boxblur=24:6[bg];"
                 "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
                 "[bg][fg]overlay=(W-w)/2:(H-h)/2[rf]")


def job_highlights(job_id: str, src: Path, p: dict):
    """AI tìm khoảnh khắc hay trong video dài → tự cắt thành shorts hoàn chỉnh
    (caption karaoke + khung dọc 9:16 nền mờ) — kiểu Opus Clip nhưng 100% local."""
    count = min(6, max(1, int(p.get("count", 3))))
    smin = min(60.0, max(5.0, float(p.get("min_dur", 15))))
    smax = min(120.0, max(smin + 5, float(p.get("max_dur", 60))))
    make = bool(p.get("make_shorts", True))
    info = media_info(src)
    dur = info["duration"] or 1
    if dur < smin * 1.5:
        raise RuntimeError(f"Video quá ngắn ({dur:.0f}s) — cần dài hơn {smin * 1.5:.0f}s")

    model_size = p.get("model") if p.get("model") in WHISPER_MODELS else "base"
    language = p.get("language") if p.get("language") in ("vi", "en") else None
    seg_list, _lines, lang = _speech_recognize(
        job_id, src, model_size, language, p.get("engine"), dur, 3, 45)
    lines = _transcript_lines(seg_list)
    if not lines:
        raise RuntimeError("Video không có lời thoại — AI highlights cần giọng nói")
    _cancel_point(job_id)

    ai = "claude" if p.get("ai") == "claude" and claude_available() else "local"
    brain = "Claude (gói sub)" if ai == "claude" else "Qwen3 local"
    _set(job_id, progress=48, message=f"AI ({brain}) đang chọn khoảnh khắc hay...")
    picked = _pick_highlights(lines, dur, count, smin, smax, job_id, engine=ai)
    if not picked:
        raise RuntimeError("AI không chọn được đoạn phù hợp — thử giảm độ dài tối thiểu")
    _cancel_point(job_id)

    outputs = []
    tmp_ass = []
    try:
        for i, hl in enumerate(picked):
            _cancel_point(job_id)
            t0, t1 = hl["start"], hl["end"]
            d = t1 - t0
            _set(job_id, progress=62 + i / len(picked) * 33,
                 message=f"Dựng short {i + 1}/{len(picked)}: {hl['title'][:40]}")
            safe_title = re.sub(r"[^\w\-. ]+", "", hl["title"])[:40].strip() or f"doan{i + 1}"
            out = unique_out(f"{src.stem}_short{i + 1}_{safe_title}", ".mp4")
            if make:
                wsegs = _window_segments(seg_list, t0, t1)
                chunks = chunk_words(wsegs, int(p.get("max_words", 3)),
                                     bool(p.get("uppercase", False)))
                fc = REFRAME916_FC
                vout = "[rf]"
                cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{t0:.2f}", "-t", f"{d:.2f}", "-i", str(src)]
                if chunks:
                    ass = TMP / f"hl_{job_id}_{i}.ass"
                    font = p.get("font") if p.get("font") in SUB_FONTS else "Arial"
                    ass.write_text(build_ass(chunks, 1080, 1920, font,
                                             p.get("size", "M"),
                                             p.get("effect", "karaoke"),
                                             p.get("position", "bottom")),
                                   encoding="utf-8")
                    tmp_ass.append(ass)
                    fc += f";[rf]subtitles={ass.name}[vout]"
                    vout = "[vout]"
                cmd += ["-filter_complex", fc, "-map", vout, "-map", "0:a?",
                        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
                r = _run_tracked(job_id, cmd, cwd=str(TMP))
            else:
                cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{t0:.2f}", "-t", f"{d:.2f}", "-i", str(src),
                       "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
                r = _run_tracked(job_id, cmd)
            if r.returncode != 0:
                raise RuntimeError(f"Cắt short {i + 1} thất bại: {r.stderr_text[-300:]}")
            outputs.append(out_entry(out))

        def mmss(t):
            return f"{int(t // 60):02d}:{int(t % 60):02d}"
        note = unique_out(f"{src.stem}_highlights", ".txt")
        note.write_text("\n".join(
            f"{i + 1}. [{mmss(h['start'])}–{mmss(h['end'])}] {h['title']}\n   → {h['hook']}"
            for i, h in enumerate(picked)) + "\n", encoding="utf-8")
        outputs.append(out_entry(note))
        _set(job_id, message=f"Xong — {len(picked)} shorts (ngôn ngữ: {lang})")
        return outputs
    finally:
        for a in tmp_ass:
            a.unlink(missing_ok=True)


# ---------------------------------------------------------------- job: AI content writer
def job_content(job_id: str, src: Path, p: dict):
    """LLM local viết tiêu đề/mô tả/hashtags/chapters từ transcript video."""
    platform = p.get("platform") if p.get("platform") in ("tiktok", "youtube") else "youtube"
    info = media_info(src)
    dur = info["duration"] or 1
    model_size = p.get("model") if p.get("model") in WHISPER_MODELS else "base"
    language = p.get("language") if p.get("language") in ("vi", "en") else None
    seg_list, _l, lang = _speech_recognize(
        job_id, src, model_size, language, p.get("engine"), dur, 3, 60)
    lines = _transcript_lines(seg_list)
    if not lines:
        raise RuntimeError("Video không có lời thoại để viết nội dung")
    _cancel_point(job_id)

    text_all = "\n".join(lines)
    if len(text_all) > 15000:  # transcript rất dài → giữ đầu + cuối
        text_all = text_all[:9000] + "\n[...phần giữa lược bớt...]\n" + text_all[-5000:]

    ai = "claude" if p.get("ai") == "claude" and claude_available() else "local"
    brain = "Claude (gói sub)" if ai == "claude" else "Qwen3 local"
    _set(job_id, progress=65, message=f"AI ({brain}) đang viết nội dung...")
    chapters_req = ("\n5. CHAPTERS: các mốc chương dạng 'mm:ss Tên phần' (dựa mốc giây "
                    "trong transcript, chương đầu là 00:00)") if dur > 120 else ""
    prompt = (
        f"Bạn là chuyên gia nội dung {'TikTok/Shorts' if platform == 'tiktok' else 'YouTube'} "
        f"người Việt. Transcript video ({dur:.0f}s, ngôn ngữ {lang}):\n\n{text_all}\n\n"
        "Viết bằng tiếng Việt, định dạng Markdown với đúng các mục:\n"
        "1. TIÊU ĐỀ: 3 phương án tiêu đề giật hook (mỗi cái ≤70 ký tự)\n"
        "2. HOOK MỞ ĐẦU: 1-2 câu thoại nên nói trong 3 giây đầu\n"
        "3. MÔ TẢ: đoạn mô tả chuẩn SEO (2-4 câu + CTA)\n"
        "4. HASHTAGS: 8-12 hashtag phù hợp" + chapters_req
    )
    content = llm_generate(prompt, max_tokens=1600, job_id=job_id, engine=ai)
    _cancel_point(job_id)
    out = unique_out(f"{src.stem}_content", ".md")
    out.write_text(f"# Nội dung cho: {src.name} ({platform})\n\n{content}\n",
                   encoding="utf-8")
    _set(job_id, message=f"Xong — tiêu đề/mô tả/hashtags{' /chapters' if chapters_req else ''}")
    return [out_entry(out)]


# ---------------------------------------------------------------- job: B-roll tự động (Pexels)
def pexels_key() -> str:
    return os.environ.get("PEXELS_API_KEY", "").strip()


def pexels_available() -> bool:
    return bool(pexels_key())


def _pexels_search_download(query: str, orientation: str, want_dur: float,
                            dest: Path) -> bool:
    """Tìm 1 clip stock hợp từ khoá trên Pexels rồi tải về dest. True nếu ok."""
    import httpx
    headers = {"Authorization": pexels_key()}
    params = {"query": query, "per_page": 8, "orientation": orientation}
    try:
        r = httpx.get("https://api.pexels.com/videos/search", headers=headers,
                      params=params, timeout=25)
        if r.status_code != 200:
            return False
        vids = r.json().get("videos", [])
    except (httpx.HTTPError, ValueError):
        return False
    if not vids:
        return False
    # ưu tiên clip đủ dài, chọn file .mp4 độ phân giải vừa (~720-1080 chiều dài)
    vids.sort(key=lambda v: abs((v.get("duration") or 0) - max(want_dur, 4)))
    for v in vids:
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4" and f.get("link")]
        if not files:
            continue
        files.sort(key=lambda f: abs((f.get("height") or 0) - 1080))
        link = files[0]["link"]
        try:
            with httpx.stream("GET", link, timeout=60, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    continue
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 16):
                        fh.write(chunk)
            if dest.exists() and dest.stat().st_size > 10000:
                return True
        except (httpx.HTTPError, OSError):
            continue
    return False


def _pick_broll_cues(lines: list, dur: float, count: int,
                     job_id: str, engine: str) -> list:
    """AI đọc transcript → chọn các đoạn nên chèn B-roll + từ khoá tìm (tiếng Anh
    để Pexels ra kết quả tốt)."""
    text = "\n".join(lines)
    if len(text) > (120000 if engine == "claude" else 11000):
        text = text[:(120000 if engine == "claude" else 11000)]
    prompt = (
        "Bạn là biên tập video. Dưới đây là transcript có mốc thời gian (giây) "
        "của một video người nói.\n\n" + text +
        f"\n\nChọn {count} đoạn nên chèn B-roll (cảnh minh hoạ) đè lên. Mỗi đoạn "
        "dài 2-5 giây, KHÔNG chồng lấn, nằm trong video. Từ khoá tìm cảnh phải "
        "bằng TIẾNG ANH, cụ thể, hợp nội dung đang nói (vd 'city traffic aerial', "
        "'person typing laptop').\n"
        'Trả về DUY NHẤT mảng JSON: [{"start": <giây>, "end": <giây>, '
        '"query": "<từ khoá tiếng Anh>"}]'
    )
    try:
        data = _extract_json(llm_generate(prompt, max_tokens=1000,
                                          job_id=job_id, engine=engine))
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    cues = []
    for c in data:
        if not isinstance(c, dict):
            continue
        try:
            s, e = float(c["start"]), float(c["end"])
            q = str(c.get("query") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        q = re.sub(r"[^\w\s\-]", " ", q).strip()  # chỉ giữ chữ/số/space/gạch (từ khoá tìm)
        q = re.sub(r"\s+", " ", q)
        if not q or e <= s:
            continue
        s = max(0.0, min(s, dur - 1.5))
        e = min(dur, min(e, s + 6.0))
        if e - s < 1.5:
            continue
        if any(not (e <= x["end"] or s >= x["start"]) for x in cues):
            continue
        cues.append({"start": round(s, 2), "end": round(e, 2), "query": q[:80]})
        if len(cues) >= count:
            break
    cues.sort(key=lambda x: x["start"])
    return cues


def job_broll(job_id: str, src: Path, p: dict):
    """AI đọc lời thoại → chọn đoạn + từ khoá → tải cảnh stock từ Pexels → chèn
    B-roll đè lên video (giữ nguyên tiếng gốc)."""
    if not pexels_available():
        raise RuntimeError("Chưa có PEXELS_API_KEY (thêm vào backend/.env)")
    info = media_info(src)
    w, h = info["width"], info["height"]
    if not w or not h:
        raise RuntimeError("Tệp không có luồng video")
    dur = info["duration"] or 1
    fps = info["fps"] or 30
    orient = "landscape" if w > h * 1.2 else "portrait" if h > w * 1.2 else "square"
    count = min(6, max(1, _safe_int(p.get("count"), 3)))

    model_size = p.get("model") if p.get("model") in WHISPER_MODELS else "base"
    language = p.get("language") if p.get("language") in ("vi", "en") else None
    seg_list, _l, _lang = _speech_recognize(
        job_id, src, model_size, language, p.get("engine"), dur, 3, 40)
    lines = _transcript_lines(seg_list)
    if not lines:
        raise RuntimeError("Video không có lời thoại — B-roll cần biết đang nói gì")
    _cancel_point(job_id)

    ai = "claude" if p.get("ai") == "claude" and claude_available() else "local"
    if not llm_available() and claude_available():
        ai = "claude"
    _set(job_id, progress=44, message="AI chọn đoạn chèn B-roll + từ khoá...")
    cues = _pick_broll_cues(lines, dur, count, job_id, ai)
    if not cues:
        raise RuntimeError("AI không chọn được đoạn B-roll phù hợp")
    _cancel_point(job_id)

    tmp = []
    try:
        clips = []  # (cue, normalized_path)
        for i, cue in enumerate(cues):
            _cancel_point(job_id)
            _set(job_id, progress=48 + i / len(cues) * 34,
                 message=f"Tải cảnh {i + 1}/{len(cues)}: {cue['query']}")
            raw = TMP / f"br_raw_{job_id}_{i}.mp4"
            tmp.append(raw)  # thêm TRƯỚC khi tải để finally dọn cả file tải hỏng dở
            if not _pexels_search_download(cue["query"], orient, cue["end"] - cue["start"], raw):
                continue  # không tìm được cảnh này → bỏ qua, không làm hỏng cả job
            norm = TMP / f"br_norm_{job_id}_{i}.mp4"
            tmp.append(norm)
            seg_dur = cue["end"] - cue["start"]
            # phủ khung, cắt/lặp đúng độ dài đoạn, khớp fps, bỏ tiếng
            r = _run_tracked(job_id, [
                FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                "-stream_loop", "-1", "-i", str(raw), "-t", f"{seg_dur:.3f}",
                "-an", "-vf",
                f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},fps={fps},setsar=1",
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", str(norm)])
            if r.returncode != 0 or not norm.exists():
                continue
            clips.append((cue, norm))

        if not clips:
            raise RuntimeError("Không tải được cảnh B-roll nào từ Pexels (thử từ khoá/khác)")

        _set(job_id, progress=84, message=f"Chèn {len(clips)} đoạn B-roll vào video...")
        out = unique_out(f"{src.stem}_broll", ".mp4")
        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
        for _c, np_ in clips:
            cmd += ["-i", str(np_)]
        # dời PTS mỗi B-roll về đúng mốc đoạn rồi overlay có enable theo thời gian
        parts, prev = [], "[0:v]"
        for i, (cue, _np) in enumerate(clips):
            lab = f"[v{i + 1}]" if i < len(clips) - 1 else "[vout]"
            parts.append(f"[{i + 1}:v]setpts=PTS-STARTPTS+{cue['start']:.3f}/TB[b{i}]")
            parts.append(f"{prev}[b{i}]overlay=enable='between(t,{cue['start']:.3f},"
                         f"{cue['end']:.3f})'{lab}")
            prev = lab
        fc = ";".join(parts)
        cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a?",
                "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)]
        r = _run_tracked(job_id, cmd)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Chèn B-roll thất bại: {r.stderr_text[-400:]}")
        note = unique_out(f"{src.stem}_broll", ".txt")
        note.write_text("\n".join(
            f"[{c['start']:.1f}-{c['end']:.1f}s] {c['query']}" for c, _ in clips) + "\n",
            encoding="utf-8")
        _set(job_id, message=f"Xong — chèn {len(clips)} đoạn B-roll từ Pexels")
        return [out_entry(out), out_entry(note)]
    finally:
        for t in tmp:
            t.unlink(missing_ok=True)


# ---------------------------------------------------------------- job: lesson (bài giảng → giáo án + quiz)
AILMS_DB = Path.home() / "ai-lms" / "data" / "lms.db"
AILMS_DEPARTMENTS = {"Marketing", "Sales", "Kế toán - Tài chính", "Nhân sự",
                     "Vận hành", "Kỹ thuật - IT", "Ban lãnh đạo"}


def _safe_int(v, default: int) -> int:
    """Ép về int an toàn — null/chuỗi lạ/float đều không làm sập job."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def ailms_available() -> bool:
    return AILMS_DB.exists()


def _push_to_ailms(title: str, summary: str, content: str,
                   level: int, department) -> int:
    """Chèn 1 bài học vào SQLite của AI-LMS (cùng máy). WAL cho phép ghi
    song song trong lúc AI-LMS đang đọc."""
    import sqlite3
    if not AILMS_DB.exists():
        raise RuntimeError("Không thấy CSDL AI-LMS (~/ai-lms/data/lms.db)")
    con = sqlite3.connect(str(AILMS_DB), timeout=10)
    try:
        dept = department if department in AILMS_DEPARTMENTS else None
        ord_row = con.execute(
            "SELECT COALESCE(MAX(ord),0)+1 FROM lessons WHERE level=? AND "
            "((department IS NULL AND ? IS NULL) OR department=?)",
            (level, dept, dept)).fetchone()
        rubric = ("Mức độ hoàn thành yêu cầu (40đ)\n"
                  "Chất lượng và tính thực tế (30đ)\n"
                  "Quá trình sử dụng AI: prompt và vòng lặp (30đ)")
        cur = con.execute(
            "INSERT INTO lessons (level, department, ord, title, summary, content, rubric) "
            "VALUES (?,?,?,?,?,?,?)",
            (level, dept, ord_row[0], title, summary, content, rubric))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def job_lesson(job_id: str, src: Path, p: dict):
    """Bài giảng (video/audio) → transcript → AI viết giáo án + câu hỏi quiz,
    xuất .md; tuỳ chọn đẩy thẳng vào hệ AI-LMS."""
    info = media_info(src)
    dur = info["duration"] or 1
    model_size = p.get("model") if p.get("model") in WHISPER_MODELS else "base"
    language = p.get("language") if p.get("language") in ("vi", "en") else None
    seg_list, _l, lang = _speech_recognize(
        job_id, src, model_size, language, p.get("engine"), dur, 3, 55)
    lines = _transcript_lines(seg_list)
    if not lines:
        raise RuntimeError("Bài giảng không có lời thoại để soạn giáo án")
    _cancel_point(job_id)

    ai = "claude" if p.get("ai") == "claude" and claude_available() else "local"
    if p.get("ai") == "claude" and not claude_available():
        ai = "local"
    if not llm_available() and claude_available():
        ai = "claude"
    brain = "Claude (gói sub)" if ai == "claude" else "Qwen3 local"

    text_all = "\n".join(l.split("] ", 1)[-1] for l in lines)  # bỏ timestamp
    cap = 120000 if ai == "claude" else 12000
    if len(text_all) > cap:
        text_all = text_all[:cap] + "\n[...lược bớt...]"
    n_quiz = min(15, max(3, _safe_int(p.get("quiz"), 5)))

    _set(job_id, progress=60, message=f"AI ({brain}) đang soạn giáo án + {n_quiz} câu hỏi...")
    prompt = (
        "Bạn là giáo viên soạn giáo án. Dưới đây là lời thoại một bài giảng "
        f"(ngôn ngữ {lang}):\n\n{text_all}\n\n"
        f"Soạn giáo án tiếng Việt. Trả về DUY NHẤT một object JSON:\n"
        '{"title": "<tiêu đề bài học ngắn gọn>", '
        '"summary": "<tóm tắt 1-2 câu>", '
        '"content": "<nội dung Markdown: ## Mục tiêu bài học, ## Nội dung chính '
        '(các ý gạch đầu dòng), ## Ghi chú cho học viên, ## Câu hỏi ôn tập '
        f'({n_quiz} câu trắc nghiệm A/B/C/D kèm **Đáp án** cuối mỗi câu)>"}}'
    )
    try:
        data = _extract_json(llm_generate(prompt, max_tokens=3000,
                                          job_id=job_id, engine=ai))
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"AI không soạn được giáo án hợp lệ: {str(e)[:150]}")
    if not isinstance(data, dict) or not data.get("content"):
        raise RuntimeError("AI trả giáo án thiếu nội dung")

    title = str(data.get("title") or src.stem)[:200]
    summary = str(data.get("summary") or "")[:500]
    content = str(data["content"])
    _cancel_point(job_id)

    md = unique_out(f"{src.stem}_giaoan", ".md")
    md.write_text(f"# {title}\n\n> {summary}\n\n{content}\n", encoding="utf-8")
    outputs = [out_entry(md)]

    if p.get("push_lms"):
        _set(job_id, progress=95, message="Đẩy bài học sang AI-LMS...")
        level = _safe_int(p.get("level"), 1)
        if level not in (1, 2, 3, 4):
            level = 1
        dept = p.get("department") or None
        try:
            lid = _push_to_ailms(title, summary, content, level, dept)
            _set(job_id, message=f"Xong — giáo án ({brain}) + đã thêm vào AI-LMS (bài #{lid})")
            return outputs
        except Exception as e:  # noqa: BLE001 - giáo án đã có, chỉ báo lỗi bước đẩy
            _set(job_id, message=f"Xong giáo án nhưng đẩy AI-LMS lỗi: {str(e)[:120]}")
            return outputs
    _set(job_id, message=f"Xong — giáo án + {n_quiz} câu hỏi ({brain})")
    return outputs


# ================================================================ Phụ đề Viral 1 chạm (v1.0)
# Giới từ / liên từ tiếng Việt không nên đứng lẻ cuối dòng đầu (bước 3)
_VN_DANGLING = {
    "và", "hoặc", "nhưng", "mà", "thì", "là", "của", "cho", "với", "để", "vì",
    "nên", "khi", "nếu", "hay", "rồi", "ở", "trong", "trên", "dưới", "ra", "vào",
    "từ", "đến", "bằng", "về", "theo", "như", "bởi", "do", "tại", "qua", "cùng",
    "sẽ", "đã", "đang", "một", "các", "những", "này", "đó", "kia",
}


def _flatten_words(seg_list):
    """Lấy list từ có mốc thời gian cấp từ (bước 1 đã bật word_timestamps)."""
    words = []
    for s in seg_list:
        for w in (getattr(s, "words", None) or []):
            t = (w.word or "").strip()
            if t:
                words.append({"t": t, "s": float(w.start), "e": float(w.end)})
        if not getattr(s, "words", None):  # model không trả word → cả câu 1 "từ"
            t = (s.text or "").strip()
            if t:
                words.append({"t": t, "s": float(s.start), "e": float(s.end)})
    return words


def _viral_clusters(seg_list, max_line_chars=28, max_lines=2,
                    min_dur=1.2, max_dur=3.0):
    """Bước 2: gộp từ thành cụm hiển thị theo 3 ràng buộc đồng thời (≤2 dòng,
    ≤28 ký tự/dòng, 1.2-3.0s). Ưu tiên cắt tại dấu câu > khoảng lặng >250ms >
    giới hạn ký tự. Không tách rời một từ."""
    words = _flatten_words(seg_list)
    if not words:
        return []
    max_chars = max_line_chars * max_lines  # trần ký tự cả cụm (2 dòng)
    clusters, cur = [], []

    def chars(c):
        return sum(len(w["t"]) for w in c) + max(0, len(c) - 1)

    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        d = cur[-1]["e"] - cur[0]["s"]
        c = chars(cur)
        ends_punct = w["t"][-1] in ".,!?…;:"
        gap_next = (nxt["s"] - w["e"]) if nxt else 99.0
        # nếu thêm từ kế sẽ vượt trần ký tự/thời lượng → phải chốt ngay
        next_over = nxt and (c + 1 + len(nxt["t"]) > max_chars
                             or (nxt["e"] - cur[0]["s"]) > max_dur)
        close = False
        if not nxt:
            close = True
        elif c >= max_chars or d >= max_dur or next_over:
            close = True
        elif d >= min_dur and ends_punct:        # ưu tiên 1: dấu câu
            close = True
        elif d >= min_dur and gap_next > 0.25:    # ưu tiên 2: khoảng lặng
            close = True
        if close:
            clusters.append(cur)
            cur = []
    # cụm cuối quá ngắn → gộp vào cụm trước nếu vẫn trong trần
    if len(clusters) >= 2:
        last = clusters[-1]
        if last[-1]["e"] - last[0]["s"] < min_dur:
            merged = clusters[-2] + last
            if (merged[-1]["e"] - merged[0]["s"] <= max_dur
                    and chars(merged) <= max_chars):
                clusters[-2] = merged
                clusters.pop()
    return clusters


def _balance_lines(words, max_line_chars=28):
    """Bước 3: ngắt tối đa 2 dòng, cân bằng độ dài (chênh ≤20%), không để dòng
    đầu kết thúc bằng giới từ/liên từ lẻ. Trả về (line1_words, line2_words|None)."""
    texts = [w["t"] for w in words]
    total = sum(len(t) for t in texts) + max(0, len(texts) - 1)
    if total <= max_line_chars or len(texts) == 1:
        return words, None
    best, best_score = None, 1e9
    for k in range(1, len(texts)):  # dòng 1 = [0:k]
        l1 = " ".join(texts[:k])
        l2 = " ".join(texts[k:])
        if len(l1) > max_line_chars or len(l2) > max_line_chars:
            continue
        imbalance = abs(len(l1) - len(l2)) / max(1, max(len(l1), len(l2)))
        dangling = texts[k - 1].lower().strip(".,!?…;:") in _VN_DANGLING
        score = imbalance + (0.5 if imbalance > 0.20 else 0) + (1.0 if dangling else 0)
        if score < best_score:
            best_score, best = score, k
    if best is None:  # không chỗ nào ≤28/dòng → cắt giữa theo từ
        best = max(1, len(texts) // 2)
    return words[:best], words[best:]


# --- Preset chữ "Viral Trắng Viền Đen" (kích thước theo % khung để tự co giãn)
VIRAL_KEYWORD_ASS = r"{\c&H0000D4FF&}"   # vàng #FFD400 (ASS &HAABBGGRR)
VIRAL_WHITE_ASS = r"{\c&H00FFFFFF&}"


def _is_keyword(word_clean: str, kw_set: set) -> bool:
    if word_clean.lower() in kw_set:
        return True
    return bool(re.search(r"\d", word_clean))  # con số luôn là từ khoá quan trọng


def _viral_ass(clusters, w: int, h: int, params: dict) -> str:
    """Bước 4: sinh .ass theo preset Viral. fontsize/outline/marginV theo % khung."""
    # validate + clamp mọi tham số client (tránh giá trị rác làm libass vỡ SAU transcribe)
    fs = params.get("fontsize")
    fontsize = int(min(h * 0.12, max(h * 0.02, _safe_float(fs, h * 0.046))))
    ol = params.get("outline")
    outline = round(min(w * 0.03, max(0.0, _safe_float(ol, w * 0.007))), 1)
    mv = params.get("marginV")
    margin_v = int(min(h * 0.85, max(0.0, _safe_float(mv, h * 0.15))))
    # font: chỉ giữ chữ/số/space (chống chèn dấu phẩy / xuống dòng vào Style)
    font = re.sub(r"[^\w \-]", "", str(params.get("font") or "Be Vietnam Pro")).strip() \
        or "Be Vietnam Pro"
    kw_on = bool(params.get("keyword", False))
    kw_set = set(k.strip().lower() for k in (params.get("keywords") or []) if k.strip())
    karaoke = bool(params.get("karaoke", False))

    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nScaledBorderAndShadow: yes\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Viral,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,2,60,60,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def render_word(wd: dict) -> str:
        raw = ass_escape(wd["t"])
        clean = raw.strip(".,!?…;:\"'()")
        piece = raw
        if kw_on and _is_keyword(clean, kw_set):
            piece = VIRAL_KEYWORD_ASS + raw + VIRAL_WHITE_ASS
        if karaoke:
            cs = max(1, int(round((wd["e"] - wd["s"]) * 100)))
            piece = (r"{\kf%d}" % cs) + piece
        return piece

    lines = []
    for c in clusters:
        st = max(0.0, c[0]["s"])          # clamp: không âm, end > start
        en = max(st + 0.1, c[-1]["e"])
        l1, l2 = _balance_lines(c)
        prefix = r"{\2c&H00AAAAAA&}" if karaoke else ""  # chưa đọc = xám → sweep sang trắng
        t1 = " ".join(render_word(x) for x in l1)
        text = prefix + t1
        if l2:
            text += r"\N" + " ".join(render_word(x) for x in l2)
        lines.append(f"Dialogue: 0,{ass_ts(st)},{ass_ts(en)},Viral,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


def _viral_srt(clusters) -> str:
    """Xuất .srt (chữ thuần) để người dùng chỉnh tay."""
    out = []
    for n, c in enumerate(clusters, 1):
        l1, l2 = _balance_lines(c)
        txt = " ".join(x["t"] for x in l1)
        if l2:
            txt += "\n" + " ".join(x["t"] for x in l2)
        out.append(f"{n}\n{srt_ts(c[0]['s'])} --> {srt_ts(c[-1]['e'])}\n{txt}\n")
    return "\n".join(out) + "\n"


# --- Bước 5: chuỗi xử lý âm thanh (giọng) + ducking nhạc nền
def _viral_voice_af() -> str:
    """Khử ồn nhẹ + nén động 3:1 ngưỡng -18dB + chuẩn hoá -14 LUFS, chặn đỉnh
    thực ≤ -1 dBTP (alimiter brickwall vì loudnorm 1 lượt không đảm bảo TP)."""
    return ("highpass=f=80,afftdn=nf=-25,"
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=100,"
            "loudnorm=I=-14:TP=-1.5:LRA=11,"
            "alimiter=limit=0.82:level=false")  # ~-1.7 dBFS: chừa headroom cho AAC (lossy đẩy TP lên ~0.5dB) → file cuối ≤ -1 dBTP


def _viral_ai_keywords(seg_list, job_id, engine) -> list:
    """AI tự chọn danh từ/con số quan trọng làm từ khoá tô sáng."""
    lines = _transcript_lines(seg_list)
    text = "\n".join(lines)
    if len(text) > (100000 if engine == "claude" else 9000):
        text = text[:(100000 if engine == "claude" else 9000)]
    prompt = (
        "Đây là lời thoại một video tiếng Việt:\n\n" + text +
        "\n\nChọn 8-20 TỪ KHOÁ quan trọng nhất để tô sáng trong phụ đề viral "
        "(danh từ chính, tên riêng, con số, cụm gây chú ý). Chỉ 1 từ mỗi mục, "
        "viết thường, đúng như trong lời thoại.\n"
        'Trả về DUY NHẤT mảng JSON: ["từ1","từ2",...]'
    )
    try:
        data = _extract_json(llm_generate(prompt, max_tokens=400,
                                          job_id=job_id, engine=engine))
        return [str(x).strip().lower() for x in data if str(x).strip()][:30] \
            if isinstance(data, list) else []
    except (ValueError, json.JSONDecodeError):
        return []


def _run_viral_recognize(job_id, src, p, lo, hi):
    """Bước 1: nhận dạng cấp từ. Mặc định tiếng Việt, model large-v3."""
    model = p.get("model") if p.get("model") in WHISPER_MODELS else "large-v3"
    lang = p.get("language") or "vi"
    if lang not in ("vi", "en"):
        lang = "vi"
    info = media_info(src)
    seg_list, _lines, _lang = _speech_recognize(
        job_id, src, model, lang, p.get("engine"), info["duration"] or 1, lo, hi)
    return seg_list


def job_viral_caption(job_id: str, src: Path, p: dict):
    """Phụ đề Viral 1 chạm — pipeline 7 bước, xuất mp4 (burn) + .ass + .srt."""
    info = media_info(src)
    w, h = info["width"], info["height"]
    if not w or not h:
        raise RuntimeError("Tệp không có luồng video")
    dur = info["duration"] or 1

    # cụm: từ dữ liệu chỉnh tay, hoặc tự phân tích (bước 1-3)
    edited = p.get("clusters")
    if edited and isinstance(edited, list):
        seg_list = None
        clusters = []
        for c in edited:
            if not isinstance(c, dict):
                continue
            ws = c.get("words")
            if isinstance(ws, list) and ws:
                wl = [{"t": str(x["t"]), "s": _safe_float(x.get("s"), 0),
                       "e": _safe_float(x.get("e"), 0)}
                      for x in ws if isinstance(x, dict) and x.get("t") is not None]
                if wl:
                    clusters.append(wl)
            elif c.get("text"):
                s = _safe_float(c.get("start"), 0)
                e = _safe_float(c.get("end"), s + 1.5)
                if e <= s:
                    e = s + 1.0
                clusters.append([{"t": str(c["text"]).strip(), "s": s, "e": e}])
    else:
        seg_list = _run_viral_recognize(job_id, src, p, 3, 42)
        _cancel_point(job_id)
        clusters = _viral_clusters(seg_list)
    if not clusters:
        raise RuntimeError("Không tách được cụm phụ đề — video có lời thoại không?")

    # từ khoá tô sáng
    params = dict(p)
    if p.get("keyword"):
        kws = [k for k in (p.get("keywords") or []) if str(k).strip()]
        if not kws and seg_list is not None:  # để trống → AI tự chọn
            # TÔN TRỌNG lựa chọn offline: chỉ dùng Claude khi người dùng CHỌN claude
            # (không tự fallback → không gửi transcript ra ngoài ngoài ý muốn)
            want_claude = p.get("ai") == "claude"
            if want_claude and claude_available():
                _set(job_id, progress=46, message="Claude chọn từ khoá tô sáng...")
                kws = _viral_ai_keywords(seg_list, job_id, "claude")
            elif llm_available():
                _set(job_id, progress=46, message="AI local chọn từ khoá tô sáng...")
                kws = _viral_ai_keywords(seg_list, job_id, "local")
            # không có engine phù hợp → bỏ qua tô từ khoá (con số vẫn tự tô ở _is_keyword)
        params["keywords"] = kws
    _cancel_point(job_id)

    # bước 4: sinh .ass + .srt
    _set(job_id, progress=52, message="Áp preset chữ Viral, sinh .ass...")
    ass_text = _viral_ass(clusters, w, h, params)
    ass_path = unique_out(f"{src.stem}_viral", ".ass")
    ass_path.write_text(ass_text, encoding="utf-8")
    srt_path = ass_path.with_suffix(".srt")
    srt_path.write_text(_viral_srt(clusters), encoding="utf-8")

    # bước 6: end card (ảnh tĩnh cuối)
    endcard = None
    if p.get("endcard"):
        ec = safe_upload_path(str(p["endcard"]))
        if ec.is_file() and ec.suffix.lower() in IMAGE_EXT:
            endcard = ec

    # bước 5+7: xử lý âm thanh + burn phụ đề (libass) + mã hoá H.264 CRF18 slow
    _set(job_id, progress=58, message="Chuẩn âm thanh + cháy phụ đề (libass)...")
    out = unique_out(f"{src.stem}_viral", ".mp4")
    voice_af = _viral_voice_af()
    subs = f"subtitles={_ff_escape_path(ass_path)}:fontsdir={_ff_escape_path(FONTS_DIR)}"
    music = None
    if p.get("music"):
        mp = safe_upload_path(str(p["music"]))
        if mp.is_file():
            music = mp

    common_v = ["-c:v", "libx264", "-crf", "18", "-preset", "slow",
                "-pix_fmt", "yuv420p", "-colorspace", "bt709",
                "-color_primaries", "bt709", "-color_trc", "bt709"]
    if music is not None and info["has_audio"]:
        # nhạc nền + ducking -18dB khi có tiếng nói, nhả 400ms
        fc = (f"[0:v]{subs}[v];"
              f"[0:a]{voice_af},asplit=2[vo][sc];"
              "[1:a]volume=1.0[mus];"
              "[mus][sc]sidechaincompress=threshold=0.05:ratio=8:attack=5:"
              "release=400:makeup=1[duck];"
              "[vo][duck]amix=inputs=2:duration=first:normalize=0,"
              "alimiter=limit=0.82[a]")  # chừa headroom cho AAC → ≤ -1 dBTP
        args = ["-i", str(src), "-stream_loop", "-1", "-i", str(music),
                "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                "-shortest"] + common_v + ["-c:a", "aac", "-b:a", "192k", str(out)]
    elif music is not None:
        # video KHÔNG có tiếng nhưng người dùng thêm nhạc → dùng nhạc làm audio
        args = ["-i", str(src), "-stream_loop", "-1", "-i", str(music),
                "-vf", subs, "-map", "0:v", "-map", "1:a", "-shortest"] \
            + common_v + ["-c:a", "aac", "-b:a", "192k", str(out)]
    else:
        args = ["-i", str(src), "-vf", subs]
        if info["has_audio"]:
            args += ["-af", voice_af]
        args += common_v + ["-c:a", "aac", "-b:a", "192k", str(out)]
    run_ffmpeg_progress(args, dur, lambda pr: _set(job_id, progress=58 + pr * 0.30),
                        job_id)

    outputs = [out, ass_path, srt_path]
    if endcard is not None:
        _set(job_id, progress=92, message="Nối end card...")
        ec_dur = min(10.0, max(1.0, float(p.get("endcard_dur", 3.0))))
        ec_clip = TMP / f"vc_ec_{job_id}.mp4"
        r = _run_tracked(job_id, [
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(endcard),
            "-f", "lavfi", "-t", f"{ec_dur:.2f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{ec_dur:.2f}", "-vf",
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={info['fps'] or 30},setsar=1",
            "-map", "0:v", "-map", "1:a"] + common_v + [
            "-c:a", "aac", "-b:a", "192k", str(ec_clip)])
        if r.returncode == 0 and ec_clip.exists():
            lst = TMP / f"vc_cat_{job_id}.txt"
            lst.write_text(f"file '{out.as_posix()}'\nfile '{ec_clip.as_posix()}'\n",
                           encoding="utf-8")
            final = unique_out(f"{src.stem}_viral_end", ".mp4")
            r2 = _run_tracked(job_id, [
                FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", str(final)])
            lst.unlink(missing_ok=True)
            ec_clip.unlink(missing_ok=True)
            if r2.returncode == 0 and final.exists():
                out.unlink(missing_ok=True)
                outputs[0] = final
    _set(job_id, message=f"Xong — {len(clusters)} cụm phụ đề viral (burn + .ass + .srt)")
    return [out_entry(x) for x in outputs]


# ---------------------------------------------------------------- job: face blur
def job_face_blur(job_id: str, src: Path, mode: str, strength: float):
    """Tự phát hiện & làm mờ mọi khuôn mặt (YuNet ONNX) — che mặt học sinh,
    người qua đường... trước khi đăng video."""
    import cv2
    import numpy as np

    if not YUNET_ONNX.exists():
        raise RuntimeError("Chưa có model YuNet trong binaries/yunet")
    info = media_info(src)
    w, h = info["width"], info["height"]
    if not w or not h:
        raise RuntimeError("Tệp không có luồng video")
    fps = info["fps"] or 30
    dur = info["duration"] or 1
    total = max(1, int(dur * fps))

    # detect ở bản thu nhỏ ~640px cho nhanh, scale box ngược lại
    dw = min(640, w)
    dh = max(1, int(h * dw / w))
    det = cv2.FaceDetectorYN.create(str(YUNET_ONNX), "", (dw, dh), 0.6, 0.3, 500)
    sx, sy = w / dw, h / dh

    out = unique_out(f"{src.stem}_facemask", ".mp4")
    dec_log = TMP / f"fbdec_{job_id}.log"
    dec_ef = open(dec_log, "wb")
    dec = subprocess.Popen(
        [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(src), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        stdout=subprocess.PIPE, stderr=dec_ef,
    )
    enc_cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
               "-r", str(fps), "-i", "pipe:0"]
    if info["has_audio"]:
        enc_cmd += ["-i", str(src), "-map", "0:v", "-map", "1:a",
                    "-c:a", "aac", "-shortest"]
    # ép kích thước chẵn (x264 yuv420p đòi chẵn — video crop lẻ pixel sẽ chết encoder)
    enc_cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v", "libx264", "-crf", "19",
                "-preset", "veryfast" if w * h > 1920 * 1080 else "medium",
                "-pix_fmt", "yuv420p", str(out)]
    enc_log = TMP / f"fb_{job_id}.log"
    enc_ef = open(enc_log, "wb")
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=enc_ef)
    _track_proc(job_id, enc)

    frame_bytes = w * h * 3
    active = []  # [x1,y1,x2,y2,ttl] — giữ box 6 frame chống nhấp nháy
    i = faces_total = 0
    try:
        while True:
            if job_id in CANCEL_REQUESTED:
                dec.kill()
                enc.kill()
                raise JobCancelled()
            buf = dec.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy()
            small = cv2.resize(frame, (dw, dh))
            _ok, faces = det.detect(small)
            for b in active:
                b[4] -= 1
            active = [b for b in active if b[4] > 0]
            if faces is not None:
                for f in faces:
                    x, y, fw, fh = f[0] * sx, f[1] * sy, f[2] * sx, f[3] * sy
                    ex, ey = fw * 0.15, fh * 0.2  # nới box che cả tóc/cằm
                    x1 = int(max(0, x - ex))
                    y1 = int(max(0, y - ey))
                    x2 = int(min(w, x + fw + ex))
                    y2 = int(min(h, y + fh + ey))
                    if x2 > x1 and y2 > y1:
                        # bỏ box cũ trùng vị trí (tâm nằm trong box mới) — chống tích luỹ
                        active = [b for b in active
                                  if not (x1 <= (b[0] + b[2]) // 2 <= x2
                                          and y1 <= (b[1] + b[3]) // 2 <= y2)]
                        active.append([x1, y1, x2, y2, 6])
                        faces_total += 1
            for x1, y1, x2, y2, _t in active:
                roi = frame[y1:y2, x1:x2]
                if roi.size == 0:
                    continue
                if mode == "pixelate":
                    bw = max(3, int((x2 - x1) / (10 * strength)))
                    bh = max(3, int((y2 - y1) / (10 * strength)))
                    roi_s = cv2.resize(roi, (bw, bh), interpolation=cv2.INTER_LINEAR)
                    frame[y1:y2, x1:x2] = cv2.resize(
                        roi_s, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
                else:
                    sigma = max(6.0, (x2 - x1) / 8.0 * strength)
                    frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (0, 0), sigma)
            try:
                enc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                break  # encoder chết — đọc enc_log bên dưới để báo lỗi thật
            i += 1
            if i % 15 == 0:
                _set(job_id, progress=min(99, i / total * 100),
                     message=f"Che mặt: {i}/{total} khung hình · {faces_total} lượt phát hiện")
    finally:
        dec.stdout.close()
        dec.wait()
        dec_ef.close()
        if enc.stdin:
            try:
                enc.stdin.close()
            except BrokenPipeError:
                pass
        enc.wait()
        enc_ef.close()
    _cancel_point(job_id)
    if enc.returncode != 0 or not out.exists():
        err = enc_log.read_text(encoding="utf-8", errors="replace") if enc_log.exists() else ""
        raise RuntimeError(f"Encode failed: {err[-500:]}")
    if dec.returncode != 0 and i < total * 0.9:  # decode đứt giữa chừng ≠ EOF bình thường
        err = dec_log.read_text(encoding="utf-8", errors="replace") if dec_log.exists() else ""
        raise RuntimeError(f"Decode failed ở khung {i}/{total}: {err[-400:]}")
    enc_log.unlink(missing_ok=True)
    dec_log.unlink(missing_ok=True)
    _set(job_id, message=f"Xong — {faces_total} lượt phát hiện mặt trên {i} khung hình")
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
        "version": "1.0.0",
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
            "merge": ffmpeg_ok,
            "beatsync": ffmpeg_ok and librosa_available(),
            "audio_enhance": ffmpeg_ok,
            "brand": ffmpeg_ok,
            "audiogram": ffmpeg_ok,
            "llm": llm_available(),
            "claude": claude_available(),
            "director": claude_available(),
            "highlights": (llm_available() or claude_available()) and whisper_ok and ffmpeg_ok,
            "content": (llm_available() or claude_available()) and whisper_ok,
            "face_blur": cv2_available() and YUNET_ONNX.exists() and ffmpeg_ok,
            "lesson": (llm_available() or claude_available()) and whisper_ok,
            "ailms": ailms_available(),
            "broll": pexels_available() and (llm_available() or claude_available())
            and whisper_ok and ffmpeg_ok,
            "viral_caption": whisper_ok and ffmpeg_ok and FONTS_DIR.exists(),
        },
    }


ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
               ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
               ".png", ".jpg", ".jpeg", ".webp"}  # ảnh: logo watermark
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
WHISPER_MODELS = {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}
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
    if req.type == "merge":
        extra_names = list(p.get("files") or [])
        if len(extra_names) > 7:
            raise HTTPException(400, "Tối đa 8 clip mỗi lần ghép — bỏ bớt rồi chạy tiếp")
        extra = [safe_upload_path(str(n)) for n in extra_names]
        files = [src] + [f for f in extra if f.is_file()]
        if len(files) < 2:
            raise HTTPException(400, "Cần ít nhất 2 clip — bật 'Chọn nhiều' ở kho media")
        transition = p.get("transition", "fade")
        if transition not in XFADE_TRANSITIONS:
            transition = "fade"
        tdur = min(2.0, max(0.2, float(p.get("duration", 0.6))))
        target = p.get("target") if p.get("target") in FRAME_SIZES else "169"
        music = None
        if p.get("music"):
            music = safe_upload_path(str(p["music"]))
            if not music.is_file():
                raise HTTPException(404, "Không tìm thấy file nhạc")
        vol = min(1.0, max(0.05, float(p.get("volume", 0.6))))
        label = f"{src.name} +{len(files) - 1} clip"
        return submit_job("merge", label, job_merge, files, transition, tdur,
                          target, music, vol)
    if req.type == "beatsync":
        if not librosa_available():
            raise HTTPException(400, "Chưa cài librosa trên máy chủ")
        extra = [safe_upload_path(str(n)) for n in (p.get("files") or [])][:5]
        files = [src] + [f for f in extra if f.is_file()]
        music = safe_upload_path(str(p.get("music", "")))
        if not music.is_file():
            raise HTTPException(404, "Cần chọn file nhạc trong kho media")
        target = p.get("target") if p.get("target") in FRAME_SIZES else "916"
        max_seg = min(120, max(10, int(p.get("max_segments", 60))))
        label = f"{src.name} × nhạc {music.name}"
        return submit_job("beatsync", label, job_beatsync, files, music, target, max_seg)
    if req.type == "audio_enhance":
        denoise = bool(p.get("denoise", True))
        loudness = bool(p.get("loudness", True))
        if not denoise and not loudness:
            loudness = True
        return submit_job("audio_enhance", src.name, job_audio_enhance,
                          src, denoise, loudness)
    if req.type == "brand":
        title = str(p.get("title") or "")[:120]
        sign = str(p.get("sign") or "")[:60]
        logo = None
        if p.get("logo"):
            logo = safe_upload_path(str(p["logo"]))
            if not logo.is_file() or logo.suffix.lower() not in IMAGE_EXT:
                raise HTTPException(400, "Logo phải là ảnh png/jpg/webp trong kho media")
        if not title and not sign and logo is None:
            raise HTTPException(400, "Cần ít nhất tiêu đề, chữ ký hoặc logo")
        corner = p.get("corner") if p.get("corner") in LOGO_CORNERS else "br"
        opacity = min(1.0, max(0.1, float(p.get("opacity", 0.7))))
        title_dur = min(10.0, max(1.0, float(p.get("title_dur", 3.0))))
        return submit_job("brand", src.name, job_brand, src, title, sign, logo,
                          corner, opacity, title_dur)
    if req.type == "audiogram":
        target = p.get("target") if p.get("target") in FRAME_SIZES else "11"
        title = str(p.get("title") or "")[:80]
        return submit_job("audiogram", src.name, job_audiogram, src, target, title)
    if req.type == "highlights":
        if not llm_available() and not claude_available():
            raise HTTPException(400, "Cần mlx-lm (LLM local) hoặc Claude CLI trên máy chủ")
        return submit_job("highlights", src.name, job_highlights, src, dict(p))
    if req.type == "content":
        if not llm_available() and not claude_available():
            raise HTTPException(400, "Cần mlx-lm (LLM local) hoặc Claude CLI trên máy chủ")
        return submit_job("content", src.name, job_content, src, dict(p))
    if req.type == "face_blur":
        mode = p.get("mode", "blur")
        if mode not in ("blur", "pixelate"):
            mode = "blur"
        strength = min(2.0, max(0.5, float(p.get("strength", 1.0))))
        return submit_job("face_blur", src.name, job_face_blur, src, mode, strength)
    if req.type == "lesson":
        if not llm_available() and not claude_available():
            raise HTTPException(400, "Cần mlx-lm (LLM local) hoặc Claude CLI trên máy chủ")
        return submit_job("lesson", src.name, job_lesson, src, dict(p))
    if req.type == "broll":
        if not pexels_available():
            raise HTTPException(400, "Chưa có PEXELS_API_KEY — thêm vào backend/.env rồi khởi động lại")
        if not llm_available() and not claude_available():
            raise HTTPException(400, "Cần mlx-lm hoặc Claude CLI để chọn cảnh B-roll")
        return submit_job("broll", src.name, job_broll, src, dict(p))
    if req.type == "viral_caption":
        if not (whisper_available_any()):
            raise HTTPException(400, "Cần whisper (faster/MLX) để nhận dạng lời nói")
        return submit_job("viral_caption", src.name, job_viral_caption, src, dict(p))
    raise HTTPException(400, f"Loại job không hỗ trợ: {req.type}")


# ---------------------------------------------------------------- Đạo diễn AI (Claude sub)
DIRECTOR_ALLOWED_TYPES = {
    "transcribe", "silence_cut", "upscale", "export", "rife", "bg_remove",
    "auto_edit", "reframe", "speed", "color", "music", "stabilize", "merge",
    "beatsync", "audio_enhance", "brand", "audiogram", "highlights", "content",
    "face_blur", "tts", "lesson", "broll",
}

DIRECTOR_CATALOG = """\
- transcribe: caption tự động. params: model(tiny/base/small), effect(classic/bold/yellow/karaoke/neon/box), max_words(0-7), burn(bool), language(vi/en/null)
- silence_cut: cắt khoảng lặng. params: margin(0-1, giây)
- upscale: phóng to AI. params: mode(ai/fast), scale(2/3/4)
- export: xuất preset. params: preset(tiktok/youtube/square/reels45/gif/mp3)
- rife: nội suy khung hình. params: mode(smooth=mượt gấp đôi fps / slowmo=quay chậm 2x)
- bg_remove: tách nền. params: bg(green/black/white/alpha)
- auto_edit: dựng tự động trọn gói. params: cut(bool), caption(bool), preset(reframe916/tiktok/youtube/square/reels45/null), effect, max_words
- reframe: đổi khung dọc 9:16. params: mode(blur=nền mờ CapCut / crop=cắt giữa)
- speed: đổi tốc độ. params: factor(0.5/0.75/1.25/1.5/2/3)
- color: filter màu. params: filter(vivid/warm/cool/bw/film/sharp)
- music: trộn nhạc nền. params: music(tên file audio trong kho), volume(0-1), duck(bool)
- stabilize: chống rung. params: (không)
- merge: ghép nhiều clip. file=clip đầu, params: files([tên clip khác]), transition(fade/dissolve/wipeleft/slideleft/circleopen...), duration(giây), target(169/916/11), music(tên file, tùy chọn)
- beatsync: cắt theo nhịp nhạc. file=clip đầu, params: files([clip khác, tùy chọn]), music(tên file audio, BẮT BUỘC), target(916/169/11), max_segments(10-120)
- audio_enhance: khử ồn + chuẩn âm lượng. params: denoise(bool), loudness(bool)
- brand: tiêu đề + chữ ký + logo. params: title(chuỗi), sign(chuỗi), logo(tên file ảnh, tùy chọn), corner(tl/tr/bl/br), opacity(0-1)
- audiogram: audio → video sóng nhạc. params: target(11/916/169), title(chuỗi)
- highlights: AI cắt shorts từ video dài. params: count(1-6), min_dur, max_dur, make_shorts(bool), ai(local/claude)
- content: AI viết tiêu đề/mô tả/hashtags. params: platform(tiktok/youtube), ai(local/claude)
- face_blur: che mặt tự động. params: mode(blur/pixelate), strength(0.5-2)
- lesson: bài giảng → giáo án + câu hỏi quiz (.md). params: quiz(số câu 3-15), push_lms(bool đẩy sang AI-LMS), level(1-4), department, ai(local/claude)
- broll: tự chèn B-roll (cảnh minh hoạ) tải từ Pexels đè lên video người nói. params: count(1-6 số đoạn), ai(local/claude)
- tts: đọc văn bản (KHÔNG cần file). params: text(chuỗi), voice(vi/en), speed(0.6-1.5)"""


class DirectorReq(BaseModel):
    message: str
    reset: bool = False


# Bộ nhớ hội thoại của Đạo diễn (app cá nhân 1 người — 1 luồng chung, có khoá)
DIRECTOR_HISTORY: list = []       # [{role: user|ai, text}]
DIRECTOR_HISTORY_LOCK = threading.Lock()
DIRECTOR_MAX_TURNS = 12           # giữ tối đa 12 lượt gần nhất gửi lại cho Claude


@app.post("/api/director/reset")
def director_reset():
    with DIRECTOR_HISTORY_LOCK:
        DIRECTOR_HISTORY.clear()
    return {"ok": True}


@app.post("/api/director")
def director(req: DirectorReq):
    """Claude (gói sub) đọc yêu cầu → lập kế hoạch job. Backend validate lại
    TOÀN BỘ (whitelist type + create_job) rồi mới xếp hàng — Claude không có
    quyền chạy gì trực tiếp. Nhớ hội thoại nhiều lượt để hiểu 'cái vừa nãy'."""
    if not claude_available():
        raise HTTPException(400, "Chưa cài / đăng nhập Claude Code CLI trên máy chủ")
    if req.reset:
        with DIRECTOR_HISTORY_LOCK:
            DIRECTOR_HISTORY.clear()
    msg = (req.message or "").strip()
    if not msg:
        raise HTTPException(400, "Thiếu nội dung yêu cầu")
    if len(msg) > 2000:
        raise HTTPException(400, "Yêu cầu quá dài (tối đa 2000 ký tự)")

    items = []
    for pth in sorted(UPLOADS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:40]:
        if pth.is_file():
            inf = media_info(pth)
            kind = ("ảnh" if inf["width"] and inf["duration"] < 0.1
                    else "video" if inf["width"] else "audio")
            items.append(f"- {pth.name} ({kind}, {inf['duration']:.0f}s)")
    media_desc = "\n".join(items) or "(kho media trống)"

    with DIRECTOR_HISTORY_LOCK:
        hist = list(DIRECTOR_HISTORY[-DIRECTOR_MAX_TURNS * 2:])
    hist_desc = ""
    if hist:
        lines = [("Người dùng: " if h["role"] == "user" else "Bạn: ") + h["text"]
                 for h in hist]
        hist_desc = "HỘI THOẠI TRƯỚC ĐÓ (để hiểu 'cái vừa nãy', 'video đó'...):\n" \
            + "\n".join(lines) + "\n\n"

    prompt = (
        "Bạn là đạo diễn hậu kỳ điều khiển app dựng video Local Studio.\n\n"
        f"KHO MEDIA (tên file phải dùng CHÍNH XÁC):\n{media_desc}\n\n"
        f"CÔNG CỤ (job type + params):\n{DIRECTOR_CATALOG}\n\n"
        f"{hist_desc}"
        f"YÊU CẦU MỚI CỦA NGƯỜI DÙNG: {msg}\n\n"
        "Trả về DUY NHẤT một object JSON, không giải thích ngoài JSON:\n"
        '{"reply": "<trả lời ngắn gọn tiếng Việt: sẽ làm gì / hoặc câu trả lời nếu chỉ hỏi>", '
        '"actions": [{"type": "<job type>", "file": "<tên file trong kho, hoặc \\"\\" với tts>", '
        '"params": {...}}]}\n'
        "Quy tắc: tối đa 8 actions; chỉ dùng type trong danh sách; chỉ dùng file có trong kho; "
        "nếu yêu cầu mơ hồ hoặc thiếu file thì actions để rỗng và hỏi lại trong reply."
    )
    try:
        data = _extract_json(claude_generate(prompt, timeout=180))
    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"Đạo diễn không phản hồi hợp lệ: {str(e)[:200]}")
    if not isinstance(data, dict):
        raise HTTPException(502, "Đạo diễn trả sai định dạng")

    reply = str(data.get("reply") or "")[:1200]
    actions = data.get("actions") if isinstance(data.get("actions"), list) else []
    created, errors = [], []
    for a in actions[:8]:
        if not isinstance(a, dict):
            continue
        jtype = a.get("type")
        if jtype not in DIRECTOR_ALLOWED_TYPES:
            errors.append(f"bỏ qua công cụ lạ: {jtype}")
            continue
        try:
            params = a.get("params") if isinstance(a.get("params"), dict) else {}
            job = create_job(JobRequest(type=jtype, file=str(a.get("file") or ""),
                                        params=params))
            created.append(job)
        except HTTPException as e:
            errors.append(f"{jtype}: {e.detail}")
        except (ValueError, TypeError) as e:
            errors.append(f"{jtype}: tham số không hợp lệ ({str(e)[:80]})")

    # ghi vào bộ nhớ hội thoại (tóm tắt hành động để lượt sau hiểu ngữ cảnh)
    ai_note = reply
    if created:
        ai_note += " [đã xếp: " + ", ".join(
            f"{j['type']}({j['input']})" for j in created) + "]"
    with DIRECTOR_HISTORY_LOCK:
        DIRECTOR_HISTORY.append({"role": "user", "text": msg[:500]})
        DIRECTOR_HISTORY.append({"role": "ai", "text": ai_note[:500]})
        del DIRECTOR_HISTORY[:-DIRECTOR_MAX_TURNS * 2]
    return {"reply": reply, "jobs": created, "errors": errors}


# ---------------------------------------------------------------- Phụ đề Viral: analyze + preset
class ViralAnalyzeReq(BaseModel):
    file: str
    model: str = "large-v3"
    language: str = "vi"
    engine: str = ""


@app.post("/api/viral/analyze")
def viral_analyze(req: ViralAnalyzeReq):
    """Bước 1-3: nhận dạng + tách cụm → trả về danh sách cụm (text + mốc thời
    gian cấp từ) để người dùng chỉnh tay trên timeline trước khi render."""
    if not whisper_available_any():
        raise HTTPException(400, "Cần whisper để nhận dạng lời nói")
    src = safe_upload_path(req.file)
    if not src.is_file():
        raise HTTPException(404, f"Không tìm thấy file: {req.file}")
    info = media_info(src)
    p = {"model": req.model, "language": req.language, "engine": req.engine or None}
    job = submit_job("viral_analyze", src.name, _job_viral_analyze, src, p)
    return {"job": job}


def _job_viral_analyze(job_id: str, src: Path, p: dict):
    seg_list = _run_viral_recognize(job_id, src, p, 5, 90)
    _cancel_point(job_id)
    clusters = _viral_clusters(seg_list)
    info = media_info(src)
    data = []
    for c in clusters:
        l1, l2 = _balance_lines(c)
        text = " ".join(x["t"] for x in l1)
        if l2:
            text += "\n" + " ".join(x["t"] for x in l2)
        data.append({"start": round(c[0]["s"], 2), "end": round(c[-1]["e"], 2),
                     "text": text,
                     "words": [{"t": x["t"], "s": round(x["s"], 2),
                                "e": round(x["e"], 2)} for x in c]})
    note = unique_out(f"{src.stem}_viral_cues", ".json")
    note.write_text(json.dumps({"w": info["width"], "h": info["height"],
                                "clusters": data}, ensure_ascii=False), encoding="utf-8")
    _set(job_id, message=f"Phân tích xong — {len(data)} cụm (chỉnh tay rồi render)")
    return [out_entry(note)]


VIRAL_PRESETS_DIR = WORKSPACE / "presets"
VIRAL_PRESETS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/viral/presets")
def viral_presets_list():
    items = []
    for f in sorted(VIRAL_PRESETS_DIR.glob("*.json")):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return items


class ViralPresetReq(BaseModel):
    name: str
    params: dict = {}


@app.post("/api/viral/presets")
def viral_preset_save(req: ViralPresetReq):
    name = re.sub(r"[^\w\- ]+", "", req.name).strip()[:60]
    if not name:
        raise HTTPException(400, "Tên preset không hợp lệ")
    data = {"name": name, "params": req.params}
    (VIRAL_PRESETS_DIR / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


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
