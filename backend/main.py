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


def llm_generate(prompt: str, max_tokens: int = 1500, job_id: str = None) -> str:
    """Sinh văn bản bằng Qwen3 4B 4-bit qua MLX — nạp model 1 lần, khoá tuần tự.
    Trong lúc chờ khoá vẫn phản hồi lệnh hủy job."""
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


def _pick_highlights(lines: list, dur: float, count: int,
                     smin: float, smax: float, job_id: str = None) -> list:
    """LLM chọn các khoảnh khắc đáng làm shorts. Transcript dài thì chia phần."""
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
            data = _extract_json(llm_generate(prompt, max_tokens=1200, job_id=job_id))
            return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
        except (ValueError, json.JSONDecodeError):
            return []

    # transcript quá dài → chọn sơ bộ theo từng phần rồi chung kết
    CHUNK = 11000
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
                idx = _extract_json(llm_generate(prompt, max_tokens=200, job_id=job_id))
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

    _set(job_id, progress=48, message="AI (Qwen3 local) đang chọn khoảnh khắc hay...")
    picked = _pick_highlights(lines, dur, count, smin, smax, job_id)
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

    _set(job_id, progress=65, message="AI (Qwen3 local) đang viết nội dung...")
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
    content = llm_generate(prompt, max_tokens=1600, job_id=job_id)
    _cancel_point(job_id)
    out = unique_out(f"{src.stem}_content", ".md")
    out.write_text(f"# Nội dung cho: {src.name} ({platform})\n\n{content}\n",
                   encoding="utf-8")
    _set(job_id, message=f"Xong — tiêu đề/mô tả/hashtags{' /chapters' if chapters_req else ''}")
    return [out_entry(out)]


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
        "version": "0.6.0",
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
            "highlights": llm_available() and whisper_ok and ffmpeg_ok,
            "content": llm_available() and whisper_ok,
            "face_blur": cv2_available() and YUNET_ONNX.exists() and ffmpeg_ok,
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
        if not llm_available():
            raise HTTPException(400, "Chưa cài mlx-lm (LLM local) trên máy chủ")
        return submit_job("highlights", src.name, job_highlights, src, dict(p))
    if req.type == "content":
        if not llm_available():
            raise HTTPException(400, "Chưa cài mlx-lm (LLM local) trên máy chủ")
        return submit_job("content", src.name, job_content, src, dict(p))
    if req.type == "face_blur":
        mode = p.get("mode", "blur")
        if mode not in ("blur", "pixelate"):
            mode = "blur"
        strength = min(2.0, max(0.5, float(p.get("strength", 1.0))))
        return submit_job("face_blur", src.name, job_face_blur, src, mode, strength)
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
