import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import {
  askDirector, authStatus, cancelJob, clipSearch, createJob, deleteMedia,
  deleteProject, deleteTemplate, fetchCuesJson, fmtDur, fmtSize,
  getClaudeSettings, getHealth, getIngestInfo, getJobs, getMedia,
  listProjects, listTemplates, loadProject, login, logout, openOutputs,
  resetDirector, saveClaudeSettings, saveProject, saveTemplate, scanFolder,
  setWatch, getWatch, stopDirector, testClaude, uploadFile, viralAnalyze,
  type Health, type Job, type MediaItem, type ProjectInfo, type TemplateInfo,
  type ViralCluster, type WatchCfg,
} from "./api";

// Nhãn giọng Piper (key khớp backend PIPER_VOICE_MAP)
const VOICE_LABELS: Record<string, string> = {
  vi: "🇻🇳 Nữ — vais1000", vi2: "🇻🇳 Nam — 25hours",
  en: "🇺🇸 Nữ — lessac", en2: "🇺🇸 Nữ — amy", en3: "🇺🇸 Nam — ryan (HQ)",
};

// Phân nhóm công cụ (tab lọc kiểu TTM) + màu chip đánh số
type ToolCat = "ai" | "edit" | "audio" | "pub";
const TOOL_CATS: Record<string, ToolCat> = {
  pipeline: "ai", viral: "ai", director: "ai", highlights: "ai", broll: "ai",
  translate: "ai", social_pack: "ai", dub: "ai", qc: "ai", script: "ai",
  thumbnail: "ai", auto_edit: "ai", transcribe: "ai", content: "ai", lesson: "ai",
  clipsearch: "ai", folder: "ai", autoframe: "edit", filler_cut: "ai",
  enhance: "edit", retouch: "edit", voicefx: "audio",
  autopilot: "ai", script_video: "ai", post_pack: "ai",
  scene_split: "edit", punchin: "edit", multi_translate: "ai",
  merge: "edit", beatsync: "edit", silence_cut: "edit", upscale: "edit",
  rife: "edit", bg_remove: "edit", reframe: "edit", speed: "edit", color: "edit",
  grade: "edit", track: "edit", stabilize: "edit", face_blur: "edit", brand: "edit",
  music: "audio", audio_enhance: "audio", audiogram: "audio", tts: "audio",
  export: "pub",
};
const CAT_TABS: [ToolCat | "all", string][] = [
  ["all", "Tất cả"], ["ai", "🤖 AI"], ["edit", "✂️ Dựng"],
  ["audio", "🎵 Âm thanh"], ["pub", "📤 Xuất"],
];

// tool key → key trong health.features quyết định bật/tắt
const FEAT_KEY: Record<string, string> = {
  transcribe: "transcribe", silence_cut: "silence_cut", upscale: "upscale_fast",
  rife: "rife", bg_remove: "bg_remove", tts: "tts", auto_edit: "auto_edit",
  beatsync: "beatsync", highlights: "highlights", content: "content",
  face_blur: "face_blur", director: "director", lesson: "lesson",
  translate: "translate", social_pack: "social_pack", dub: "dub", qc: "qc",
  script: "script", thumbnail: "thumbnail", viral: "viral_caption",
  pipeline: "export", export: "export", grade: "grade", track: "track",
  clipsearch: "clipsearch", folder: "folder", autoframe: "autoframe",
  voicefx: "voicefx", enhance: "enhance", filler_cut: "filler_cut", retouch: "retouch",
  autopilot: "autopilot", script_video: "script_video", post_pack: "post_pack",
  scene_split: "scene_split", punchin: "punchin", multi_translate: "multi_translate",
  reframe: "ffmpeg", speed: "ffmpeg", color: "ffmpeg", music: "ffmpeg",
  stabilize: "ffmpeg", merge: "ffmpeg", audio_enhance: "ffmpeg",
  brand: "ffmpeg", audiogram: "ffmpeg",
};

// Lý do tính năng bị khoá (hiện tooltip thay vì ẩn bí hiểm — góp ý nhân viên)
const TOOL_REQS: Record<string, string> = {
  transcribe: "cần faster-whisper/MLX (chạy setup + requirements)",
  silence_cut: "cần auto-editor (pip install trong requirements.txt)",
  upscale: "cần Real-ESRGAN trong binaries/ (chạy ./setup-binaries.sh)",
  rife: "cần RIFE trong binaries/ (chạy ./setup-binaries.sh)",
  bg_remove: "cần model RVM trong binaries/rvm (chạy ./setup-binaries.sh)",
  tts: "cần piper-tts + giọng trong binaries/piper/voices (setup-binaries.sh)",
  face_blur: "cần opencv + model YuNet (chạy ./setup-binaries.sh)",
  beatsync: "cần librosa (pip install trong requirements.txt)",
  director: "cần Claude Code CLI đăng nhập gói sub + bật trong ⚙ Cài đặt",
  thumbnail: "cần Claude Code CLI + bật trong ⚙ Cài đặt",
  broll: "cần PEXELS_API_KEY trong backend/.env + Claude/Qwen",
  highlights: "cần Claude CLI hoặc mlx-lm + whisper",
  content: "cần Claude CLI hoặc mlx-lm + whisper",
  lesson: "cần Claude CLI hoặc mlx-lm + whisper",
  translate: "cần Claude CLI hoặc mlx-lm + whisper",
  social_pack: "cần Claude CLI hoặc mlx-lm + whisper",
  qc: "cần Claude CLI hoặc mlx-lm + whisper",
  script: "cần Claude CLI hoặc mlx-lm + whisper",
  dub: "cần whisper + Piper TTS + Claude/Qwen",
  viral: "cần whisper + font trong binaries/fonts (setup-binaries.sh)",
  track: "cần pip install ultralytics + model sam2.1_t.pt trong binaries/sam2",
  clipsearch: "cần pip install open_clip_torch (model tự tải lần đầu)",
  autoframe: "cần opencv + model YuNet (chạy ./setup-binaries.sh)",
  retouch: "cần opencv + model YuNet (chạy ./setup-binaries.sh)",
  filler_cut: "cần faster-whisper/MLX",
  script_video: "cần PEXELS_API_KEY + Piper TTS + Claude/Qwen",
  post_pack: "cần whisper (faster/MLX)",
  punchin: "cần whisper (faster/MLX)",
  multi_translate: "cần whisper + Claude/Qwen",
};

// Ngôn ngữ đích cho Dịch phụ đề AI
const TR_LANGS: [string, string][] = [
  ["en", "English"], ["vi", "Tiếng Việt"], ["zh", "中文 Chinese"], ["ja", "日本語 Japanese"],
  ["ko", "한국어 Korean"], ["fr", "Français"], ["es", "Español"], ["de", "Deutsch"],
  ["th", "ไทย Thai"], ["id", "Indonesia"], ["pt", "Português"], ["ru", "Русский"],
];

function LoginScreen({ onOk }: { onOk: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try { await login(pw); onOk(); }
    catch (ex) { setErr(String((ex as Error).message)); }
    finally { setBusy(false); }
  };
  return (
    <div className="loginwrap">
      <form className="logincard" onSubmit={submit}>
        <div className="logo" style={{ justifyContent: "center", border: "none", paddingRight: 0 }}>
          <span className="mk">L</span><b>LOCAL STUDIO</b>
        </div>
        <p className="logindesc">Khu vực quản trị — nhập mật khẩu admin.<br />
          <small>Mật khẩu nằm trong <code>backend\ADMIN-PASSWORD.txt</code> trên máy chủ.</small></p>
        <input type="password" autoFocus placeholder="Mật khẩu admin" value={pw}
          onChange={(e) => setPw(e.target.value)} className="logininput" />
        {err && <div className="loginerr">⚠ {err}</div>}
        <button className="btn pri big" disabled={busy || !pw}>
          {busy ? "Đang kiểm tra..." : "🔓 Đăng nhập"}
        </button>
        <p className="loginfoot">Phiên đăng nhập 7 ngày · footage vẫn nằm nguyên trên máy chủ</p>
      </form>
    </div>
  );
}

type ToolKey = "auto_edit" | "transcribe" | "silence_cut" | "upscale" | "rife" | "bg_remove"
  | "tts" | "reframe" | "speed" | "color" | "music" | "stabilize" | "export"
  | "merge" | "beatsync" | "audio_enhance" | "brand" | "audiogram"
  | "highlights" | "face_blur" | "content" | "director" | "lesson" | "broll" | "viral"
  | "translate" | "social_pack" | "dub" | "qc" | "script" | "thumbnail"
  | "grade" | "track" | "clipsearch" | "folder" | "pipeline"
  | "autoframe" | "voicefx" | "enhance" | "filler_cut" | "retouch"
  | "autopilot" | "script_video" | "post_pack" | "scene_split" | "punchin" | "multi_translate";

// Lớp phủ multi-track (đè lên video nền theo mốc thời gian)
type Layer = {
  id: number; kind: "image" | "text" | "audio" | "video";
  file?: string; text?: string; color?: string; anim?: string;
  start: number; end: number; x: number; y: number;
  x2?: number; y2?: number;   // keyframe vị trí cuối (bay từ x,y → x2,y2)
  scale: number; opacity: number; size: number; volume: number;
};
const ANIMS: [string, string][] = [
  ["none", "Không"], ["fade", "Mờ dần"], ["slide_l", "Trượt ← trái"],
  ["slide_r", "Trượt phải →"], ["slide_t", "Trượt ↓ trên"], ["slide_b", "Trượt ↑ dưới"],
];

// Các bước có thể xếp vào Chuỗi tự động (nối tiếp output→input)
type PipeStep = { type: string; params: Record<string, unknown> };
const PIPE_PALETTE: { type: string; label: string; def: Record<string, unknown> }[] = [
  { type: "silence_cut", label: "✂️ Cắt lặng", def: { margin: 0.2 } },
  { type: "speed", label: "⏩ Tốc độ", def: { factor: 1.5 } },
  { type: "color", label: "🎨 Filter màu", def: { filter: "vivid" } },
  { type: "grade", label: "🎛️ Chỉnh màu PRO", def: { contrast: 1.1, vibrance: 0.4, temperature: -15 } },
  { type: "enhance", label: "✨ Đẹp màu 1 chạm", def: { mode: "natural" } },
  { type: "autoframe", label: "📱 Auto reframe bám mặt", def: { ratio: "916" } },
  { type: "filler_cut", label: "🧹 Cắt từ đệm", def: { model: "base" } },
  { type: "retouch", label: "💆 Mịn da", def: { strength: 1 } },
  { type: "voicefx", label: "🎭 Đổi giọng", def: { effect: "deep" } },
  { type: "stabilize", label: "🧷 Chống rung", def: {} },
  { type: "reframe", label: "📐 Khung 9:16", def: { mode: "blur" } },
  { type: "rife", label: "🎞️ Nội suy mượt", def: { mode: "smooth" } },
  { type: "upscale", label: "🔍 Upscale", def: { mode: "fast", scale: 2 } },
  { type: "bg_remove", label: "🪄 Tách nền", def: { bg: "green" } },
  { type: "audio_enhance", label: "🎚️ Chuẩn âm", def: { denoise: true, loudness: true } },
  { type: "face_blur", label: "🫥 Che mặt", def: { mode: "blur", strength: 1 } },
  { type: "transcribe", label: "💬 Caption", def: { model: "base", effect: "karaoke", burn: true } },
  { type: "viral_caption", label: "🔥 Phụ đề Viral", def: { model: "large-v3-turbo" } },
  { type: "export", label: "📤 Xuất preset", def: { preset: "tiktok" } },
];

const TOOLS: { key: ToolKey; icon: string; name: string; desc: string; gpu: boolean }[] = [
  { key: "autopilot", icon: "🚀", name: "AutoPilot — Làm hết cho tôi", desc: "1 nút: AI tự dò → cắt lặng+ừm → đẹp màu → chuẩn âm → 9:16 → phụ đề", gpu: true },
  { key: "script_video", icon: "🎥", name: "Kịch bản → Video (Script-to-Video)", desc: "gõ chủ đề → AI chia cảnh + đọc lời + B-roll → video hoàn chỉnh", gpu: true },
  { key: "post_pack", icon: "📦", name: "Gói đăng bài 1 chạm", desc: "video + thumbnail AI + caption/hashtags + srt → 1 file .zip", gpu: true },
  { key: "pipeline", icon: "🔗", name: "Chuỗi tự động (nối nhiều bước)", desc: "xếp nhiều tính năng chạy nối tiếp trên 1 video", gpu: false },
  { key: "folder", icon: "📁", name: "Edit hàng loạt cả THƯ MỤC", desc: "trỏ vào folder trong ổ cứng → chạy chuỗi bước cho mọi video", gpu: false },
  { key: "viral", icon: "🔥", name: "Phụ đề Viral 1 chạm", desc: "nhận dạng → tách cụm → cháy preset viral + chuẩn âm", gpu: true },
  { key: "director", icon: "🎬", name: "Đạo diễn AI (Claude)", desc: "ra lệnh bằng lời — Claude tự xếp job", gpu: false },
  { key: "highlights", icon: "🎯", name: "AI cắt Shorts từ video dài", desc: "AI chọn khoảnh khắc → shorts 9:16", gpu: true },
  { key: "broll", icon: "🎞️", name: "Ghép B-roll tự động (Pexels)", desc: "AI tải cảnh minh hoạ chèn theo lời nói", gpu: false },
  { key: "translate", icon: "🌐", name: "Dịch phụ đề AI", desc: "Whisper → Claude dịch → .srt + cháy vào video", gpu: false },
  { key: "social_pack", icon: "📢", name: "Tái chế nội dung (Claude)", desc: "1 video → tiêu đề, caption, tweet, LinkedIn...", gpu: false },
  { key: "dub", icon: "🎙️", name: "Lồng tiếng AI", desc: "dịch → Piper đọc tiếng đích → thay track (vi/en)", gpu: false },
  { key: "qc", icon: "🔍", name: "QC video AI (Claude)", desc: "soi filler/khoảng chết → báo cáo + tự cắt", gpu: false },
  { key: "script", icon: "✍️", name: "Bác sĩ kịch bản (Claude)", desc: "viết lại lời + hook + teleprompter", gpu: false },
  { key: "thumbnail", icon: "🖼️", name: "Claude chọn Thumbnail", desc: "Claude nhìn khung hình → chấm ảnh bìa giật view", gpu: false },
  { key: "auto_edit", icon: "✨", name: "Tự động dựng (AI 1 chạm)", desc: "cắt lặng → caption → preset · batch", gpu: false },
  { key: "merge", icon: "🎬", name: "Ghép clip + chuyển cảnh", desc: "xfade 10 hiệu ứng · chọn nhiều clip", gpu: false },
  { key: "beatsync", icon: "🥁", name: "Cắt theo nhịp nhạc", desc: "beat-sync kiểu CapCut · librosa", gpu: false },
  { key: "transcribe", icon: "💬", name: "Caption tự động", desc: "whisper word-level · .srt/.ass", gpu: false },
  { key: "silence_cut", icon: "✂️", name: "Cắt khoảng lặng", desc: "auto-editor · jump-cut", gpu: false },
  { key: "upscale", icon: "🔍", name: "AI Upscale ×2/×4", desc: "Real-ESRGAN ncnn-vulkan", gpu: true },
  { key: "rife", icon: "🎞️", name: "Nội suy khung hình", desc: "RIFE · mượt ×2 fps · slow-motion", gpu: true },
  { key: "bg_remove", icon: "🪄", name: "Tách nền video", desc: "RVM matting · nền màu / trong suốt", gpu: false },
  { key: "tts", icon: "🗣️", name: "Đọc văn bản (TTS)", desc: "Piper · giọng Việt/Anh · offline", gpu: false },
  { key: "reframe", icon: "📐", name: "Đổi khung 9:16", desc: "nền mờ kiểu CapCut · crop giữa", gpu: false },
  { key: "speed", icon: "⏩", name: "Tốc độ video", desc: "0.5× – 3× · giữ cao độ âm thanh", gpu: false },
  { key: "color", icon: "🎨", name: "Filter màu", desc: "vivid · warm · film · B&W · sharp", gpu: false },
  { key: "grade", icon: "🎛️", name: "Bảng chỉnh màu PRO", desc: "phơi sáng · tương phản · nhiệt độ · vibrance · nét", gpu: false },
  { key: "track", icon: "🎯", name: "Track đối tượng (SAM 2)", desc: "bấm vào vật/người → AI bám theo → mờ/spotlight/tách nền", gpu: true },
  { key: "clipsearch", icon: "🔎", name: "Tìm cảnh bằng AI (CLIP)", desc: "gõ mô tả → tìm đúng khoảnh khắc trong cả kho video", gpu: true },
  { key: "autoframe", icon: "📱", name: "Auto Reframe bám chủ thể", desc: "AI dò mặt → khung dọc LIA THEO người nói (CapCut Pro)", gpu: false },
  { key: "filler_cut", icon: "🧹", name: "Tự cắt từ đệm (ừm, à...)", desc: "AI nghe → cắt sạch ừm/à/uh khỏi video", gpu: false },
  { key: "enhance", icon: "✨", name: "Đẹp màu 1 chạm", desc: "tự cân bằng trắng + màu sống động + nét", gpu: false },
  { key: "retouch", icon: "💆", name: "Làm mịn da (retouch)", desc: "AI dò mặt → mịn da vùng mặt, nền giữ nét", gpu: false },
  { key: "voicefx", icon: "🎭", name: "Đổi giọng (voice FX)", desc: "sóc chuột · trầm · robot · điện thoại · vang · hang", gpu: false },
  { key: "scene_split", icon: "🎬", name: "Tách cảnh tự động", desc: "dò chuyển cảnh → cắt thành từng clip riêng", gpu: false },
  { key: "punchin", icon: "🔍", name: "Auto punch-in theo câu", desc: "zoom xen kẽ theo từng câu nói — nhịp faceless channel", gpu: false },
  { key: "multi_translate", icon: "🌏", name: "Dịch đa ngữ 1 chạm", desc: "1 video → nhiều bản hardsub (EN/中/日/한...) trong 1 job", gpu: true },
  { key: "music", icon: "🎵", name: "Nhạc nền + ducking", desc: "tự nén nhạc khi có giọng nói", gpu: false },
  { key: "face_blur", icon: "🫥", name: "Làm mờ mặt tự động", desc: "YuNet AI · che mặt học sinh/người lạ", gpu: false },
  { key: "content", icon: "📝", name: "AI viết nội dung", desc: "tiêu đề · mô tả · hashtags · chapters", gpu: true },
  { key: "lesson", icon: "📚", name: "Bài giảng → Giáo án + Quiz", desc: "AI soạn giáo án · đẩy sang AI-LMS", gpu: true },
  { key: "audio_enhance", icon: "🎚️", name: "Chuẩn hoá âm thanh", desc: "khử ồn + loudnorm -16 LUFS", gpu: false },
  { key: "brand", icon: "🏷️", name: "Tiêu đề & Logo", desc: "title mở đầu · chữ ký · watermark PNG", gpu: false },
  { key: "audiogram", icon: "📻", name: "Audiogram sóng nhạc", desc: "audio/TTS → video đăng MXH", gpu: false },
  { key: "stabilize", icon: "🧷", name: "Chống rung", desc: "deshake · video quay tay", gpu: false },
  { key: "export", icon: "📤", name: "Xuất preset mạng xã hội", desc: "TikTok · YouTube · 4:5 · GIF · MP3", gpu: false },
];
const TOOL_NAMES: Record<string, string> = {
  ...Object.fromEntries(TOOLS.map((t) => [t.key, t.name])),
  // job type không có nút tool riêng (gọi từ timeline/panel khác) — tránh toast "undefined"
  cutlist: "Cắt timeline", composite: "Lớp phủ multi-track",
  clip_index: "Lập chỉ mục tìm cảnh", viral_caption: "Phụ đề Viral",
};

const FONTS = ["Arial", "Arial Black", "Impact", "Segoe UI", "Verdana", "Tahoma",
  "Georgia", "Times New Roman", "Comic Sans MS", "Consolas", "Bahnschrift"];

// overlay preview: tô vàng từ khoá (khớp preset — approximate so với bản cháy libass)
function renderVcLine(line: string, kwOn: boolean, kwCsv: string) {
  if (!kwOn) return line;
  const set = new Set(kwCsv.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean));
  return line.split(" ").map((w, i) => {
    const clean = w.replace(/[.,!?…;:"'()]/g, "").toLowerCase();
    const hot = (set.size ? set.has(clean) : false) || /\d/.test(clean);
    return <span key={i} style={hot ? { color: "#FFD400" } : undefined}>{w}{" "}</span>;
  });
}

type Preview = { input: string; url: string; name: string };

// Bánh xe nhuộm màu (kiểu TTM "Nhuộm Tối/Nhuộm Sáng"): kéo/chạm chọn hue + độ đậm
function ColorWheel({ label, hue, sat, onChange }: {
  label: string; hue: number; sat: number; onChange: (h: number, s: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const pick = (clientX: number, clientY: number) => {
    const el = ref.current; if (!el) return;
    const r = el.getBoundingClientRect();
    const dx = (clientX - (r.left + r.width / 2)) / (r.width / 2);
    const dy = (clientY - (r.top + r.height / 2)) / (r.height / 2);
    const dist = Math.min(1, Math.hypot(dx, dy));
    const ang = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
    onChange(Math.round(ang), Math.round(dist * 100) / 100);
  };
  const a = hue * Math.PI / 180;
  return (
    <div className="cwheel-wrap">
      <span className="tllab">{label}</span>
      <div ref={ref} className="cwheel"
        onPointerDown={(e) => { (e.target as Element).setPointerCapture(e.pointerId); pick(e.clientX, e.clientY); }}
        onPointerMove={(e) => { if (e.buttons) pick(e.clientX, e.clientY); }}>
        <i style={{ left: `${50 + sat * 44 * Math.cos(a)}%`, top: `${50 + sat * 44 * Math.sin(a)}%` }} />
      </div>
      <button className="btn sm" title="Bỏ nhuộm" onClick={() => onChange(0, 0)}>↺</button>
    </div>
  );
}

// Timeline cắt trực tiếp: kéo tay cầm IN/OUT trên dải khung hình, playhead đồng bộ player
function TrimBar({ vidRef, dur, name, srcKey, onKeep, onDrop, onAdd, onSeek }: {
  vidRef: RefObject<HTMLVideoElement | null>; dur: number; name: string; srcKey: string;
  onKeep: (a: number, b: number) => void; onDrop: (a: number, b: number) => void;
  onAdd: (a: number, b: number) => void; onSeek: (t: number) => void;
}) {
  const [inT, setInT] = useState(0);
  const [outT, setOutT] = useState(dur);
  const [ph, setPh] = useState(0);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<"in" | "out" | "seek" | null>(null);
  useEffect(() => { setInT(0); setOutT(dur); setPh(0); }, [name, dur]);
  useEffect(() => {  // đồng bộ playhead với player (nghe trên video element hiện tại)
    const v = vidRef.current; if (!v) return;
    const f = () => setPh(v.currentTime || 0);
    v.addEventListener("timeupdate", f);
    return () => v.removeEventListener("timeupdate", f);
  }, [vidRef, name, srcKey]);
  const posToT = (clientX: number) => {
    const el = trackRef.current; if (!el) return 0;
    const r = el.getBoundingClientRect();
    return Math.min(dur, Math.max(0, (clientX - r.left) / r.width * dur));
  };
  const down = (e: React.PointerEvent) => {
    const el = trackRef.current; if (!el || dur <= 0) return;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const r = el.getBoundingClientRect();
    const xIn = r.left + (inT / dur) * r.width;
    const xOut = r.left + (outT / dur) * r.width;
    dragRef.current = Math.abs(e.clientX - xIn) < 10 ? "in"
      : Math.abs(e.clientX - xOut) < 10 ? "out" : "seek";
    move(e);
  };
  const move = (e: React.PointerEvent) => {
    if (!dragRef.current || (!e.buttons && e.type === "pointermove")) return;
    const t = posToT(e.clientX);
    if (dragRef.current === "in") setInT(Math.min(t, outT - 0.15));
    else if (dragRef.current === "out") setOutT(Math.max(t, inT + 0.15));
    else { setPh(t); onSeek(t); }
  };
  const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
  const pct = (t: number) => `${dur > 0 ? (t / dur) * 100 : 0}%`;
  return (
    <div className="trimwrap">
      <div ref={trackRef} className="trimtrack" onPointerDown={down} onPointerMove={move}
        onPointerUp={() => { dragRef.current = null; }}>
        <img className="trimstrip" draggable={false} alt=""
          src={`/api/strip/${encodeURIComponent(name)}`}
          onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
        <div className="trimdim" style={{ left: 0, width: pct(inT) }} />
        <div className="trimdim" style={{ left: pct(outT), right: 0 }} />
        <div className="trimhandle in" style={{ left: pct(inT) }} />
        <div className="trimhandle out" style={{ left: pct(outT) }} />
        <div className="trimph" style={{ left: pct(ph) }} />
      </div>
      <div className="trimbar-foot">
        <span className="tllab">IN {fmt(inT)} → OUT {fmt(outT)} · chọn {(outT - inT).toFixed(1)}s / {dur.toFixed(1)}s</span>
        <div className="spacer" />
        <button className="btn sm" title="Chỉ giữ lại khoảng IN→OUT"
          onClick={() => onKeep(inT, outT)}>✂ Giữ đoạn</button>
        <button className="btn sm" title="Cắt bỏ khoảng IN→OUT, nối 2 phần còn lại"
          onClick={() => onDrop(inT, outT)}>🗑 Cắt bỏ đoạn</button>
        <button className="btn sm" title="Thêm khoảng IN→OUT vào danh sách ghép"
          onClick={() => onAdd(inT, outT)}>➕ Thêm vào danh sách</button>
      </div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<MediaItem | null>(null);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [leftTab, setLeftTab] = useState<"media" | "ai">("media");
  const [rightTab, setRightTab] = useState<"tool" | "jobs">("tool");
  const [tool, setTool] = useState<ToolKey>("transcribe");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [ab, setAb] = useState<"goc" | "sua">("sua");
  const fileRef = useRef<HTMLInputElement>(null);

  // caption params
  const [whModel, setWhModel] = useState("base");
  const [whEngine, setWhEngine] = useState("");
  const [whBurn, setWhBurn] = useState(true);
  const [whLang, setWhLang] = useState("");
  const [whMaxWords, setWhMaxWords] = useState(3);
  const [whFont, setWhFont] = useState("Arial");
  const [whEffect, setWhEffect] = useState("karaoke");
  const [whSize, setWhSize] = useState("M");
  const [whPos, setWhPos] = useState("bottom");
  const [whUpper, setWhUpper] = useState(false);
  // other params
  const [margin, setMargin] = useState(0.2);
  const [upMode, setUpMode] = useState<"ai" | "fast">("ai");
  const [upScale, setUpScale] = useState(2);
  const [upModel, setUpModel] = useState("realesr-animevideov3");
  const [preset, setPreset] = useState("tiktok");
  const [rifeMode, setRifeMode] = useState<"smooth" | "slowmo">("smooth");
  const [bgMode, setBgMode] = useState("green");
  const [ttsText, setTtsText] = useState("");
  const [ttsVoice, setTtsVoice] = useState("vi");
  const [ttsSpeed, setTtsSpeed] = useState(1.0);
  // batch (chọn nhiều tệp chạy hàng loạt)
  const [batch, setBatch] = useState(false);
  const [batchSel, setBatchSel] = useState<string[]>([]);
  // wave 2 (CapCut-style)
  const [autoCut, setAutoCut] = useState(true);
  const [autoCaption, setAutoCaption] = useState(true);
  const [autoPreset, setAutoPreset] = useState("");
  const [reframeMode, setReframeMode] = useState("blur");
  const [speedFactor, setSpeedFactor] = useState(2);
  const [colorName, setColorName] = useState("vivid");
  const [musicFile, setMusicFile] = useState("");
  const [musicVol, setMusicVol] = useState(0.25);
  const [musicDuck, setMusicDuck] = useState(true);
  // wave 3 (v0.5)
  const [mgTrans, setMgTrans] = useState("fade");
  const [mgDur, setMgDur] = useState(0.6);
  const [mgTarget, setMgTarget] = useState("169");
  const [mgMusic, setMgMusic] = useState("");
  const [bsMusic, setBsMusic] = useState("");
  const [bsTarget, setBsTarget] = useState("916");
  const [bsMaxSeg, setBsMaxSeg] = useState(60);
  const [aeDenoise, setAeDenoise] = useState(true);
  const [aeLoudness, setAeLoudness] = useState(true);
  const [brTitle, setBrTitle] = useState("");
  const [brSign, setBrSign] = useState("");
  const [brLogo, setBrLogo] = useState("");
  const [brCorner, setBrCorner] = useState("br");
  const [brOpacity, setBrOpacity] = useState(0.7);
  const [agTarget, setAgTarget] = useState("11");
  const [agTitle, setAgTitle] = useState("");
  // wave 9 (v1.1) — Chuỗi tự động + số luồng
  const [pipe, setPipe] = useState<PipeStep[]>([]);
  const [workers, setWorkers] = useState(2);
  // wave 8 (v1.0) — Phụ đề Viral
  const [vcModel, setVcModel] = useState("large-v3-turbo");
  const [vcKeyword, setVcKeyword] = useState(true);
  const [vcKeywords, setVcKeywords] = useState("");
  const [vcKaraoke, setVcKaraoke] = useState(true);
  const [vcFont, setVcFont] = useState(4.6);       // % chiều cao
  const [vcOutline, setVcOutline] = useState(0.7); // % chiều rộng
  const [vcPos, setVcPos] = useState(15);          // % từ đáy
  const [vcEndcard, setVcEndcard] = useState("");
  const [vcMusic, setVcMusic] = useState("");
  const [vcClusters, setVcClusters] = useState<ViralCluster[] | null>(null);
  const [vcDim, setVcDim] = useState<{ w: number; h: number }>({ w: 1080, h: 1920 });
  const [vcBusy, setVcBusy] = useState(false);
  const [vcNowText, setVcNowText] = useState("");  // overlay preview
  const vidElRef = useRef<HTMLVideoElement | null>(null);
  // wave 7 (v0.9) — b-roll
  const [brCount, setBrCount] = useState(3);
  // wave 6 (v0.8) — giáo án
  const [lsQuiz, setLsQuiz] = useState(5);
  const [lsPush, setLsPush] = useState(false);
  const [lsLevel, setLsLevel] = useState(1);
  const [lsDept, setLsDept] = useState("");
  // wave 5 (v0.7) — Claude sub
  const [aiEngine, setAiEngine] = useState("local");
  const [dirMsg, setDirMsg] = useState("");
  const [dirBusy, setDirBusy] = useState(false);
  const [dirLog, setDirLog] = useState<{ role: "user" | "ai"; text: string }[]>([]);
  // wave 4 (v0.6) — AI
  const [hlCount, setHlCount] = useState(3);
  const [hlMin, setHlMin] = useState(15);
  const [hlMax, setHlMax] = useState(60);
  const [hlMake, setHlMake] = useState(true);
  const [fbMode, setFbMode] = useState("blur");
  const [fbStrength, setFbStrength] = useState(1.0);
  const [ctPlatform, setCtPlatform] = useState("youtube");
  // Dịch phụ đề AI
  const [trLang, setTrLang] = useState("en");
  const [trBurn, setTrBurn] = useState(true);
  // Lồng tiếng / QC / Thumbnail
  const [dubVoice, setDubVoice] = useState("vi");
  const [qcAutocut, setQcAutocut] = useState(true);
  const [thumbCount, setThumbCount] = useState(6);
  // Đợt góp ý nhân viên + UI TTM
  const [toolCat, setToolCat] = useState<ToolCat | "all">("all");
  const [toolQ, setToolQ] = useState("");   // ô tìm công cụ kiểu CapCut
  const [muteMusic, setMuteMusic] = useState(false);
  const [spellfix, setSpellfix] = useState(false);
  const [aeStrong, setAeStrong] = useState(false);
  const [stabStrong, setStabStrong] = useState(true);
  const [vcFx, setVcFx] = useState("classic");
  const [pbRate, setPbRate] = useState(1);
  const [previewFb, setPreviewFb] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const GRADE_DEF = { exposure: 0, brightness: 0, contrast: 1, saturation: 1, gamma: 1, temperature: 0, hue: 0, vibrance: 0, sharp: 0, zoom: 1 };
  const [grade, setGrade] = useState<Record<string, number>>({ ...GRADE_DEF });
  const [shTint, setShTint] = useState({ h: 0, s: 0 });   // Nhuộm Tối (shadows)
  const [hlTint, setHlTint] = useState({ h: 0, s: 0 });   // Nhuộm Sáng (highlights)
  const [segs, setSegs] = useState<{ start: number; end: number }[]>([]);  // timeline cắt
  // multi-track lớp phủ + SAM2 track
  const [layers, setLayers] = useState<Layer[]>([]);
  const [laySel, setLaySel] = useState<number>(-1);
  const [tlNow, setTlNow] = useState(0);
  const layDrag = useRef<{ i: number; mode: "move" | "l" | "r"; x0: number; s0: number; e0: number } | null>(null);
  const ovDrag = useRef<{ i: number; pt: 1 | 2 } | null>(null);  // lớp + điểm (đầu/cuối) đang kéo
  const [projName, setProjName] = useState("");
  const [projList, setProjList] = useState<ProjectInfo[]>([]);
  const [projPick, setProjPick] = useState("");
  // Gói CapCut-parity
  const [afRatio, setAfRatio] = useState("916");
  const [vfxFx, setVfxFx] = useState("deep");
  const [enhMode, setEnhMode] = useState("natural");
  const [rtStr, setRtStr] = useState(1.0);
  // Wave automation: autopilot / script-to-video / gói đăng / tách cảnh / punch-in / đa ngữ
  const [apTarget, setApTarget] = useState("tiktok");
  const [svText, setSvText] = useState("");
  const [svVoice, setSvVoice] = useState("vi");
  const [svScenes, setSvScenes] = useState(5);
  const [svCaption, setSvCaption] = useState(true);
  const [svPortrait, setSvPortrait] = useState(true);
  const [ppVideo, setPpVideo] = useState(true);
  const [ssThr, setSsThr] = useState(0.35);
  const [piStrong, setPiStrong] = useState(false);
  const [mtLangs, setMtLangs] = useState<string[]>(["en"]);
  // Template chuỗi + thư mục nóng + lịch
  const [tplName, setTplName] = useState("");
  const [tplList, setTplList] = useState<TemplateInfo[]>([]);
  const [tplPick, setTplPick] = useState("");
  const [watchCfg, setWatchCfg] = useState<WatchCfg | null>(null);
  const [ingest, setIngest] = useState<{ token: string; url: string } | null>(null);
  const [hwOn, setHwOn] = useState(true);
  // Edit hàng loạt cả thư mục ổ cứng
  const [fdPath, setFdPath] = useState("");
  const [fdRec, setFdRec] = useState(false);
  const [fdScan, setFdScan] = useState<{ dir: string; files: { name: string; rel: string; size: number }[]; truncated: boolean } | null>(null);
  const [fdBusy, setFdBusy] = useState(false);
  const [trackPt, setTrackPt] = useState<{ x: number; y: number } | null>(null);
  const [trackFx, setTrackFx] = useState("blur");
  const [trackStr, setTrackStr] = useState(1.0);
  const layerIdRef = useRef(1);
  // Tìm cảnh CLIP
  const [clipQ, setClipQ] = useState("");
  const [clipRes, setClipRes] = useState<{ file: string; t: number; score: number }[]>([]);
  const [clipUnidx, setClipUnidx] = useState<string[]>([]);
  const [clipBusy, setClipBusy] = useState(false);
  const seekRef = useRef<number | null>(null);   // tua tới mốc sau khi video nạp
  // Settings — gói sub Claude
  const [csEnabled, setCsEnabled] = useState(true);
  const [csModel, setCsModel] = useState("sonnet");
  const [csAliases, setCsAliases] = useState<string[]>(["haiku", "sonnet", "opus"]);
  const [csPresent, setCsPresent] = useState(false);
  const [csTest, setCsTest] = useState("");
  const [csBusy, setCsBusy] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const refreshMedia = useCallback(async () => {
    try { setMedia(await getMedia()); } catch { /* not up yet */ }
  }, []);

  useEffect(() => {
    authStatus().then((s) => setAuthed(s.authenticated)).catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (!authed) return;
    const loadHealth = () => getHealth().then(setHealth).catch((e) => {
      if (String(e.message) === "401") setAuthed(false);
      setHealth(null);
    });
    loadHealth();
    refreshMedia();
    const t1 = setInterval(loadHealth, 10000);
    const t2 = setInterval(() => { getJobs().then(setJobs).catch(() => {}); }, 1500);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [authed, refreshMedia]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  };

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        const item = await uploadFile(f);
        setSelected(item);
        setPreview(null);
      }
      await refreshMedia();
      showToast("✅ Đã thêm vào kho media");
    } catch (e) { showToast("❌ " + String(e)); }
    finally { setUploading(false); }
  };

  const run = async (type: string, params: Record<string, unknown>) => {
    // TTS không cần tệp nguồn
    const targets = type === "tts" ? [""]
      : batch && batchSel.length > 0 ? batchSel
      : selected ? [selected.name] : [];
    if (targets.length === 0) { showToast("⚠️ Hãy chọn một video trong kho media trước"); return; }
    try {
      for (const name of targets) await createJob(type, name, params);
      setJobs(await getJobs());
      setRightTab("jobs");
      showToast(targets.length > 1
        ? `🚀 ${targets.length} việc "${TOOL_NAMES[type]}" đã vào hàng đợi — cứ để máy chạy`
        : `🚀 "${TOOL_NAMES[type]}" đã vào hàng đợi — chạy trên máy bạn`);
    } catch (e) { showToast("❌ " + String(e)); }
  };

  // chạy job LUÔN trên video đang chọn — cho thao tác timeline (cắt/lớp phủ),
  // vì IN/OUT + lớp thuộc về đúng video này, KHÔNG theo chế độ "Chọn nhiều"
  const runOne = async (type: string, params: Record<string, unknown>) => {
    if (!selected) { showToast("⚠️ Chọn video trước"); return; }
    try {
      await createJob(type, selected.name, params);
      setJobs(await getJobs());
      setRightTab("jobs");
      showToast(`🚀 "${TOOL_NAMES[type] ?? type}" — ${selected.name}`);
    } catch (e) { showToast("❌ " + String(e)); }
  };

  const sendDirector = async () => {
    const m = dirMsg.trim();
    if (!m || dirBusy) return;
    setDirLog((l) => [...l, { role: "user", text: m }]);
    setDirMsg("");
    setDirBusy(true);
    try {
      // gửi kèm file đang chọn → Claude tự hiểu khi không nêu tên file
      const res = await askDirector(m, selected?.name || "");
      let text = res.reply || "(không có phản hồi)";
      if (res.jobs.length) text += `\n\n▶ Đã xếp ${res.jobs.length} việc vào hàng đợi.`;
      if (res.errors.length) text += `\n⚠ ${res.errors.join("; ")}`;
      setDirLog((l) => [...l, { role: "ai", text }]);
      if (res.jobs.length) {
        setJobs(await getJobs());
        setRightTab("jobs");
      }
    } catch (e) {
      setDirLog((l) => [...l, { role: "ai", text: "❌ " + String((e as Error).message) }]);
    } finally {
      setDirBusy(false);
    }
  };

  // đồng bộ số luồng từ health
  useEffect(() => { if (health?.workers) setWorkers(health.workers); }, [health?.workers]);
  const changeWorkers = async (n: number) => {
    setWorkers(n);
    try {
      await fetch("/api/workers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workers: n }),
      });
    } catch { /* ignore */ }
  };

  // Settings — cấu hình gói sub Claude
  const loadClaudeSettings = useCallback(async () => {
    try {
      const s = await getClaudeSettings();
      setCsEnabled(s.enabled); setCsModel(s.model);
      setCsAliases(s.model_aliases); setCsPresent(s.cli_present);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    if (!showSettings) return;
    setCsTest(""); loadClaudeSettings();
    getIngestInfo().then(setIngest).catch(() => {});
    fetch("/api/perf").then((r) => r.json()).then((d) => setHwOn(!!d.hw)).catch(() => {});
  }, [showSettings, loadClaudeSettings]);
  // nạp cấu hình thư mục nóng khi mở tool folder
  useEffect(() => { if (tool === "folder") { getWatch().then((w) => { setWatchCfg(w); if (!fdPath && w.path) setFdPath(w.path); }).catch(() => {}); } }, [tool]);  // eslint-disable-line react-hooks/exhaustive-deps
  const applyClaude = async (patch: { enabled?: boolean; model?: string }) => {
    try {
      const s = await saveClaudeSettings(patch);
      setCsEnabled(s.enabled); setCsModel(s.model);
      setHealth(await getHealth());  // cập nhật feats (claude/translate...) theo trạng thái mới
    } catch (e) { showToast("❌ " + String((e as Error).message)); }
  };
  const runClaudeTest = async () => {
    setCsBusy(true); setCsTest("");
    try {
      const r = await testClaude();
      setCsTest(`✅ ${r.model} · ${r.ms}ms · "${r.reply}"`);
    } catch (e) { setCsTest("❌ " + String((e as Error).message)); }
    finally { setCsBusy(false); }
  };

  // Chuỗi tự động: thêm/xoá/đổi chỗ/đổi tham số bước, rồi chạy 1 job nối tiếp
  const addStep = (type: string) => {
    const d = PIPE_PALETTE.find((x) => x.type === type);
    if (d) setPipe((s) => [...s, { type, params: { ...d.def } }]);
  };
  const moveStep = (i: number, dir: -1 | 1) => setPipe((s) => {
    const j = i + dir; if (j < 0 || j >= s.length) return s;
    const a = [...s];[a[i], a[j]] = [a[j], a[i]]; return a;
  });
  const setStepParam = (i: number, k: string, v: unknown) =>
    setPipe((s) => s.map((st, j) => j === i ? { ...st, params: { ...st.params, [k]: v } } : st));
  const runPipeline = async () => {
    if (!selected) { showToast("⚠️ Chọn video trước"); return; }
    if (pipe.length === 0) { showToast("⚠️ Thêm ít nhất 1 bước"); return; }
    try {
      await createJob("pipeline", selected.name, { steps: pipe });
      setJobs(await getJobs()); setRightTab("jobs");
      showToast(`🔗 Chuỗi ${pipe.length} bước đã vào hàng đợi`);
    } catch (e) { showToast("❌ " + String(e)); }
  };

  // Phụ đề Viral: gói tham số preset
  const vcParams = () => ({
    model: vcModel, language: "vi", ai: aiEngine,
    keyword: vcKeyword,
    keywords: vcKeyword && vcKeywords.trim()
      ? vcKeywords.split(",").map((s) => s.trim()).filter(Boolean) : [],
    karaoke: vcKaraoke, style_fx: vcFx,
    fontsize: Math.round(vcDim.h * vcFont / 100),
    outline: Math.round(vcDim.w * vcOutline / 100 * 10) / 10,
    marginV: Math.round(vcDim.h * vcPos / 100),
    endcard: vcEndcard || null, music: vcMusic || null,
  });

  // phân tích cụm để chỉnh tay (poll job xong rồi tải json cụm)
  const analyzeViral = async () => {
    if (!selected) { showToast("⚠️ Chọn video trước"); return; }
    setVcBusy(true); setVcClusters(null);
    try {
      // engine ASR để auto (mlx/faster) — KHÔNG truyền aiEngine (local/claude) vào ô Whisper
      const job = await viralAnalyze(selected.name, vcModel, "");
      let done: Job | undefined;
      for (let i = 0; i < 200; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const js = await getJobs();
        const j = js.find((x) => x.id === job.id);
        if (j && (j.status === "done" || j.status === "error")) { done = j; break; }
      }
      if (!done || done.status !== "done") throw new Error(done?.error || "phân tích lỗi");
      const jsonOut = done.outputs.find((o) => o.name.endsWith(".json"));
      if (!jsonOut) throw new Error("không có dữ liệu cụm");
      const data = await fetchCuesJson(jsonOut.url);
      setVcDim({ w: data.w || 1080, h: data.h || 1920 });
      setVcClusters(data.clusters);
      showToast(`✅ ${data.clusters.length} cụm — sửa text rồi Cháy phụ đề`);
    } catch (e) { showToast("❌ " + String((e as Error).message)); }
    finally { setVcBusy(false); }
  };

  const renderViral = async (withEdits: boolean) => {
    if (!selected) { showToast("⚠️ Chọn video trước"); return; }
    const params: Record<string, unknown> = vcParams();
    if (withEdits && vcClusters) {
      params.clusters = vcClusters.map((c) => ({
        start: c.start, end: c.end, text: c.text,
        // giữ word timestamps để karaoke; nếu text bị sửa khác thì bỏ words (dùng text thô)
        // chuẩn hoá xuống dòng→cách vì cụm 2 dòng nối bằng "\n" nhưng words nối bằng " "
        words: c.text.replace(/\n/g, " ") === c.words.map((w) => w.t).join(" ")
          ? c.words : undefined,
      }));
    }
    try {
      await createJob("viral_caption", selected.name, params);
      setJobs(await getJobs()); setRightTab("jobs");
      showToast("🔥 Đang cháy phụ đề viral — chạy trên GPU máy bạn");
    } catch (e) { showToast("❌ " + String(e)); }
  };

  // overlay preview: hiện text cụm hiện tại trên player theo currentTime
  useEffect(() => {
    if (tool !== "viral" || !vcClusters) { setVcNowText(""); return; }
    const v = vidElRef.current;
    if (!v) return;
    const onT = () => {
      const t = v.currentTime;
      const c = vcClusters.find((x) => t >= x.start && t <= x.end);
      setVcNowText(c ? c.text : "");
    };
    v.addEventListener("timeupdate", onT);
    return () => v.removeEventListener("timeupdate", onT);
    // KHÔNG đưa stageSrc vào deps (TDZ sập app) — dùng các state gốc để re-attach
    // khi video remount (đổi video/preview), tránh listener nằm trên element cũ
  }, [tool, vcClusters, selected, preview, ab, previewFb]);

  // seed kích thước khung từ video đang chọn → "1 chạm" (không analyze) tính cỡ chữ đúng
  useEffect(() => {
    if (selected?.info.width && selected.info.height)
      setVcDim({ w: selected.info.width, h: selected.info.height });
  }, [selected]);

  // đổi nguồn xem → bỏ bản xem thử fallback của nguồn cũ
  useEffect(() => { setPreviewFb(null); }, [selected, preview, ab]);
  // áp tốc độ phát ngay khi đổi
  useEffect(() => { if (vidElRef.current) vidElRef.current.playbackRate = pbRate; }, [pbRate]);
  // đổi video → dọn lớp phủ + điểm track của video cũ
  useEffect(() => { setLayers([]); setLaySel(-1); setTrackPt(null); }, [selected?.name]);
  // đồng hồ hiện tại cho preview lớp phủ (nghe timeupdate của player)
  useEffect(() => {
    const v = vidElRef.current; if (!v || layers.length === 0) return;
    const f = () => setTlNow(v.currentTime || 0);
    v.addEventListener("timeupdate", f);
    return () => v.removeEventListener("timeupdate", f);
    // KHÔNG dùng stageSrc trong deps (khai báo phía dưới → TDZ sập app — vết xe cũ);
    // selected/preview/ab đủ để re-attach khi video remount
  }, [layers.length, previewFb, selected, preview, ab]);

  // thêm lớp phủ mới (mặc định 0 → hết video, giữa màn hình)
  const addLayer = (kind: Layer["kind"]) => {
    if (!selected) { showToast("⚠️ Chọn video trước"); return; }
    const d = selected.info.duration;
    if (kind === "image" && imageFiles.length === 0) { showToast("⚠️ Kéo 1 ảnh (png/jpg) vào kho media trước"); return; }
    if (kind === "audio" && audioFiles.length === 0) { showToast("⚠️ Kéo 1 file nhạc vào kho media trước"); return; }
    const vidFiles = media.filter((m) => m.info.width && m.info.duration > 0.2 && m.name !== selected.name);
    if (kind === "video" && vidFiles.length === 0) { showToast("⚠️ Cần thêm 1 video khác trong kho để làm PiP"); return; }
    const l: Layer = {
      id: layerIdRef.current++, kind, anim: "none",
      file: kind === "image" ? imageFiles[0].name
        : kind === "audio" ? audioFiles[0].name
        : kind === "video" ? vidFiles[0].name : undefined,
      text: kind === "text" ? "Chữ của bạn" : undefined, color: "#FFD400",
      start: 0, end: Math.min(d, kind === "audio" ? d : Math.max(3, d * 0.4)),
      x: kind === "image" ? 0.92 : kind === "video" ? 0.9 : 0.5,
      y: kind === "image" ? 0.06 : kind === "video" ? 0.1 : 0.82,
      scale: kind === "video" ? 0.35 : 0.22, opacity: 1, size: 0.06,
      volume: kind === "video" ? 0 : 0.5,
    };
    setLayers((ls) => [...ls, l]); setLaySel(layers.length);
  };
  const patchLayer = (i: number, patch: Partial<Layer>) =>
    setLayers((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  // merge/beatsync: NHIỀU clip vào MỘT job (thứ tự = thứ tự chọn trong batch)
  const runMulti = async (type: string, params: Record<string, unknown>) => {
    const sources = batch && batchSel.length > 0 ? batchSel
      : selected ? [selected.name] : [];
    if (sources.length === 0) { showToast("⚠️ Hãy chọn video trước — bật 'Chọn nhiều' để lấy nhiều clip"); return; }
    try {
      await createJob(type, sources[0], { ...params, files: sources.slice(1) });
      setJobs(await getJobs());
      setRightTab("jobs");
      showToast(`🚀 "${TOOL_NAMES[type]}" (${sources.length} clip) đã vào hàng đợi`);
    } catch (e) { showToast("❌ " + String(e)); }
  };

  const openPreview = (j: Job, url: string, name: string) => {
    const item = media.find((m) => m.name === j.input) ?? null;
    if (item) setSelected(item);
    setPreview({ input: j.input, url, name });
    setAb("sua");
  };

  const feats = health?.features ?? {};
  // mp3 có ảnh bìa nhúng vẫn là audio (vcodec mjpeg/png attached_pic)
  const COVER_CODECS = ["mjpeg", "png", "bmp", "gif"];
  const audioFiles = media.filter((m) => m.info.has_audio &&
    (!m.info.width || COVER_CODECS.includes(m.info.vcodec ?? "")));
  const imageFiles = media.filter((m) => m.info.width && !m.info.has_audio && m.info.duration < 0.1);
  const running = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const doneCount = jobs.filter((j) => j.status === "done").length;
  const gpu = health?.gpu;
  const vramPct = gpu ? Math.round((gpu as any).vram_used_mb / gpu.vram_total_mb * 100) : 0;

  const stageSrc = preview
    ? (ab === "goc" ? `/files/uploads/${preview.input}` : preview.url)
    : selected?.url;

  // xem thử chỉnh màu GẦN ĐÚNG bằng CSS filter (render thật vẫn qua ffmpeg)
  const gradeCss = [
    `brightness(${(1 + grade.brightness + grade.exposure * 0.25).toFixed(2)})`,
    `contrast(${grade.contrast.toFixed(2)})`,
    `saturate(${(grade.saturation + grade.vibrance * 0.3).toFixed(2)})`,
    grade.hue ? `hue-rotate(${grade.hue}deg)` : "",
    grade.temperature ? `sepia(${Math.min(0.35, Math.abs(grade.temperature) / 300).toFixed(2)})` : "",
  ].filter(Boolean).join(" ");

  if (authed === null) {
    return <div className="loginwrap"><div className="logindesc">Đang kiểm tra phiên...</div></div>;
  }
  if (!authed) {
    return <LoginScreen onOk={() => setAuthed(true)} />;
  }

  return (
    <div className="app">
      {/* ================= TITLEBAR ================= */}
      <header className="titlebar">
        <div className="logo"><span className="mk">L</span><b>LOCAL STUDIO</b></div>
        <span className="pname">v2.3 — dựng &amp; xử lý AI trên máy</span>
        <div className="spacer" />
        <div className={"offline" + (health ? "" : " err")}>
          <span className="d" /><span>{health ? "OFFLINE · FOOTAGE KHÔNG RỜI MÁY" : "MẤT KẾT NỐI BACKEND"}</span>
        </div>
        {gpu && <span className="gpuchip">⚡ {gpu.name.replace("NVIDIA GeForce ", "")} · {Math.round(gpu.vram_total_mb / 1024)}GB</span>}
        <button className="btn" onClick={() => openOutputs()}>📂 Kết quả</button>
        <button className="btn pri" onClick={() => { setTool("export"); setRightTab("tool"); setLeftTab("ai"); }}>
          Xuất bản
        </button>
        <button className="btn" title="Cài đặt gói sub Claude" onClick={() => setShowSettings(true)}>⚙️</button>
        <button className="btn" title="Đăng xuất admin"
          onClick={async () => { await logout(); setAuthed(false); }}>🔒</button>
      </header>

      {showSettings && (
        <div className="modal-bg" onClick={() => setShowSettings(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <b>⚙️ Cài đặt — Gói sub Claude</b>
              <button className="btn" onClick={() => setShowSettings(false)}>✕</button>
            </div>
            <p className="logindesc" style={{ textAlign: "left", marginBottom: 12 }}>
              Các tính năng AI (Đạo diễn, Dịch phụ đề, Tái chế nội dung, viết nội dung...)
              dùng gói Claude bạn đã <b>đăng nhập sẵn trên máy</b> qua Claude Code CLI.
              Chỉ văn bản (transcript) được gửi đi — video luôn nằm trên máy.
            </p>
            <div className="field">
              <label>Claude CLI trên máy</label>
              <div className={"csrow " + (csPresent ? "ok" : "bad")}>
                {csPresent ? "✅ Đã phát hiện & đăng nhập" : "❌ Không thấy — cài Claude Code + đăng nhập rồi thử lại"}
              </div>
            </div>
            <label className="chk"><input type="checkbox" checked={csEnabled}
              onChange={(e) => { setCsEnabled(e.target.checked); applyClaude({ enabled: e.target.checked }); }} />
              Bật Claude cho các tính năng AI (tắt = mọi thứ chạy Qwen local)</label>
            <div className="field" style={{ marginTop: 10 }}>
              <label>Model Claude</label>
              <div className="seg">
                {(csAliases.includes(csModel) ? csAliases : [...csAliases, csModel]).map((m) => (
                  <button key={m} className={csModel === m ? "on" : ""}
                    onClick={() => { setCsModel(m); applyClaude({ model: m }); }}>{m}</button>
                ))}
              </div>
            </div>
            <div className="field">
              <label>…hoặc nhập model ID cụ thể</label>
              <div style={{ display: "flex", gap: 6 }}>
                <input className="logininput" style={{ flex: 1 }} placeholder="vd claude-opus-4-8"
                  value={csModel} onChange={(e) => setCsModel(e.target.value)} />
                <button className="btn" onClick={() => applyClaude({ model: csModel })}>Áp dụng</button>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
              <button className="btn pri" disabled={csBusy || !csPresent} onClick={runClaudeTest}>
                {csBusy ? "Đang gọi Claude..." : "🔌 Test kết nối"}
              </button>
              {csTest && <span className="logindesc" style={{ margin: 0 }}>{csTest}</span>}
            </div>
            <div className="field" style={{ marginTop: 14 }}>
              <label>⚡ Tăng tốc phần cứng (VideoToolbox GPU)</label>
              <label className="chk"><input type="checkbox" checked={hwOn}
                onChange={async (e) => {
                  const r = await fetch("/api/perf", { method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ hw: e.target.checked }) });
                  const d = await r.json(); setHwOn(d.hw);
                  showToast(d.hw ? "⚡ Encode GPU BẬT — mọi job nhanh hơn" : "Encode CPU (x264 chất lượng tối đa)");
                }} />
                Encode video bằng GPU Apple — nhanh hơn ~40%, CPU rảnh gấp 8 lần khi chạy nhiều job</label>
            </div>
            <div className="field" style={{ marginTop: 14 }}>
              <label>📲 Nhận video từ iPhone (Apple Shortcuts)</label>
              <div className="csrow" style={{ wordBreak: "break-all", fontSize: 10.5 }}>
                {ingest ? <>Tạo Shortcut: <b>Chia sẻ → Get Contents of URL</b> · Method POST ·
                  Request Body = Form, field <code>file</code> = video · URL:<br />
                  <code>{ingest.url}</code>
                  {watchCfg?.steps?.length ? " — video gửi lên sẽ TỰ xử lý theo chuỗi thư mục nóng" : ""}</>
                  : "đang tải..."}
              </div>
            </div>
            <div className="field" style={{ marginTop: 16 }}>
              <label>Trạng thái hệ thống (tính năng nào tắt → xem lý do & cách cài)</label>
              <div className="statgrid">
                {TOOLS.map((t) => {
                  const fk = FEAT_KEY[t.key];
                  const ok = fk ? !!feats[fk] : true;
                  return (
                    <div key={t.key} className={"statrow" + (ok ? "" : " bad")}
                      title={ok ? "sẵn sàng" : TOOL_REQS[t.key] || "thiếu thành phần"}>
                      {ok ? "✅" : "❌"} {t.icon} {t.name}
                      {!ok && <span className="statwhy"> — {TOOL_REQS[t.key] || "thiếu thành phần"}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* nút chat Đạo diễn AI nổi — truy cập từ mọi màn hình (kiểu TTM "Chat Lucy AI") */}
      {feats.director && !chatOpen && (
        <button className="fabchat" onClick={() => setChatOpen(true)}>💬 Đạo diễn AI</button>
      )}
      {chatOpen && (
        <div className="chatbox">
          <div className="modal-head">
            <b>🎬 Đạo diễn AI {selected ? `· ${selected.name}` : ""}</b>
            <button className="btn" onClick={() => setChatOpen(false)}>✕</button>
          </div>
          <div className="chatlog">
            {dirLog.length === 0 && <div className="hint">Ra lệnh bằng lời — vd "cắt lặng rồi làm phụ đề viral video đang chọn".</div>}
            {dirLog.map((m, i) => (
              <div key={i} className={"dmsg " + m.role}>{m.text}</div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <textarea className="ttsbox" rows={2} maxLength={2000} value={dirMsg}
              placeholder="Yêu cầu của bạn..." onChange={(e) => setDirMsg(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!dirBusy && dirMsg.trim()) sendDirector(); } }} />
            <button className="btn pri" disabled={dirBusy || !dirMsg.trim()} onClick={sendDirector}>
              {dirBusy ? "⏳" : "▶"}
            </button>
            {dirBusy && <button className="btn" onClick={() => stopDirector()}>⏹</button>}
          </div>
        </div>
      )}

      {/* ================= MID ================= */}
      <div className="mid">
        {/* ---------- LEFT ---------- */}
        <section className="left">
          <nav className="tabs">
            <button className={"tab" + (leftTab === "media" ? " on" : "")} onClick={() => setLeftTab("media")}>Kho media</button>
            <button className={"tab" + (leftTab === "ai" ? " on" : "")} onClick={() => setLeftTab("ai")}>Công cụ AI</button>
          </nav>

          {leftTab === "media" && (
            <div className="lpanel">
              <div className="lp-title"><span>Trong dự án · {media.length} tệp</span>
                <button className={"batchbtn" + (batch ? " on" : "")}
                  onClick={() => { setBatch(!batch); setBatchSel([]); }}>
                  {batch ? `✓ Đã chọn ${batchSel.length}` : "☰ Chọn nhiều"}
                </button>
              </div>
              <div className={"dropzone" + (uploading ? " busy" : "")}
                onClick={() => fileRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files); }}>
                {uploading ? "Đang nhập tệp..." : <>⬆️ Kéo-thả video vào đây<br /><small>tệp không được sao chép đi đâu cả</small></>}
              </div>
              <input ref={fileRef} type="file" accept="video/*,audio/*,image/png,image/jpeg,image/webp" multiple hidden
                onChange={(e) => onFiles(e.target.files)} />
              <div className="mgrid">
                {media.map((m) => (
                  <div key={m.name}
                    className={"mitem" + ((batch ? batchSel.includes(m.name) : selected?.name === m.name) ? " sel" : "")}
                    onClick={() => {
                      if (batch) {
                        setBatchSel((s) => s.includes(m.name) ? s.filter((x) => x !== m.name) : [...s, m.name]);
                      } else { setSelected(m); setPreview(null); }
                    }}>
                    <div className="th">
                      <img src={`/api/thumb/${encodeURIComponent(m.name)}`} loading="lazy" alt=""
                        onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                      <span className="dur">{fmtDur(m.info.duration)}</span>
                      {batch && <span className={"bsel" + (batchSel.includes(m.name) ? " on" : "")}>
                        {batchSel.includes(m.name) ? "✓" : ""}</span>}
                      {!batch && <button className="mdel" title="Xoá khỏi kho media"
                        onClick={async (e) => {
                          e.stopPropagation();
                          if (!window.confirm(`Xoá "${m.name}" khỏi kho media?`)) return;
                          try {
                            await deleteMedia(m.name);
                            if (selected?.name === m.name) setSelected(null);
                            setMedia(await getMedia());
                            showToast("🗑 Đã xoá " + m.name);
                          } catch (err) { showToast("❌ " + String((err as Error).message)); }
                        }}>🗑</button>}
                    </div>
                    <div className="nm">{m.name}</div>
                  </div>
                ))}
              </div>
              {media.length === 0 && <div className="empty">Chưa có tệp nào</div>}
            </div>
          )}

          {leftTab === "ai" && (
            <div className="lpanel">
              {gpu && (
                <div className="hwbox">
                  <div className="hwrow"><span>{gpu.name.replace("NVIDIA GeForce ", "")}</span>
                    <b>{gpu.type === "apple"
                      ? `${Math.round(gpu.vram_total_mb / 1024)} GB RAM hợp nhất`
                      : `${((gpu as any).vram_used_mb / 1024).toFixed(1)} / ${Math.round(gpu.vram_total_mb / 1024)} GB`}</b></div>
                  {gpu.type !== "apple" && <div className="meter"><i style={{ width: `${vramPct}%` }} /></div>}
                  <div className="hwrow"><span>Hàng đợi</span><b>{running > 0 ? `${running} việc đang chạy` : "trống"}</b></div>
                  <div className="hwrow" style={{ marginTop: 4 }}>
                    <span>Số luồng song song</span>
                    <div className="seg sm">{[1, 2, 3, 4].map((n) => (
                      <button key={n} className={workers === n ? "on" : ""} onClick={() => changeWorkers(n)}>{n}</button>))}</div>
                  </div>
                </div>
              )}
              <div className="cattabs">
                {CAT_TABS.map(([c, l]) => (
                  <button key={c} className={toolCat === c ? "on" : ""}
                    onClick={() => setToolCat(c)}>{l}</button>
                ))}
              </div>
              <input className="toolsearch" placeholder="🔍 Tìm công cụ... (vd: phụ đề, zoom, giọng)"
                value={toolQ} onChange={(e) => setToolQ(e.target.value)} />
              <div className="toolgrid">
                {TOOLS.map((t, ti) => ({ t, n: ti + 1 }))
                  .filter(({ t }) => toolCat === "all" || TOOL_CATS[t.key] === toolCat)
                  .filter(({ t }) => !toolQ.trim()
                    || (t.name + " " + t.desc).toLowerCase().includes(toolQ.trim().toLowerCase()))
                  .map(({ t, n }) => {
                  const fk = FEAT_KEY[t.key];
                  const off = fk ? !feats[fk] : false;
                  return (
                  <button key={t.key}
                    title={off ? `Chưa sẵn sàng — ${TOOL_REQS[t.key] || "thiếu thành phần trên máy chủ"}` : t.desc}
                    className={"aitool cat-" + (TOOL_CATS[t.key] || "edit")
                      + (tool === t.key ? " sel" : "") + (off ? " disabled" : "")}
                    onClick={() => { setTool(t.key); setRightTab("tool"); }}>
                    <span className="ai-num">{n}</span>
                    <span className={"ai-g" + (t.gpu ? "" : " cpu")}>{t.gpu ? "GPU" : "CPU"}</span>
                    <span className="ai-ic">{t.icon}</span>
                    <span className="ai-n">{t.name}</span>
                  </button>
                  );
                })}
              </div>
              <div className="hint">💡 Job xếp hàng chạy nền — cứ thêm nhiều việc rồi để máy tự xử lý. 0 credit, không giới hạn.</div>
            </div>
          )}
        </section>

        {/* ---------- STAGE ---------- */}
        <section className="stagewrap">
          <div className="player">
            {stageSrc ? (
              <div className="canvas"
                onPointerMove={(e) => {
                  const g = ovDrag.current;
                  if (!g) return;
                  if (!e.buttons) { ovDrag.current = null; return; }  // thả chuột ngoài canvas → không "dính"
                  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                  const nx = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
                  const ny = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
                  patchLayer(g.i, g.pt === 2 ? { x2: nx, y2: ny } : { x: nx, y: ny });
                }}
                onPointerUp={() => { ovDrag.current = null; }}>
                {selected && !preview && (
                  <span className="reslab">{selected.info.width}×{selected.info.height} · {Math.round(selected.info.fps)}fps</span>
                )}
                {preview && (
                  <span className={"reslab" + (ab === "sua" ? " up" : "")}>
                    {ab === "sua" ? "BẢN SỬA" : "BẢN GỐC"}
                  </span>
                )}
                <video ref={vidElRef} key={previewFb ?? stageSrc} src={previewFb ?? stageSrc}
                  controls autoPlay={!!preview}
                  style={tool === "grade" ? { filter: gradeCss } : undefined}
                  onLoadedMetadata={(e) => {
                    const v = e.target as HTMLVideoElement;
                    v.playbackRate = pbRate;
                    if (seekRef.current != null) { v.currentTime = seekRef.current; seekRef.current = null; }
                  }}
                  onError={() => {
                    // trình duyệt không phát được (mkv/avi/hevc...) → bản xem thử H.264
                    if (!previewFb && selected && stageSrc === selected.url && selected.info.width) {
                      setPreviewFb(`/api/preview/${encodeURIComponent(selected.name)}`);
                      showToast("🎞 Đang tạo bản xem thử H.264 (lần đầu hơi lâu)...");
                    }
                  }} />
                {/* SAM2: bắt click chọn đối tượng khi mở tool Track */}
                {tool === "track" && !preview && (
                  <div className="clickcatch"
                    onPointerDown={(e) => {
                      const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                      setTrackPt({
                        x: Math.round((e.clientX - r.left) / r.width * 1000) / 1000,
                        y: Math.round((e.clientY - r.top) / r.height * 1000) / 1000,
                      });
                    }}>
                    {trackPt && <span className="trackdot" style={{ left: `${trackPt.x * 100}%`, top: `${trackPt.y * 100}%` }} />}
                    {!trackPt && <span className="trackhint">👆 Bấm vào ĐỐI TƯỢNG cần theo dõi</span>}
                  </div>
                )}
                {/* preview lớp phủ multi-track: kéo điểm ĐẦU (liền) + điểm CUỐI (mờ) */}
                {!preview && layers.map((l, i) => {
                  if (l.kind === "audio") return null;
                  const show = laySel === i || (tlNow >= l.start && tlNow <= l.end);
                  if (!show) return null;
                  const hasKf = l.x2 != null && l.y2 != null;
                  // đang phát trong cửa sổ + có keyframe → nội suy vị trí như bản render
                  const k = hasKf && tlNow > l.start && laySel !== i
                    ? Math.min(1, Math.max(0, (tlNow - l.start) / Math.max(0.1, l.end - l.start)))
                    : 0;
                  const cx = hasKf ? l.x + ((l.x2 as number) - l.x) * k : l.x;
                  const cy = hasKf ? l.y + ((l.y2 as number) - l.y) * k : l.y;
                  const pos = (px: number, py: number) => ({
                    left: `${px * 100}%`, top: `${py * 100}%`,
                    transform: `translate(-${px * 100}%, -${py * 100}%)`,
                  } as const);
                  const common = {
                    ...pos(cx, cy),
                    outline: laySel === i ? "1.5px dashed var(--amber)" : undefined,
                  };
                  const grab = (pt: 1 | 2) => (e: { stopPropagation: () => void }) => {
                    e.stopPropagation(); setLaySel(i); ovDrag.current = { i, pt };
                  };
                  const ghost = laySel === i && hasKf && (
                    <span key={l.id + "-kf"} className="ovlayer ovghost"
                      style={{ ...pos(l.x2 as number, l.y2 as number) }}
                      onPointerDown={grab(2)}>⤳ cuối</span>
                  );
                  return l.kind === "image" || l.kind === "video" ? (
                    <span key={l.id}>
                      <img className={"ovlayer" + (l.kind === "video" ? " ovpip" : "")}
                        draggable={false} alt=""
                        src={l.kind === "video"
                          ? `/api/thumb/${encodeURIComponent(l.file || "")}`
                          : `/files/uploads/${encodeURIComponent(l.file || "")}`}
                        style={{ ...common, width: `${l.scale * 100}%`, opacity: l.opacity }}
                        onPointerDown={grab(1)} />
                      {ghost}
                    </span>
                  ) : (
                    <span key={l.id}>
                      <span className="ovlayer ovtext"
                        style={{ ...common, fontSize: `${l.size * 100}cqh`, color: l.color }}
                        onPointerDown={grab(1)}>
                        {l.text}
                      </span>
                      {ghost}
                    </span>
                  );
                })}
                {tool === "viral" && vcNowText && !preview && (
                  <div className="vcoverlay" style={{ bottom: `${vcPos}%` }}>
                    {vcNowText.split("\n").map((ln, i) => (
                      <div key={i} className="vcline"
                        style={{ fontSize: `${vcFont}cqh`,
                          WebkitTextStrokeWidth: `${vcOutline * 0.9}cqh` }}>
                        {renderVcLine(ln, vcKeyword, vcKeywords)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="emptyframe">chọn video trong kho media để bắt đầu</div>
            )}
          </div>
          <div className="transport">
            {preview ? (
              <>
                <div className="abseg">
                  <button className={ab === "goc" ? "on" : ""} onClick={() => setAb("goc")}>Gốc</button>
                  <button className={ab === "sua" ? "on" : ""} onClick={() => setAb("sua")}>Bản sửa ✨</button>
                </div>
                <span className="abname">{preview.name}</span>
                <a className="btn sm" href={preview.url} download>⬇ Tải về</a>
                <button className="xbtn" title="Đóng xem trước" onClick={() => setPreview(null)}>✕</button>
              </>
            ) : (
              <span className="selchip">
                {selected
                  ? `${selected.name} · ${fmtDur(selected.info.duration)} · ${fmtSize(selected.info.size)}`
                  : "chưa chọn tệp nào"}
              </span>
            )}
            <div className="spacer" />
            <div className="seg sm" title="Tốc độ phát xem thử">
              {[0.5, 1, 1.5, 2].map((r) => (
                <button key={r} className={pbRate === r ? "on" : ""}
                  onClick={() => setPbRate(r)}>{r}×</button>))}
            </div>
            <span className="selchip">{running > 0 ? `▶ ${running} việc đang chạy nền` : "máy đang rảnh"}</span>
          </div>
          {selected && !preview && selected.info.width && selected.info.duration > 0.2 && (
            <div className="tlbox">{/* timeline NLE: kéo IN/OUT cắt trực tiếp + sóng âm */}
              <TrimBar vidRef={vidElRef} dur={selected.info.duration}
                name={selected.name} srcKey={previewFb ?? stageSrc ?? ""}
                onSeek={(t) => { if (vidElRef.current) vidElRef.current.currentTime = t; }}
                onKeep={(a, b) => runOne("cutlist", { segments: [{ start: a, end: b }] })}
                onDrop={(a, b) => {
                  const d = selected.info.duration;
                  const keep = [{ start: 0, end: a }, { start: b, end: d }]
                    .filter((s) => s.end - s.start > 0.2);
                  if (!keep.length) { showToast("⚠️ Cắt bỏ hết thì không còn gì"); return; }
                  runOne("cutlist", { segments: keep });
                }}
                onAdd={(a, b) => { setSegs((s) => [...s, { start: a, end: b }]); showToast("➕ Đã thêm đoạn vào danh sách ghép"); }} />
              {segs.length > 0 && (
                <div className="tlrow">
                  <span className="tllab">📋 Ghép</span>
                  <div className="segchips">
                    {segs.map((s, i) => (
                      <span key={i} className="segchip">{i + 1}. {s.start.toFixed(1)}–{s.end.toFixed(1)}s
                        <button onClick={() => setSegs((x) => x.filter((_, j) => j !== i))}>✕</button>
                      </span>
                    ))}
                    <button className="btn sm pri" onClick={() => { runOne("cutlist", { segments: segs }); setSegs([]); }}>
                      🎬 Render {segs.length} đoạn
                    </button>
                    <button className="btn sm" onClick={() => setSegs([])}>Xoá hết</button>
                  </div>
                </div>
              )}
              {selected.info.has_audio && (
                <div className="tlrow">
                  <span className="tllab">🔊 Âm</span>
                  <img className="tlwave" loading="lazy" alt=""
                    src={`/api/wave/${encodeURIComponent(selected.name)}`}
                    onError={(e) => { (e.target as HTMLImageElement).parentElement!.style.display = "none"; }} />
                </div>
              )}
              {/* ===== multi-track: các lớp phủ ảnh/chữ/nhạc ===== */}
              {layers.map((l, i) => {
                const d = selected.info.duration;
                const toT = (clientX: number, el: HTMLElement) => {
                  const r = el.getBoundingClientRect();
                  return Math.min(d, Math.max(0, (clientX - r.left) / r.width * d));
                };
                return (
                  <div className="tlrow" key={l.id}>
                    <span className="tllab" style={{ cursor: "pointer" }} onClick={() => setLaySel(laySel === i ? -1 : i)}>
                      {l.kind === "image" ? "🖼" : l.kind === "text" ? "🔤" : l.kind === "video" ? "📹" : "🎵"} Lớp {i + 1}
                    </span>
                    <div className="lytrack"
                      onPointerDown={(e) => {
                        setLaySel(i);
                        (e.currentTarget as Element).setPointerCapture(e.pointerId);
                        const el = e.currentTarget as HTMLElement;
                        const r = el.getBoundingClientRect();
                        const xs = r.left + (l.start / d) * r.width;
                        const xe = r.left + (l.end / d) * r.width;
                        const mode = Math.abs(e.clientX - xs) < 8 ? "l"
                          : Math.abs(e.clientX - xe) < 8 ? "r" : "move";
                        layDrag.current = { i, mode, x0: toT(e.clientX, el), s0: l.start, e0: l.end };
                      }}
                      onPointerMove={(e) => {
                        const g = layDrag.current;
                        if (!g || g.i !== i || !e.buttons) return;
                        const t = toT(e.clientX, e.currentTarget as HTMLElement);
                        if (g.mode === "l") patchLayer(i, { start: Math.min(t, l.end - 0.2) });
                        else if (g.mode === "r") patchLayer(i, { end: Math.max(t, l.start + 0.2) });
                        else {
                          const dt = t - g.x0, len = g.e0 - g.s0;
                          const ns = Math.min(Math.max(0, g.s0 + dt), d - len);
                          patchLayer(i, { start: ns, end: ns + len });
                        }
                      }}
                      onPointerUp={() => { layDrag.current = null; }}>
                      <div className={"lyblock " + l.kind + (laySel === i ? " sel" : "")}
                        style={{ left: `${(l.start / d) * 100}%`, width: `${((l.end - l.start) / d) * 100}%` }}>
                        {l.kind === "text" ? (l.text || "").slice(0, 18) : (l.file || "").slice(0, 18)}
                      </div>
                    </div>
                  </div>
                );
              })}
              {laySel >= 0 && layers[laySel] && (() => {
                const l = layers[laySel];
                return (
                  <div className="lyinspect">
                    {l.kind === "image" && (<>
                      <select value={l.file} onChange={(e) => patchLayer(laySel, { file: e.target.value })}>
                        {imageFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                      <label>Cỡ <input type="range" min={0.05} max={0.8} step={0.01} value={l.scale}
                        onChange={(e) => patchLayer(laySel, { scale: parseFloat(e.target.value) })} /></label>
                      <label>Mờ/đục <input type="range" min={0.1} max={1} step={0.05} value={l.opacity}
                        onChange={(e) => patchLayer(laySel, { opacity: parseFloat(e.target.value) })} /></label>
                    </>)}
                    {l.kind === "text" && (<>
                      <input className="lytext" value={l.text || ""} maxLength={200}
                        onChange={(e) => patchLayer(laySel, { text: e.target.value })} />
                      <input type="color" value={l.color || "#FFD400"}
                        onChange={(e) => patchLayer(laySel, { color: e.target.value })} />
                      <label>Cỡ <input type="range" min={0.03} max={0.15} step={0.005} value={l.size}
                        onChange={(e) => patchLayer(laySel, { size: parseFloat(e.target.value) })} /></label>
                    </>)}
                    {l.kind === "video" && (<>
                      <select value={l.file} onChange={(e) => patchLayer(laySel, { file: e.target.value })}>
                        {media.filter((m) => m.info.width && m.info.duration > 0.2 && m.name !== selected.name)
                          .map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                      <label>Cỡ <input type="range" min={0.1} max={0.7} step={0.01} value={l.scale}
                        onChange={(e) => patchLayer(laySel, { scale: parseFloat(e.target.value) })} /></label>
                      <label>Tiếng PiP <input type="range" min={0} max={1.5} step={0.05} value={l.volume}
                        onChange={(e) => patchLayer(laySel, { volume: parseFloat(e.target.value) })} /></label>
                    </>)}
                    {l.kind === "audio" && (<>
                      <select value={l.file} onChange={(e) => patchLayer(laySel, { file: e.target.value })}>
                        {audioFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                      <label>Âm lượng <input type="range" min={0.05} max={2} step={0.05} value={l.volume}
                        onChange={(e) => patchLayer(laySel, { volume: parseFloat(e.target.value) })} /></label>
                    </>)}
                    {l.kind !== "audio" && (
                      <label>Bay vào
                        <select value={l.anim || "none"} disabled={l.x2 != null}
                          onChange={(e) => patchLayer(laySel, { anim: e.target.value })}>
                          {ANIMS.map(([v, lb]) => <option key={v} value={v}>{lb}</option>)}
                        </select>
                      </label>
                    )}
                    {l.kind !== "audio" && (
                      <button className={"btn sm" + (l.x2 != null ? " pri" : "")}
                        title="Bay từ vị trí đầu đến vị trí cuối trong khoảng thời gian của lớp"
                        onClick={() => patchLayer(laySel, l.x2 != null
                          ? { x2: undefined, y2: undefined }
                          : { x2: Math.min(1, l.x + 0.25), y2: l.y })}>
                        ⤳ Keyframe {l.x2 != null ? "BẬT" : "tắt"}
                      </button>
                    )}
                    {l.kind !== "audio" && <span className="tllab">
                      {l.x2 != null ? "kéo điểm ĐẦU (liền) và điểm CUỐI (mờ) trên video" : "kéo trực tiếp trên video để đặt vị trí"}</span>}
                    <button className="btn sm" style={{ color: "var(--warn)" }}
                      onClick={() => { setLayers((ls) => ls.filter((_, j) => j !== laySel)); setLaySel(-1); }}>🗑 Xoá lớp</button>
                  </div>
                );
              })()}
              <div className="tlrow">
                <span className="tllab">➕ Lớp</span>
                <div className="segchips">
                  <button className="btn sm" onClick={() => addLayer("image")}>🖼 Ảnh/Logo</button>
                  <button className="btn sm" onClick={() => addLayer("text")}>🔤 Chữ</button>
                  <button className="btn sm" onClick={() => addLayer("audio")}>🎵 Nhạc</button>
                  <button className="btn sm" onClick={() => addLayer("video")}>📹 Video PiP</button>
                  {layers.length > 0 && (
                    <button className="btn sm pri" onClick={() => runOne("composite", {
                      layers: layers.map(({ id, ...rest }) => rest),
                    })}>🎬 Render {layers.length} lớp phủ</button>
                  )}
                </div>
              </div>
              <div className="tlrow">
                <span className="tllab">💾 Dự án</span>
                <div className="segchips">
                  <input className="lytext" style={{ maxWidth: 130 }} placeholder="tên dự án..."
                    value={projName} maxLength={48} onChange={(e) => setProjName(e.target.value)} />
                  <button className="btn sm" disabled={!projName.trim() || layers.length === 0}
                    onClick={async () => {
                      try {
                        await saveProject(projName.trim(), selected.name, {
                          layers: layers.map(({ id, ...rest }) => rest),
                          grade, shTint, hlTint,
                        });
                        setProjList(await listProjects());
                        showToast("💾 Đã lưu dự án " + projName.trim());
                      } catch (e) { showToast("❌ " + String((e as Error).message)); }
                    }}>Lưu</button>
                  <select value={projPick} onChange={(e) => setProjPick(e.target.value)}
                    onFocus={async () => setProjList(await listProjects())}>
                    <option value="">— dự án đã lưu —</option>
                    {projList.map((pr) => (
                      <option key={pr.name} value={pr.name}>{pr.name} ({pr.layers} lớp · {pr.file})</option>
                    ))}
                  </select>
                  <button className="btn sm" disabled={!projPick} onClick={async () => {
                    try {
                      const pj = await loadProject(projPick);
                      const d = pj.data as { layers?: Omit<Layer, "id">[]; grade?: Record<string, number>; shTint?: { h: number; s: number }; hlTint?: { h: number; s: number } };
                      if (pj.file && pj.file !== selected.name) {
                        const item = media.find((m) => m.name === pj.file);
                        if (item) setSelected(item);
                        else showToast("⚠ Video gốc của dự án không còn — áp lên video đang chọn");
                      }
                      // đặt sau setSelected: setLayers ghi đè effect dọn lớp khi đổi video
                      setTimeout(() => {
                        setLayers((d.layers || []).map((x) => ({ ...x, id: layerIdRef.current++ })));
                        if (d.grade) setGrade({ ...GRADE_DEF, ...d.grade });
                        if (d.shTint) setShTint(d.shTint);
                        if (d.hlTint) setHlTint(d.hlTint);
                      }, 50);
                      showToast("📂 Đã mở dự án " + projPick);
                    } catch (e) { showToast("❌ " + String((e as Error).message)); }
                  }}>📂 Mở</button>
                  <button className="btn sm" disabled={!projPick} style={{ color: "var(--warn)" }}
                    onClick={async () => {
                      await deleteProject(projPick);
                      setProjPick(""); setProjList(await listProjects());
                      showToast("🗑 Đã xoá dự án");
                    }}>🗑</button>
                </div>
              </div>
            </div>
          )}
        </section>

        {/* ---------- RIGHT ---------- */}
        <aside className="right">
          <div className="rhead">
            <button className={"rtab" + (rightTab === "tool" ? " on" : "")} onClick={() => setRightTab("tool")}>Tuỳ chọn</button>
            <button className={"rtab" + (rightTab === "jobs" ? " on" : "")} onClick={() => setRightTab("jobs")}>
              Hàng đợi{running > 0 && <span className="cnt">{running}</span>}
            </button>
          </div>

          {rightTab === "tool" && (
            <div className="rbody">
              <div className="rsec-t"><span className="rsec-ic">{TOOLS.find((x) => x.key === tool)?.icon}</span> {TOOL_NAMES[tool]} — {
                tool === "tts" ? "không cần tệp nguồn"
                : batch && batchSel.length > 0 ? `${batchSel.length} tệp (hàng loạt)`
                : selected ? selected.name : "chưa chọn tệp"}</div>

              {tool === "transcribe" && (
                <>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small", "medium", "large-v3-turbo"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.whisper_mlx && (
                    <div className="field"><label>Engine</label>
                      <div className="seg">
                        <button className={whEngine === "" ? "on" : ""} onClick={() => setWhEngine("")}>Tự chọn</button>
                        <button className={whEngine === "mlx" ? "on" : ""} onClick={() => setWhEngine("mlx")}>⚡ Metal (MLX)</button>
                        <button className={whEngine === "faster" ? "on" : ""} onClick={() => setWhEngine("faster")}>CPU int8</button>
                      </div>
                    </div>
                  )}
                  <div className="field"><label>Ngôn ngữ</label>
                    <div className="seg">
                      <button className={whLang === "" ? "on" : ""} onClick={() => setWhLang("")}>Tự nhận</button>
                      <button className={whLang === "vi" ? "on" : ""} onClick={() => setWhLang("vi")}>Việt</button>
                      <button className={whLang === "en" ? "on" : ""} onClick={() => setWhLang("en")}>English</button>
                    </div>
                  </div>
                  <div className="field"><label>Số từ mỗi lần hiện</label>
                    <div className="seg">
                      {[[0, "Câu"], [1, "1"], [2, "2"], [3, "3"], [5, "5"], [7, "7"]].map(([v, l]) => (
                        <button key={v} className={whMaxWords === v ? "on" : ""}
                          onClick={() => setWhMaxWords(v as number)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field"><label>Font chữ</label>
                    <select value={whFont} onChange={(e) => setWhFont(e.target.value)}>
                      {FONTS.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </div>
                  <div className="field"><label>Hiệu ứng</label>
                    <select value={whEffect} onChange={(e) => setWhEffect(e.target.value)}>
                      <option value="classic">Cổ điển (trắng viền đen)</option>
                      <option value="bold">Đậm nổi khối (MrBeast)</option>
                      <option value="yellow">Vàng nổi bật (Hormozi)</option>
                      <option value="karaoke">Karaoke — sáng từng từ</option>
                      <option value="neon">Neon phát sáng</option>
                      <option value="box">Hộp nền đen</option>
                    </select>
                  </div>
                  <div className={"subpreview fx-" + whEffect} style={{ fontFamily: `"${whFont}", sans-serif` }}>
                    {whEffect === "karaoke"
                      ? <>XIN <span className="hl">CHÀO</span> CÁC BẠN</>
                      : (whUpper ? "XIN CHÀO CÁC BẠN" : "Xin chào các bạn")}
                  </div>
                  <div className="field"><label>Cỡ chữ</label>
                    <div className="seg">
                      {["S", "M", "L", "XL"].map((s) => (
                        <button key={s} className={whSize === s ? "on" : ""} onClick={() => setWhSize(s)}>{s}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field"><label>Vị trí</label>
                    <div className="seg">
                      <button className={whPos === "bottom" ? "on" : ""} onClick={() => setWhPos("bottom")}>Dưới</button>
                      <button className={whPos === "center" ? "on" : ""} onClick={() => setWhPos("center")}>Giữa</button>
                      <button className={whPos === "top" ? "on" : ""} onClick={() => setWhPos("top")}>Trên</button>
                    </div>
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={whUpper} onChange={(e) => setWhUpper(e.target.checked)} />IN HOA</label>
                    <label className="chk"><input type="checkbox" checked={whBurn} onChange={(e) => setWhBurn(e.target.checked)} />Burn vào video</label>
                  </div>
                  {(feats.claude || feats.llm) && (
                    <label className="chk"><input type="checkbox" checked={spellfix} onChange={(e) => setSpellfix(e.target.checked)} />
                      ✨ AI soát chính tả sub (không áp dụng hiệu ứng karaoke)</label>
                  )}
                  <div className="hint">💡 Model <b>large-v3-turbo</b> chính xác nhất (chạy GPU Metal, chính tả tốt hơn hẳn base).</div>
                  <button className="btn pri big" onClick={() => run("transcribe", {
                    model: whModel, engine: whEngine || null, burn: whBurn, language: whLang || null,
                    max_words: whMaxWords, font: whFont, effect: whEffect,
                    size: whSize, position: whPos, uppercase: whUpper,
                    spellfix, ai: aiEngine,
                  })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "rife" && (
                <>
                  <div className="field"><label>Chế độ</label>
                    <div className="seg">
                      <button className={rifeMode === "smooth" ? "on" : ""} onClick={() => setRifeMode("smooth")}>Mượt ×2 fps</button>
                      <button className={rifeMode === "slowmo" ? "on" : ""} onClick={() => setRifeMode("slowmo")}>Slow-motion ×2</button>
                    </div>
                  </div>
                  <div className="hint">
                    {rifeMode === "smooth"
                      ? "Giữ nguyên tốc độ, nhân đôi số khung hình — 30fps thành 60fps, chuyển động mượt hẳn."
                      : "Giữ nguyên fps, video dài gấp đôi — quay chậm mềm mại; âm thanh tự giãn 0.5×."}
                  </div>
                  <br />
                  <button className="btn pri big" onClick={() => run("rife", { mode: rifeMode })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "bg_remove" && (
                <>
                  <div className="field"><label>Nền thay thế</label>
                    <div className="seg">
                      {[["green", "Xanh key"], ["black", "Đen"], ["white", "Trắng"], ["alpha", "Trong suốt"]].map(([v, l]) => (
                        <button key={v} className={bgMode === v ? "on" : ""} onClick={() => setBgMode(v)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <div className="hint">
                    {bgMode === "alpha"
                      ? "Xuất .webm kênh alpha (VP9) — kéo thẳng vào phần mềm dựng, nền trong suốt. Mã hoá chậm hơn mp4."
                      : "Nền xanh lá để key tiếp trong app dựng; đen/trắng dùng ngay. AI matting theo dõi cả tóc và biên mềm."}
                  </div>
                  <br />
                  <button className="btn pri big" onClick={() => run("bg_remove", { bg: bgMode })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "tts" && (
                <>
                  <div className="field"><label>Văn bản cần đọc · {ttsText.length}/5000</label>
                    <textarea className="ttsbox" rows={7} maxLength={5000} value={ttsText}
                      placeholder="Nhập nội dung, Piper sẽ đọc thành file âm thanh — 100% trên máy..."
                      onChange={(e) => setTtsText(e.target.value)} />
                  </div>
                  <div className="field"><label>Giọng đọc</label>
                    <div className="seg">
                      {(health?.piper_voices?.length ? health.piper_voices : ["vi", "en"]).map((v) => (
                        <button key={v} className={ttsVoice === v ? "on" : ""}
                          onClick={() => setTtsVoice(v)}>{VOICE_LABELS[v] || v}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Tốc độ đọc</span><b>{ttsSpeed.toFixed(1)}×</b></div>
                    <input type="range" min={0.6} max={1.5} step={0.1} value={ttsSpeed}
                      onChange={(e) => setTtsSpeed(parseFloat(e.target.value))} />
                  </div>
                  <button className="btn pri big" disabled={!ttsText.trim()}
                    onClick={() => run("tts", { text: ttsText, voice: ttsVoice, speed: ttsSpeed })}>
                    ▶ Tạo giọng đọc (.wav + .mp3)
                  </button>
                </>
              )}

              {tool === "silence_cut" && (
                <>
                  <div className="field">
                    <div className="sl-h"><span>Biên an toàn quanh tiếng nói</span><b>{margin.toFixed(1)}s</b></div>
                    <input type="range" min={0} max={1} step={0.1} value={margin}
                      onChange={(e) => setMargin(parseFloat(e.target.value))} />
                  </div>
                  <div className="hint">Tự phát hiện & cắt mọi khoảng lặng, giữ lại biên {margin.toFixed(1)}s quanh câu nói để không bị hụt tiếng.</div>
                  <br />
                  <button className="btn pri big" onClick={() => run("silence_cut", { margin })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "upscale" && (
                <>
                  <div className="field"><label>Chế độ</label>
                    <div className="seg">
                      <button className={upMode === "ai" ? "on" : ""} disabled={!feats.upscale_ai}
                        onClick={() => setUpMode("ai")}>AI (GPU)</button>
                      <button className={upMode === "fast" ? "on" : ""} onClick={() => setUpMode("fast")}>Nhanh (lanczos)</button>
                    </div>
                  </div>
                  <div className="field"><label>Tỷ lệ phóng</label>
                    <div className="seg">
                      {[2, 3, 4].map((s) => (
                        <button key={s} className={upScale === s ? "on" : ""} onClick={() => setUpScale(s)}>×{s}</button>
                      ))}
                    </div>
                  </div>
                  {upMode === "ai" && (
                    <div className="field"><label>Model</label>
                      <select value={upModel} onChange={(e) => setUpModel(e.target.value)}>
                        <option value="realesr-animevideov3">Video (nhanh)</option>
                        <option value="realesrgan-x4plus">Ảnh thực (×4, chậm)</option>
                      </select>
                    </div>
                  )}
                  <div className="hint">💡 AI mode hợp clip ngắn. Video dài: xếp hàng đợi rồi để máy chạy — 0 credit vì là GPU của bạn.</div>
                  <br />
                  <button className="btn pri big" onClick={() => run("upscale", { mode: upMode, scale: upScale, model: upModel })}>
                    ▶ Chạy trên máy
                  </button>
                </>
              )}

              {tool === "pipeline" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🔗 Xếp nhiều tính năng theo thứ tự — chạy nối tiếp trên 1 video (kết quả
                    mỗi bước tự làm đầu vào bước sau), gộp thành 1 việc. Không cần tải lên tải xuống.
                  </div>
                  <div className="lp-title">Thêm bước vào chuỗi</div>
                  <div className="pipepalette">
                    {PIPE_PALETTE.map((s) => (
                      <button key={s.type} className="pipeadd" onClick={() => addStep(s.type)}>+ {s.label}</button>
                    ))}
                  </div>
                  {pipe.length === 0 ? (
                    <div className="nores" style={{ marginTop: 10 }}>Chưa có bước nào. Bấm thêm ở trên.</div>
                  ) : (
                    <div className="pipelist">
                      {pipe.map((st, i) => {
                        const meta = PIPE_PALETTE.find((x) => x.type === st.type);
                        return (
                          <div key={i} className="pipestep">
                            <span className="pipenum">{i + 1}</span>
                            <div className="pipebody">
                              <div className="pipehead">
                                <b>{meta?.label || st.type}</b>
                                <div className="pipebtns">
                                  <button onClick={() => moveStep(i, -1)} disabled={i === 0}>↑</button>
                                  <button onClick={() => moveStep(i, 1)} disabled={i === pipe.length - 1}>↓</button>
                                  <button className="pipedel" onClick={() => setPipe((s) => s.filter((_, j) => j !== i))}>✕</button>
                                </div>
                              </div>
                              {st.type === "speed" && (
                                <div className="seg sm">{[0.5, 1.25, 1.5, 2, 3].map((f) => (
                                  <button key={f} className={st.params.factor === f ? "on" : ""} onClick={() => setStepParam(i, "factor", f)}>×{f}</button>))}</div>
                              )}
                              {st.type === "color" && (
                                <select value={String(st.params.filter)} onChange={(e) => setStepParam(i, "filter", e.target.value)}>
                                  {["vivid", "warm", "cool", "bw", "film", "sharp"].map((f) => <option key={f} value={f}>{f}</option>)}
                                </select>
                              )}
                              {st.type === "export" && (
                                // trong chuỗi chỉ cho preset video (gif/mp3 không phải video → gãy bước sau)
                                <select value={String(st.params.preset)} onChange={(e) => setStepParam(i, "preset", e.target.value)}>
                                  {["tiktok", "youtube", "square", "reels45"].map((f) => <option key={f} value={f}>{f}</option>)}
                                </select>
                              )}
                              {st.type === "reframe" && (
                                <div className="seg sm"><button className={st.params.mode === "blur" ? "on" : ""} onClick={() => setStepParam(i, "mode", "blur")}>nền mờ</button><button className={st.params.mode === "crop" ? "on" : ""} onClick={() => setStepParam(i, "mode", "crop")}>cắt giữa</button></div>
                              )}
                              {st.type === "upscale" && (
                                <div className="seg sm">{[2, 3, 4].map((s) => (<button key={s} className={st.params.scale === s ? "on" : ""} onClick={() => setStepParam(i, "scale", s)}>×{s}</button>))}</div>
                              )}
                              {st.type === "face_blur" && (
                                <div className="seg sm"><button className={st.params.mode === "blur" ? "on" : ""} onClick={() => setStepParam(i, "mode", "blur")}>mờ</button><button className={st.params.mode === "pixelate" ? "on" : ""} onClick={() => setStepParam(i, "mode", "pixelate")}>ô vuông</button></div>
                              )}
                              {st.type === "bg_remove" && (
                                <select value={String(st.params.bg)} onChange={(e) => setStepParam(i, "bg", e.target.value)}>
                                  {["green", "black", "white", "alpha"].map((f) => <option key={f} value={f}>{f}</option>)}
                                </select>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <button className="btn pri big" style={{ marginTop: 10 }} disabled={!selected || pipe.length === 0}
                    onClick={runPipeline}>🔗 Chạy chuỗi {pipe.length} bước</button>
                  {pipe.length > 0 && (
                    <button className="btn" style={{ marginTop: 6 }} onClick={() => setPipe([])}>Xoá hết</button>
                  )}
                </>
              )}

              {tool === "viral" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🔥 Thả video dọc → 1 chạm: tự nhận dạng lời nói, tách cụm, cháy phụ đề
                    preset "Viral Trắng Viền Đen" + chuẩn hoá âm thanh (-14 LUFS). Chạy offline
                    trên GPU, không watermark. Xuất mp4 + .ass + .srt.
                  </div>
                  <div className="field"><label>Model nhận dạng (word-level)</label>
                    <select value={vcModel} onChange={(e) => setVcModel(e.target.value)}>
                      <option value="large-v3-turbo">large-v3-turbo (nhanh, khuyên dùng)</option>
                      <option value="large-v3">large-v3 (chính xác nhất, chậm hơn)</option>
                      <option value="medium">medium</option>
                      <option value="small">small</option>
                      <option value="base">base (test nhanh)</option>
                    </select>
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={vcKeyword} onChange={(e) => setVcKeyword(e.target.checked)} />Tô sáng từ khoá (vàng)</label>
                    <label className="chk"><input type="checkbox" checked={vcKaraoke} onChange={(e) => setVcKaraoke(e.target.checked)} />Karaoke từng từ</label>
                    <div className="field" style={{ marginTop: 6 }}><label>Hiệu ứng chữ</label>
                      <div className="seg sm">
                        {[["classic", "Chuẩn"], ["pop", "Pop 💥"], ["box", "Hộp nền 🔲"]].map(([v, l]) => (
                          <button key={v} className={vcFx === v ? "on" : ""} onClick={() => setVcFx(v)}>{l}</button>
                        ))}
                      </div>
                    </div>
                  </div>
                  {vcKeyword && (
                    <div className="field"><label>Từ khoá (cách nhau dấu phẩy, để trống = AI tự chọn)</label>
                      <input className="ttsbox" style={{ height: 32, resize: "none" }}
                        value={vcKeywords} placeholder="vd: AI, TikTok, 30 triệu"
                        onChange={(e) => setVcKeywords(e.target.value)} />
                    </div>
                  )}
                  <div className="field">
                    <div className="sl-h"><span>Cỡ chữ (% chiều cao)</span><b>{vcFont.toFixed(1)}%</b></div>
                    <input type="range" min={3} max={7} step={0.1} value={vcFont}
                      onChange={(e) => setVcFont(parseFloat(e.target.value))} />
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Độ dày viền (% chiều rộng)</span><b>{vcOutline.toFixed(1)}%</b></div>
                    <input type="range" min={0.2} max={1.5} step={0.1} value={vcOutline}
                      onChange={(e) => setVcOutline(parseFloat(e.target.value))} />
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Chiều cao đặt chữ (% từ đáy)</span><b>{vcPos}%</b></div>
                    <input type="range" min={12} max={40} step={1} value={vcPos}
                      onChange={(e) => setVcPos(parseInt(e.target.value))} />
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI chọn từ khoá</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (offline)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude</button>
                      </div>
                    </div>
                  )}
                  {audioFiles.length > 0 && (
                    <div className="field"><label>Nhạc nền (tuỳ chọn — tự ducking khi có giọng)</label>
                      <select value={vcMusic} onChange={(e) => setVcMusic(e.target.value)}>
                        <option value="">— không —</option>
                        {audioFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                    </div>
                  )}
                  {imageFiles.length > 0 && (
                    <div className="field"><label>End card (ảnh cuối 3s, tuỳ chọn)</label>
                      <select value={vcEndcard} onChange={(e) => setVcEndcard(e.target.value)}>
                        <option value="">— không —</option>
                        {imageFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                    </div>
                  )}
                  <div className="optrow">
                    <button className="btn pri big" style={{ flex: 1 }} disabled={vcBusy || !selected}
                      onClick={() => renderViral(false)}>🔥 1 chạm — Cháy luôn</button>
                    <button className="btn" disabled={vcBusy || !selected}
                      onClick={analyzeViral} title="Phân tích để sửa text từng cụm">
                      {vcBusy ? "⏳" : "✎ Sửa cụm"}
                    </button>
                  </div>
                  {vcClusters && (
                    <div className="vceditor">
                      <div className="lp-title">Sửa nội dung từng cụm ({vcClusters.length}) — bấm cụm để nhảy player</div>
                      {vcClusters.map((c, i) => (
                        <div key={i} className="vcrow">
                          <button className="vcts" onClick={() => { if (vidElRef.current) vidElRef.current.currentTime = c.start; }}>
                            {c.start.toFixed(1)}s
                          </button>
                          <input value={c.text}
                            onChange={(e) => setVcClusters((cl) => cl!.map((x, j) => j === i ? { ...x, text: e.target.value } : x))} />
                        </div>
                      ))}
                      <button className="btn pri big" onClick={() => renderViral(true)}>🔥 Cháy phụ đề (đã sửa)</button>
                    </div>
                  )}
                </>
              )}

              {tool === "director" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎬 Ra lệnh bằng lời — Claude (gói sub của bạn) đọc kho media, tự lập
                    kế hoạch và xếp job. Backend kiểm tra lại mọi lệnh trước khi chạy.
                    VD: <i>"cắt lặng video mới nhất, caption vàng, xuất tiktok rồi viết mô tả"</i>
                  </div>
                  <div className="dlog">
                    {dirLog.length === 0 && <div className="nores">Chưa có hội thoại nào.</div>}
                    {dirLog.map((m, i) => (
                      <div key={i} className={"dmsg " + m.role}>{m.text}</div>
                    ))}
                    {dirBusy && <div className="dmsg ai">⏳ Đạo diễn đang suy nghĩ...</div>}
                  </div>
                  <textarea className="ttsbox" rows={3} maxLength={2000} value={dirMsg}
                    placeholder="Gõ yêu cầu cho đạo diễn... (nhớ được hội thoại trước)"
                    onChange={(e) => setDirMsg(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendDirector(); }
                    }} />
                  <div className="optrow">
                    <button className="btn pri big" style={{ flex: 1 }} disabled={dirBusy || !dirMsg.trim()}
                      onClick={sendDirector}>
                      {dirBusy ? "⏳ Đang xử lý..." : "🎬 Gửi cho đạo diễn"}
                    </button>
                    {dirBusy && <button className="btn" title="Dừng ngay lệnh Claude đang chạy"
                      onClick={() => stopDirector()}>⏹ Dừng</button>}
                    <button className="btn" title="Xoá bộ nhớ hội thoại"
                      disabled={dirBusy || dirLog.length === 0}
                      onClick={async () => { await resetDirector(); setDirLog([]); }}>🗑 Quên</button>
                  </div>
                </>
              )}

              {tool === "lesson" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📚 AI nghe bài giảng và soạn giáo án hoàn chỉnh (mục tiêu, nội dung,
                    ghi chú, câu hỏi ôn tập kèm đáp án) — xuất .md, có thể đẩy thẳng vào
                    hệ đào tạo AI-LMS của bạn.
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Số câu hỏi ôn tập</span><b>{lsQuiz}</b></div>
                    <input type="range" min={3} max={15} step={1} value={lsQuiz}
                      onChange={(e) => setLsQuiz(parseInt(e.target.value))} />
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  {feats.ailms && (
                    <div className="optrow">
                      <label className="chk"><input type="checkbox" checked={lsPush} onChange={(e) => setLsPush(e.target.checked)} />
                        Đẩy thẳng vào AI-LMS làm bài học mới</label>
                    </div>
                  )}
                  {lsPush && feats.ailms && (
                    <>
                      <div className="field"><label>Cấp độ (level)</label>
                        <div className="seg">
                          {[1, 2, 3, 4].map((v) => (
                            <button key={v} className={lsLevel === v ? "on" : ""} onClick={() => setLsLevel(v)}>{v}</button>
                          ))}
                        </div>
                      </div>
                      {lsLevel === 3 && (
                        <div className="field"><label>Bộ phận (level 3)</label>
                          <select value={lsDept} onChange={(e) => setLsDept(e.target.value)}>
                            <option value="">— chung —</option>
                            {["Marketing", "Sales", "Kế toán - Tài chính", "Nhân sự",
                              "Vận hành", "Kỹ thuật - IT", "Ban lãnh đạo"].map((d) => (
                              <option key={d} value={d}>{d}</option>
                            ))}
                          </select>
                        </div>
                      )}
                    </>
                  )}
                  <button className="btn pri big" onClick={() => run("lesson", {
                    quiz: lsQuiz, model: whModel, ai: aiEngine,
                    push_lms: lsPush, level: lsLevel, department: lsLevel === 3 ? lsDept : null,
                  })}>📚 Soạn giáo án</button>
                </>
              )}

              {tool === "broll" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎞️ AI nghe video, tự chọn đoạn nên chèn cảnh minh hoạ rồi tải clip
                    stock hợp nội dung từ Pexels đè lên — giữ nguyên tiếng gốc. Hợp video
                    người nói (talking head).
                  </div>
                  {!feats.broll && (
                    <div className="hint" style={{ borderColor: "rgba(255,107,87,.4)", color: "var(--warn)" }}>
                      ⚠ Cần Pexels API key (miễn phí tại pexels.com/api). Tạo file
                      <code> backend/.env</code> với dòng <code>PEXELS_API_KEY=...</code> rồi
                      khởi động lại server.
                    </div>
                  )}
                  <div className="field">
                    <div className="sl-h"><span>Số đoạn B-roll</span><b>{brCount}</b></div>
                    <input type="range" min={1} max={6} step={1} value={brCount}
                      onChange={(e) => setBrCount(parseInt(e.target.value))} />
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI (chọn đoạn + từ khoá)</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <div className="hint">Từ khoá tìm cảnh do AI đặt bằng tiếng Anh (Pexels ra kết quả tốt hơn). Chỉ từ khoá được gửi ra ngoài — video gốc vẫn ở máy.</div>
                  <br />
                  <button className="btn pri big" disabled={!feats.broll}
                    onClick={() => run("broll", { count: brCount, model: whModel, ai: aiEngine })}>
                    🎞️ Ghép B-roll tự động
                  </button>
                </>
              )}

              {tool === "highlights" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎯 AI nghe toàn bộ video (bài giảng, podcast, vlog dài...), tự chọn
                    khoảnh khắc hay nhất rồi dựng thành shorts 9:16 kèm caption karaoke —
                    100% trên máy, không gửi dữ liệu đi đâu.
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Số shorts muốn tạo</span><b>{hlCount}</b></div>
                    <input type="range" min={1} max={6} step={1} value={hlCount}
                      onChange={(e) => setHlCount(parseInt(e.target.value))} />
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Độ dài mỗi short</span><b>{hlMin}–{hlMax}s</b></div>
                    <div className="seg">
                      {[[10, 30, "Ngắn 10-30s"], [15, 60, "Vừa 15-60s"], [30, 90, "Dài 30-90s"]].map(([a, b, l]) => (
                        <button key={l as string} className={hlMin === a && hlMax === b ? "on" : ""}
                          onClick={() => { setHlMin(a as number); setHlMax(b as number); }}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={hlMake} onChange={(e) => setHlMake(e.target.checked)} />
                      Dựng hoàn chỉnh (9:16 nền mờ + caption karaoke)</label>
                  </div>
                  <button className="btn pri big" onClick={() => run("highlights", {
                    count: hlCount, min_dur: hlMin, max_dur: hlMax, make_shorts: hlMake,
                    model: whModel, effect: whEffect, max_words: whMaxWords,
                    font: whFont, size: whSize, position: whPos, ai: aiEngine,
                  })}>🎯 AI cắt shorts</button>
                </>
              )}

              {tool === "face_blur" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🫥 AI phát hiện mọi khuôn mặt trong video và che tự động — hợp che mặt
                    học sinh, người qua đường trước khi đăng công khai.
                  </div>
                  <div className="field"><label>Kiểu che</label>
                    <div className="seg">
                      <button className={fbMode === "blur" ? "on" : ""} onClick={() => setFbMode("blur")}>Làm mờ (blur)</button>
                      <button className={fbMode === "pixelate" ? "on" : ""} onClick={() => setFbMode("pixelate")}>Ô vuông (pixel)</button>
                    </div>
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Độ mạnh</span><b>{fbStrength.toFixed(1)}×</b></div>
                    <input type="range" min={0.5} max={2} step={0.1} value={fbStrength}
                      onChange={(e) => setFbStrength(parseFloat(e.target.value))} />
                  </div>
                  <button className="btn pri big" onClick={() => run("face_blur", {
                    mode: fbMode, strength: fbStrength,
                  })}>🫥 Che mặt tự động</button>
                </>
              )}

              {tool === "content" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📝 AI nghe video rồi viết sẵn: 3 tiêu đề giật hook, câu mở đầu,
                    mô tả SEO, hashtags, chapters — xuất file .md tải về.
                  </div>
                  <div className="field"><label>Nền tảng</label>
                    <div className="seg">
                      <button className={ctPlatform === "youtube" ? "on" : ""} onClick={() => setCtPlatform("youtube")}>YouTube</button>
                      <button className={ctPlatform === "tiktok" ? "on" : ""} onClick={() => setCtPlatform("tiktok")}>TikTok/Shorts</button>
                    </div>
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("content", {
                    platform: ctPlatform, model: whModel, ai: aiEngine,
                  })}>📝 AI viết nội dung</button>
                </>
              )}

              {tool === "translate" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🌐 Whisper nghe lời thoại → AI dịch (giữ đúng mốc thời gian) → xuất
                    file .srt, tuỳ chọn cháy thẳng phụ đề dịch vào video. Video KHÔNG rời máy.
                  </div>
                  <div className="field"><label>Dịch sang</label>
                    <select value={trLang} onChange={(e) => setTrLang(e.target.value)}>
                      {TR_LANGS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small", "large-v3-turbo"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  <label className="chk"><input type="checkbox" checked={trBurn} onChange={(e) => setTrBurn(e.target.checked)} />Cháy phụ đề dịch vào video (.mp4)</label>
                  {feats.claude && (
                    <div className="field"><label>Não AI dịch</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("translate", {
                    target_lang: trLang, model: whModel, ai: aiEngine, burn: trBurn,
                  })}>🌐 Dịch phụ đề</button>
                </>
              )}

              {tool === "social_pack" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📢 1 video → AI sinh trọn bộ đăng: 5 tiêu đề, mô tả SEO, caption
                    TikTok, tweet thread, bài LinkedIn, đoạn cắt short & câu trích dẫn — file .md.
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("social_pack", {
                    model: whModel, ai: aiEngine,
                  })}>📢 Tái chế nội dung</button>
                </>
              )}

              {tool === "dub" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎙️ Nghe lời gốc → AI dịch → Piper đọc tiếng đích → khớp thời gian
                    từng câu, thay track âm thanh (giữ nguyên hình). Giọng Piper: Việt/Anh.
                  </div>
                  <div className="field"><label>Giọng đọc đích (vi→dịch tiếng Việt, en→English)</label>
                    <div className="seg">
                      {(health?.piper_voices?.length ? health.piper_voices : ["vi", "en"]).map((v) => (
                        <button key={v} className={dubVoice === v ? "on" : ""}
                          onClick={() => setDubVoice(v)}>{VOICE_LABELS[v] || v}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small", "large-v3-turbo"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI dịch</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("dub", {
                    voice: dubVoice, model: whModel, ai: aiEngine,
                  })}>🎙️ Lồng tiếng</button>
                </>
              )}

              {tool === "qc" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🔍 Claude soi transcript: gắn mốc chỗ có khoảng chết, câu ề à, lạc đề,
                    lặp ý → báo cáo .md kèm điểm. Bật "tự cắt" để xuất luôn bản đã gọt.
                  </div>
                  <label className="chk"><input type="checkbox" checked={qcAutocut} onChange={(e) => setQcAutocut(e.target.checked)} />Tự cắt bỏ đoạn lỗi → xuất video mới</label>
                  <div className="field" style={{ marginTop: 8 }}><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("qc", {
                    model: whModel, ai: aiEngine, autocut: qcAutocut,
                  })}>🔍 Soi & QC video</button>
                </>
              )}

              {tool === "script" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    ✍️ Claude nghe lời gốc rồi viết lại kịch bản cho gọn/cuốn, thêm hook
                    mở đầu, bản teleprompter dễ đọc khi quay & gợi ý chèn B-roll — file .md.
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("script", {
                    model: whModel, ai: aiEngine,
                  })}>✍️ Biên tập kịch bản</button>
                </>
              )}

              {tool === "thumbnail" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🖼️ Trích nhiều khung hình → Claude (thị giác) xem & chấm khung "giật
                    view" nhất làm ảnh bìa .jpg. Chỉ các khung hình rời máy, video thì không.
                  </div>
                  <div className="field"><label>Số khung để Claude chấm: {thumbCount}</label>
                    <input type="range" min={4} max={10} step={1} value={thumbCount}
                      onChange={(e) => setThumbCount(Number(e.target.value))} style={{ width: "100%" }} />
                  </div>
                  {!feats.thumbnail && <div className="hint">⚠ Cần bật Claude trong ⚙ Cài đặt (tính năng thị giác).</div>}
                  <button className="btn pri big" disabled={!feats.thumbnail} onClick={() => run("thumbnail", {
                    count: thumbCount,
                  })}>🖼️ Claude chọn thumbnail</button>
                </>
              )}

              {tool === "auto_edit" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    ✨ AI tự dựng trọn gói: cắt khoảng lặng → caption karaoke → xuất preset.
                    Bật "Chọn nhiều" ở kho media để chạy hàng loạt qua đêm.
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={autoCut} onChange={(e) => setAutoCut(e.target.checked)} />Cắt khoảng lặng</label>
                    <label className="chk"><input type="checkbox" checked={autoCaption} onChange={(e) => setAutoCaption(e.target.checked)} />Caption karaoke</label>
                  </div>
                  <div className="field"><label>Bước cuối</label>
                    <select value={autoPreset} onChange={(e) => setAutoPreset(e.target.value)}>
                      <option value="">Giữ khung gốc</option>
                      <option value="reframe916">Dọc 9:16 nền mờ (CapCut)</option>
                      <option value="tiktok">TikTok 9:16 viền đen</option>
                      <option value="youtube">YouTube 1080p</option>
                      <option value="square">Vuông 1:1</option>
                      <option value="reels45">Feed 4:5</option>
                    </select>
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  <br />
                  <button className="btn pri big" disabled={!autoCut && !autoCaption && !autoPreset}
                    onClick={() => run("auto_edit", {
                      cut: autoCut, caption: autoCaption, preset: autoPreset || null,
                      margin, model: whModel, effect: whEffect, max_words: whMaxWords,
                      font: whFont, size: whSize, position: whPos, uppercase: whUpper,
                    })}>✨ Dựng tự động</button>
                </>
              )}

              {tool === "reframe" && (
                <>
                  <div className="field"><label>Kiểu khung dọc</label>
                    <div className="seg">
                      <button className={reframeMode === "blur" ? "on" : ""} onClick={() => setReframeMode("blur")}>Nền mờ</button>
                      <button className={reframeMode === "crop" ? "on" : ""} onClick={() => setReframeMode("crop")}>Cắt giữa</button>
                    </div>
                  </div>
                  <div className="hint">
                    {reframeMode === "blur"
                      ? "Video nằm giữa, nền là chính nó phóng to làm mờ — đúng kiểu CapCut khi đăng video ngang lên TikTok."
                      : "Phóng to tràn khung 1080×1920 rồi cắt phần giữa — hợp cảnh quay chủ thể ở giữa."}
                  </div>
                  <br />
                  <button className="btn pri big" onClick={() => run("reframe", { mode: reframeMode })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "speed" && (
                <>
                  <div className="field"><label>Hệ số tốc độ</label>
                    <div className="seg">
                      {[0.5, 0.75, 1.25, 1.5, 2, 3].map((f) => (
                        <button key={f} className={speedFactor === f ? "on" : ""} onClick={() => setSpeedFactor(f)}>×{f}</button>
                      ))}
                    </div>
                  </div>
                  <div className="hint">Âm thanh đổi tốc độ nhưng giữ nguyên cao độ (không bị chipmunk).</div>
                  <br />
                  <button className="btn pri big" onClick={() => run("speed", { factor: speedFactor })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "color" && (
                <>
                  <div className="field"><label>Chọn filter</label>
                    <div className="fgrid">
                      {[["vivid", "🌈 Rực rỡ"], ["warm", "🌅 Ấm áp"], ["cool", "❄️ Lạnh"],
                        ["bw", "⬛ Đen trắng"], ["film", "🎞️ Film cổ điển"], ["sharp", "🔪 Nét căng"]].map(([v, l]) => (
                        <button key={v} className={"fcard" + (colorName === v ? " on" : "")} onClick={() => setColorName(v)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <br />
                  <button className="btn pri big" onClick={() => run("color", { filter: colorName })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "grade" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎛️ Bảng chỉnh màu PRO — kéo thanh trượt, video xem thử đổi màu NGAY
                    (gần đúng); bấm Render để ffmpeg xuất bản chuẩn.
                  </div>
                  {([
                    ["exposure", "Phơi sáng", -3, 3, 0.1],
                    ["brightness", "Độ sáng", -1, 1, 0.05],
                    ["contrast", "Tương phản", 0, 3, 0.05],
                    ["saturation", "Bão hoà màu", 0, 3, 0.05],
                    ["temperature", "Nhiệt độ (ấm ↔ lạnh)", -100, 100, 5],
                    ["hue", "Tông màu (Hue)", -180, 180, 5],
                    ["vibrance", "Rực màu (Vibrance)", -2, 2, 0.1],
                    ["gamma", "Gamma", 0.3, 3, 0.05],
                    ["sharp", "Độ nét", 0, 5, 0.1],
                    ["zoom", "Zoom (crop giữa)", 1, 2, 0.05],
                  ] as [string, string, number, number, number][]).map(([k, label, mn, mx, st]) => (
                    <div className="field gr" key={k}>
                      <div className="sl-h"><span>{label}</span><b>{grade[k]}</b></div>
                      <input type="range" min={mn} max={mx} step={st} value={grade[k]}
                        onChange={(e) => setGrade((g) => ({ ...g, [k]: parseFloat(e.target.value) }))} />
                    </div>
                  ))}
                  <div className="field"><label>Bánh xe nhuộm màu (kéo trong vòng tròn)</label>
                    <div className="cwheels">
                      <ColorWheel label="Nhuộm Tối" hue={shTint.h} sat={shTint.s}
                        onChange={(h, s) => setShTint({ h, s })} />
                      <ColorWheel label="Nhuộm Sáng" hue={hlTint.h} sat={hlTint.s}
                        onChange={(h, s) => setHlTint({ h, s })} />
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="btn" onClick={() => { setGrade({ ...GRADE_DEF }); setShTint({ h: 0, s: 0 }); setHlTint({ h: 0, s: 0 }); }}>↺ Đặt lại</button>
                    <button className="btn pri big" style={{ flex: 1 }}
                      onClick={() => run("grade", {
                        ...grade, sh_hue: shTint.h, sh_sat: shTint.s,
                        hl_hue: hlTint.h, hl_sat: hlTint.s,
                      })}>🎛️ Render bản chỉnh màu</button>
                  </div>
                  <div className="hint">Xem thử CSS chưa gồm nhuộm Tối/Sáng — bản render ffmpeg mới có.</div>
                </>
              )}

              {tool === "autoframe" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📱 Auto Reframe kiểu CapCut Pro: AI dò khuôn mặt từng khung hình,
                    khung dọc <b>lia mượt theo người nói</b> — không phải crop giữa cứng.
                  </div>
                  <div className="field"><label>Khung đích</label>
                    <div className="seg">
                      {[["916", "9:16 TikTok"], ["45", "4:5 Feed"], ["11", "1:1 Vuông"]].map(([v, l]) => (
                        <button key={v} className={afRatio === v ? "on" : ""} onClick={() => setAfRatio(v)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("autoframe", { ratio: afRatio })}>
                    📱 Auto reframe bám chủ thể
                  </button>
                </>
              )}

              {tool === "filler_cut" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🧹 Audio Cleanup kiểu CapCut: AI nghe từng từ → tự cắt sạch
                    "ừm", "à", "ờ", "uh", "um"... và nối mượt phần còn lại.
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small", "large-v3-turbo"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("filler_cut", { model: whModel })}>
                    🧹 Cắt sạch từ đệm
                  </button>
                </>
              )}

              {tool === "enhance" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    ✨ Đẹp màu 1 chạm: tự cân bằng trắng + màu sống động (vibrance) +
                    tăng nét nhẹ — như nút "Enhance" của CapCut.
                  </div>
                  <div className="field"><label>Phong cách</label>
                    <div className="seg">
                      <button className={enhMode === "natural" ? "on" : ""} onClick={() => setEnhMode("natural")}>🌿 Tự nhiên</button>
                      <button className={enhMode === "vivid" ? "on" : ""} onClick={() => setEnhMode("vivid")}>🌈 Rực rỡ</button>
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("enhance", { mode: enhMode })}>
                    ✨ Đẹp màu 1 chạm
                  </button>
                </>
              )}

              {tool === "retouch" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    💆 Retouch kiểu CapCut: AI dò mặt → làm mịn da CHỈ vùng mặt
                    (mắt/môi/nền vẫn nét), bám mặt chống nhấp nháy.
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Độ mịn</span><b>{rtStr.toFixed(1)}</b></div>
                    <input type="range" min={0.3} max={2} step={0.1} value={rtStr}
                      onChange={(e) => setRtStr(parseFloat(e.target.value))} />
                  </div>
                  <button className="btn pri big" onClick={() => run("retouch", { strength: rtStr })}>
                    💆 Làm mịn da
                  </button>
                </>
              )}

              {tool === "voicefx" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎭 Đổi giọng kiểu CapCut — áp cho video hoặc file audio, giữ nguyên hình.
                  </div>
                  <div className="field"><label>Hiệu ứng giọng</label>
                    <div className="fgrid">
                      {[["chipmunk", "🐿 Sóc chuột"], ["deep", "🐻 Trầm ấm"], ["robot", "🤖 Robot"],
                        ["phone", "📞 Điện thoại"], ["echo", "🎤 Vang sân khấu"], ["cave", "🕳 Hang động"]].map(([v, l]) => (
                        <button key={v} className={"fcard" + (vfxFx === v ? " on" : "")} onClick={() => setVfxFx(v)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("voicefx", { effect: vfxFx })}>
                    🎭 Đổi giọng
                  </button>
                </>
              )}

              {tool === "autopilot" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🚀 <b>1 nút — máy tự làm hết:</b> dò video có lời thoại không → tự quyết
                    chuỗi: cắt lặng → cắt ừm/à → đẹp màu → chuẩn âm → khung dọc bám mặt
                    (không mặt thì nền mờ) → phụ đề viral. Ra thẳng bản đăng.
                  </div>
                  <div className="field"><label>Đích</label>
                    <div className="seg">
                      <button className={apTarget === "tiktok" ? "on" : ""} onClick={() => setApTarget("tiktok")}>📱 TikTok/Shorts 9:16</button>
                      <button className={apTarget === "youtube" ? "on" : ""} onClick={() => setApTarget("youtube")}>🖥 YouTube ngang</button>
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("autopilot", { target: apTarget })}>
                    🚀 Làm hết cho tôi
                  </button>
                </>
              )}

              {tool === "script_video" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎥 Script-to-Video kiểu CapCut 2026: gõ CHỦ ĐỀ hoặc dán kịch bản →
                    AI chia cảnh + viết lời bình → giọng đọc AI + B-roll Pexels từng cảnh
                    → video hoàn chỉnh từ con số 0.
                  </div>
                  <textarea className="ttsbox" rows={4} maxLength={4000} value={svText}
                    placeholder='VD: "5 thói quen buổi sáng của người thành công" — hoặc dán nguyên kịch bản...'
                    onChange={(e) => setSvText(e.target.value)} />
                  <div className="field"><label>Giọng đọc</label>
                    <div className="seg">
                      {(health?.piper_voices?.length ? health.piper_voices : ["vi"]).map((v) => (
                        <button key={v} className={svVoice === v ? "on" : ""}
                          onClick={() => setSvVoice(v)}>{VOICE_LABELS[v] || v}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Số cảnh</span><b>{svScenes}</b></div>
                    <input type="range" min={2} max={8} step={1} value={svScenes}
                      onChange={(e) => setSvScenes(Number(e.target.value))} />
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={svPortrait} onChange={(e) => setSvPortrait(e.target.checked)} />Khung dọc 9:16</label>
                    <label className="chk"><input type="checkbox" checked={svCaption} onChange={(e) => setSvCaption(e.target.checked)} />Phụ đề viral</label>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI viết kịch bản</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" disabled={!svText.trim()}
                    onClick={async () => {
                      try {
                        await createJob("script_video", "", {
                          text: svText.trim(), voice: svVoice, scenes: svScenes,
                          target: svPortrait ? "portrait" : "landscape",
                          caption: svCaption, ai: aiEngine,
                        });
                        setJobs(await getJobs()); setRightTab("jobs");
                        showToast("🎥 Đang dựng video từ kịch bản — AI viết + đọc + tải B-roll");
                      } catch (e) { showToast("❌ " + String((e as Error).message)); }
                    }}>🎥 Dựng video từ kịch bản</button>
                </>
              )}

              {tool === "post_pack" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📦 1 chạm ra trọn bộ sẵn sàng đăng: video + thumbnail (Claude Vision
                    chọn khung giật view) + tiêu đề/caption/hashtags + phụ đề .srt — đóng
                    gói 1 file .zip.
                  </div>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
                  <label className="chk"><input type="checkbox" checked={ppVideo} onChange={(e) => setPpVideo(e.target.checked)} />Kèm cả video vào .zip</label>
                  {feats.claude && (
                    <div className="field"><label>Não AI</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" onClick={() => run("post_pack", {
                    model: whModel, ai: aiEngine, include_video: ppVideo,
                  })}>📦 Làm gói đăng bài</button>
                </>
              )}

              {tool === "scene_split" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎬 Dò chuyển cảnh → tách video thành từng clip riêng + danh sách mốc
                    cảnh. Hợp với vlog/quay nhiều cảnh cần lọc lại.
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Độ nhạy (thấp = tách nhiều cảnh hơn)</span><b>{ssThr.toFixed(2)}</b></div>
                    <input type="range" min={0.15} max={0.7} step={0.05} value={ssThr}
                      onChange={(e) => setSsThr(parseFloat(e.target.value))} />
                  </div>
                  <button className="btn pri big" onClick={() => run("scene_split", { threshold: ssThr })}>
                    🎬 Tách cảnh
                  </button>
                </>
              )}

              {tool === "punchin" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🔍 Nhịp dựng faceless channel: AI nghe từng CÂU → zoom xen kẽ
                    1.0× / zoom-in theo câu — video nói chuyện hết nhàm chán.
                  </div>
                  <div className="field"><label>Mức zoom</label>
                    <div className="seg">
                      <button className={!piStrong ? "on" : ""} onClick={() => setPiStrong(false)}>Nhẹ (1.05×)</button>
                      <button className={piStrong ? "on" : ""} onClick={() => setPiStrong(true)}>Mạnh (1.1×)</button>
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("punchin", {
                    strength: piStrong ? "strong" : "normal", model: "tiny",
                  })}>🔍 Auto punch-in</button>
                </>
              )}

              {tool === "multi_translate" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🌏 Nghe 1 lần → dịch + cháy phụ đề NHIỀU ngôn ngữ trong 1 job —
                    mỗi ngôn ngữ 1 bản .mp4 + .srt.
                  </div>
                  <div className="field"><label>Ngôn ngữ đích (chọn nhiều, tối đa 6)</label>
                    <div className="segchips">
                      {TR_LANGS.map(([v, l]) => (
                        <button key={v}
                          className={"btn sm" + (mtLangs.includes(v) ? " pri" : "")}
                          onClick={() => setMtLangs((ls) => ls.includes(v)
                            ? ls.filter((x) => x !== v)
                            : ls.length < 6 ? [...ls, v] : ls)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  {feats.claude && (
                    <div className="field"><label>Não AI dịch</label>
                      <div className="seg">
                        <button className={aiEngine === "local" ? "on" : ""} onClick={() => setAiEngine("local")}>Local (Qwen)</button>
                        <button className={aiEngine === "claude" ? "on" : ""} onClick={() => setAiEngine("claude")}>✨ Claude (sub)</button>
                      </div>
                    </div>
                  )}
                  <button className="btn pri big" disabled={mtLangs.length === 0}
                    onClick={() => run("multi_translate", { langs: mtLangs, model: whModel, ai: aiEngine })}>
                    🌏 Dịch {mtLangs.length} ngôn ngữ
                  </button>
                </>
              )}

              {tool === "track" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎯 SAM 2 (Meta AI) chạy local: <b>bấm thẳng vào đối tượng trên video</b> bên
                    trái (vật, người, thú, xe...) → AI tự bám theo suốt video rồi áp hiệu ứng.
                  </div>
                  <div className="field"><label>Điểm đã chọn</label>
                    <div className="csrow">{trackPt
                      ? `✅ (${Math.round(trackPt.x * 100)}%, ${Math.round(trackPt.y * 100)}%) — bấm lại để đổi`
                      : "chưa có — bấm vào đối tượng trên video"}</div>
                  </div>
                  <div className="field"><label>Hiệu ứng lên đối tượng</label>
                    <div className="seg">
                      {[["blur", "🌫 Làm mờ"], ["pixelate", "🟪 Che ô"], ["spotlight", "🔦 Spotlight"], ["green", "🟩 Tách nền"]].map(([v, l]) => (
                        <button key={v} className={trackFx === v ? "on" : ""} onClick={() => setTrackFx(v)}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Cường độ (mờ/ô)</span><b>{trackStr.toFixed(1)}</b></div>
                    <input type="range" min={0.5} max={2} step={0.1} value={trackStr}
                      onChange={(e) => setTrackStr(parseFloat(e.target.value))} />
                  </div>
                  <div className="hint">Spotlight = tối mọi thứ trừ đối tượng · Tách nền = nền → xanh key
                    (ghép phông sau). Video dài xử lý lâu (AI chạy từng khung hình).</div>
                  <button className="btn pri big" disabled={!trackPt}
                    onClick={() => trackPt && run("track", { x: trackPt.x, y: trackPt.y, effect: trackFx, strength: trackStr })}>
                    🎯 Track & áp hiệu ứng
                  </button>
                </>
              )}

              {tool === "folder" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📁 Trỏ vào một THƯ MỤC trong ổ cứng máy chủ → chạy chuỗi bước cho
                    MỌI video/audio trong đó. Kết quả xuất vào thư mục con
                    <b> LocalStudio_Xuat</b> ngay cạnh file gốc (file gốc giữ nguyên).
                  </div>
                  <div className="field"><label>Đường dẫn thư mục (tuyệt đối)</label>
                    <div style={{ display: "flex", gap: 6 }}>
                      <input className="lytext" style={{ flex: 1, maxWidth: "none" }}
                        placeholder="/Users/ban/Movies/can-xu-ly" value={fdPath}
                        onChange={(e) => { setFdPath(e.target.value); setFdScan(null); }} />
                      <button className="btn" disabled={fdBusy || !fdPath.trim()} onClick={async () => {
                        setFdBusy(true);
                        try { setFdScan(await scanFolder(fdPath.trim(), fdRec)); }
                        catch (e) { setFdScan(null); showToast("❌ " + String((e as Error).message)); }
                        finally { setFdBusy(false); }
                      }}>{fdBusy ? "⏳" : "🔍 Quét"}</button>
                    </div>
                  </div>
                  <label className="chk"><input type="checkbox" checked={fdRec}
                    onChange={(e) => { setFdRec(e.target.checked); setFdScan(null); }} />Quét cả thư mục con</label>
                  {fdScan && (
                    <div className="hint" style={{ marginTop: 6 }}>
                      ✅ Tìm thấy <b>{fdScan.files.length}</b> file media{fdScan.truncated ? " (chạm trần 300)" : ""}:
                      {" "}{fdScan.files.slice(0, 8).map((f) => f.name).join(" · ")}
                      {fdScan.files.length > 8 ? ` …+${fdScan.files.length - 8}` : ""}
                    </div>
                  )}
                  <div className="field" style={{ marginTop: 8 }}><label>Chuỗi bước áp cho từng file (bấm để thêm)</label>
                    <div className="segchips">
                      {PIPE_PALETTE.map((s) => (
                        <button key={s.type} className="btn sm" onClick={() => addStep(s.type)}>{s.label}</button>
                      ))}
                    </div>
                  </div>
                  {pipe.length > 0 && (
                    <div className="segchips" style={{ marginBottom: 8 }}>
                      {pipe.map((st, i) => (
                        <span key={i} className="segchip">{i + 1}. {PIPE_PALETTE.find((x) => x.type === st.type)?.label || st.type}
                          <button onClick={() => setPipe((s) => s.filter((_, j) => j !== i))}>✕</button>
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="hint">⚙ Tham số từng bước chỉnh trong tool 🔗 Chuỗi tự động (dùng chung danh sách bước).
                    File lỗi tự bỏ qua — cuối job có báo cáo chi tiết.</div>
                  <button className="btn pri big" disabled={!fdScan || fdScan.files.length === 0 || pipe.length === 0}
                    onClick={async () => {
                      try {
                        await createJob("folder_batch", "", { path: fdPath.trim(), recursive: fdRec, steps: pipe });
                        setJobs(await getJobs()); setRightTab("jobs");
                        showToast(`📁 Đang xử lý ${fdScan!.files.length} file — kết quả vào LocalStudio_Xuat`);
                      } catch (e) { showToast("❌ " + String((e as Error).message)); }
                    }}>
                    📁 Chạy {fdScan ? fdScan.files.length : 0} file × {pipe.length} bước
                  </button>

                  <div className="field" style={{ marginTop: 14 }}><label>🧩 Template chuỗi (lưu/áp nhanh)</label>
                    <div className="segchips">
                      <input className="lytext" style={{ maxWidth: 120 }} placeholder="tên template"
                        value={tplName} maxLength={48} onChange={(e) => setTplName(e.target.value)} />
                      <button className="btn sm" disabled={!tplName.trim() || pipe.length === 0}
                        onClick={async () => {
                          try {
                            await saveTemplate(tplName.trim(), pipe);
                            setTplList(await listTemplates()); showToast("🧩 Đã lưu template");
                          } catch (e) { showToast("❌ " + String((e as Error).message)); }
                        }}>Lưu chuỗi hiện tại</button>
                      <select value={tplPick} onChange={(e) => setTplPick(e.target.value)}
                        onFocus={async () => setTplList(await listTemplates())}>
                        <option value="">— template —</option>
                        {tplList.map((t) => <option key={t.name} value={t.name}>{t.name} ({t.label})</option>)}
                      </select>
                      <button className="btn sm" disabled={!tplPick} onClick={() => {
                        const t = tplList.find((x) => x.name === tplPick);
                        if (t) { setPipe(t.steps.map((s) => ({ type: s.type, params: { ...s.params } }))); showToast("🧩 Đã áp template " + t.name); }
                      }}>Áp</button>
                      <button className="btn sm" disabled={!tplPick} style={{ color: "var(--warn)" }}
                        onClick={async () => { await deleteTemplate(tplPick); setTplPick(""); setTplList(await listTemplates()); }}>🗑</button>
                    </div>
                  </div>

                  <div className="field" style={{ marginTop: 8 }}>
                    <label>🔥 Thư mục nóng — thả video vào là TỰ xử lý {watchCfg?.enabled
                      ? <b style={{ color: "var(--ice)" }}> · ĐANG BẬT ({watchCfg.processed} file đã xử lý)</b>
                      : " · đang tắt"}</label>
                    <div className="segchips">
                      <button className={"btn sm" + (watchCfg?.enabled ? "" : " pri")}
                        disabled={!watchCfg?.enabled && (!fdPath.trim() || pipe.length === 0)}
                        onClick={async () => {
                          try {
                            const w = await setWatch(watchCfg?.enabled
                              ? { enabled: false }
                              : { enabled: true, path: fdPath.trim(), recursive: fdRec, steps: pipe });
                            setWatchCfg(w);
                            showToast(w.enabled ? "🔥 Thư mục nóng ĐANG CANH " + w.path : "Đã tắt thư mục nóng");
                          } catch (e) { showToast("❌ " + String((e as Error).message)); }
                        }}>
                        {watchCfg?.enabled ? "⏹ Tắt" : "🔥 Bật với đường dẫn + chuỗi hiện tại"}
                      </button>
                      {watchCfg?.enabled && <span className="tllab">{watchCfg.path}</span>}
                    </div>
                  </div>

                  <div className="field" style={{ marginTop: 4 }}>
                    <label>⏰ Lịch chạy đêm — tự chạy cả thư mục mỗi ngày lúc:</label>
                    <div className="segchips">
                      <input className="lytext" style={{ maxWidth: 80 }} type="time"
                        value={watchCfg?.schedule_time || "02:00"}
                        onChange={async (e) => setWatchCfg(await setWatch({ schedule_time: e.target.value }))} />
                      <button className={"btn sm" + (watchCfg?.schedule_enabled ? "" : " pri")}
                        disabled={!watchCfg?.schedule_enabled && (!fdPath.trim() || pipe.length === 0)}
                        onClick={async () => {
                          const w = await setWatch(watchCfg?.schedule_enabled
                            ? { schedule_enabled: false }
                            : { schedule_enabled: true, path: fdPath.trim(), recursive: fdRec, steps: pipe });
                          setWatchCfg(w);
                          showToast(w.schedule_enabled ? "⏰ Đã hẹn " + w.schedule_time + " mỗi đêm" : "Đã tắt lịch");
                        }}>
                        {watchCfg?.schedule_enabled ? "⏹ Tắt lịch" : "⏰ Bật lịch"}
                      </button>
                    </div>
                  </div>
                </>
              )}

              {tool === "clipsearch" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🔎 CLIP (OpenAI) chạy local: gõ mô tả cảnh — "người đứng bảng trắng",
                    "hoàng hôn", "con thằn lằn"... → tìm đúng khoảnh khắc trong CẢ kho video.
                    Tiếng Việt tự dịch sang EN cho AI hiểu.
                  </div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <input className="lytext" style={{ flex: 1, maxWidth: "none" }} value={clipQ}
                      placeholder="Mô tả cảnh cần tìm..." maxLength={300}
                      onChange={(e) => setClipQ(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && clipQ.trim() && !clipBusy) (document.getElementById("clipgo") as HTMLButtonElement)?.click(); }} />
                    <button id="clipgo" className="btn pri" disabled={clipBusy || !clipQ.trim()}
                      onClick={async () => {
                        setClipBusy(true);
                        try {
                          const r = await clipSearch(clipQ.trim());
                          setClipRes(r.results); setClipUnidx(r.unindexed);
                          if (!r.results.length && !r.unindexed.length) showToast("Không tìm thấy cảnh khớp");
                        } catch (e) { showToast("❌ " + String((e as Error).message)); }
                        finally { setClipBusy(false); }
                      }}>{clipBusy ? "⏳" : "🔎 Tìm"}</button>
                  </div>
                  {clipUnidx.length > 0 && (
                    <div className="hint" style={{ marginTop: 8 }}>
                      ⚠ {clipUnidx.length} video chưa lập chỉ mục →{" "}
                      <button className="btn sm pri" onClick={async () => {
                        try {
                          await createJob("clip_index", "", {});
                          setJobs(await getJobs()); setRightTab("jobs");
                          showToast("📚 Đang lập chỉ mục kho — xong thì tìm lại nhé");
                        } catch (e) { showToast("❌ " + String((e as Error).message)); }
                      }}>📚 Lập chỉ mục kho</button>
                    </div>
                  )}
                  <div className="cliphits">
                    {clipRes.map((r, i) => (
                      <button key={i} className="cliphit" onClick={() => {
                        const item = media.find((m) => m.name === r.file);
                        if (!item) { showToast("File không còn trong kho"); return; }
                        setPreview(null);
                        if (selected?.name === r.file && vidElRef.current) {
                          // video đang mở sẵn → tua thẳng, KHÔNG cắm seekRef (tránh
                          // video mở SAU đó bị nhảy mốc oan)
                          vidElRef.current.currentTime = r.t;
                        } else {
                          seekRef.current = r.t;
                          setSelected(item);
                        }
                        showToast(`▶ ${r.file} @ ${fmtDur(r.t)}`);
                      }}>
                        <img src={`/api/thumb/${encodeURIComponent(r.file)}`} alt="" loading="lazy" />
                        <span className="ch-t">{fmtDur(r.t)}</span>
                        <span className="ch-n">{r.file}</span>
                        <span className="ch-s">{Math.round(r.score * 100)}%</span>
                      </button>
                    ))}
                  </div>
                </>
              )}

              {tool === "music" && (
                <>
                  <div className="field"><label>Nhạc nền (tệp audio trong kho media)</label>
                    <select value={musicFile} onChange={(e) => setMusicFile(e.target.value)}>
                      <option value="">— chọn tệp nhạc —</option>
                      {audioFiles.map((m) => (
                        <option key={m.name} value={m.name}>{m.name}</option>
                      ))}
                    </select>
                  </div>
                  {audioFiles.length === 0 &&
                    <div className="hint">Chưa có tệp nhạc — kéo thả file .mp3/.wav vào kho media trước.</div>}
                  <div className="field">
                    <div className="sl-h"><span>Âm lượng nhạc</span><b>{Math.round(musicVol * 100)}%</b></div>
                    <input type="range" min={0.05} max={1} step={0.05} value={musicVol}
                      onChange={(e) => setMusicVol(parseFloat(e.target.value))} />
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={musicDuck} disabled={muteMusic} onChange={(e) => setMusicDuck(e.target.checked)} />
                      Ducking — tự nén nhạc khi có giọng nói</label>
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={muteMusic} onChange={(e) => setMuteMusic(e.target.checked)} />
                      🔇 Tắt tiếng video gốc — chỉ còn nhạc</label>
                  </div>
                  <button className="btn pri big" disabled={!musicFile}
                    onClick={() => run("music", { music: musicFile, volume: musicVol, duck: musicDuck, mute: muteMusic })}>
                    ▶ Trộn nhạc nền
                  </button>
                </>
              )}

              {tool === "stabilize" && (
                <>
                  <div className="hint">Giảm rung cho video quay tay bằng bộ lọc deshake — chạy nhanh, không cần GPU.</div>
                  <div className="field" style={{ marginTop: 8 }}><label>Mức chống rung</label>
                    <div className="seg">
                      <button className={!stabStrong ? "on" : ""} onClick={() => setStabStrong(false)}>Nhẹ (giữ nguyên khung)</button>
                      <button className={stabStrong ? "on" : ""} onClick={() => setStabStrong(true)}>Mạnh (crop 6% — rõ hơn hẳn)</button>
                    </div>
                  </div>
                  <button className="btn pri big" onClick={() => run("stabilize", { strength: stabStrong ? "strong" : "normal" })}>▶ Chạy trên máy</button>
                </>
              )}

              {tool === "merge" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🎬 Bật <b>"Chọn nhiều"</b> ở kho media và tick các clip theo thứ tự muốn ghép
                    (đang chọn: {batch ? batchSel.length : 0}/8 clip tối đa).
                  </div>
                  <div className="field"><label>Hiệu ứng chuyển cảnh</label>
                    <select value={mgTrans} onChange={(e) => setMgTrans(e.target.value)}>
                      <option value="fade">Fade (mờ dần)</option>
                      <option value="dissolve">Dissolve (tan hạt)</option>
                      <option value="wipeleft">Wipe trái</option>
                      <option value="wiperight">Wipe phải</option>
                      <option value="slideleft">Slide trái</option>
                      <option value="slideright">Slide phải</option>
                      <option value="circleopen">Mở tròn</option>
                      <option value="circleclose">Đóng tròn</option>
                      <option value="pixelize">Pixel hoá</option>
                      <option value="radial">Xoay radial</option>
                    </select>
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Độ dài chuyển cảnh</span><b>{mgDur.toFixed(1)}s</b></div>
                    <input type="range" min={0.2} max={1.5} step={0.1} value={mgDur}
                      onChange={(e) => setMgDur(parseFloat(e.target.value))} />
                  </div>
                  <div className="field"><label>Khung hình</label>
                    <div className="seg">
                      <button className={mgTarget === "169" ? "on" : ""} onClick={() => setMgTarget("169")}>16:9</button>
                      <button className={mgTarget === "916" ? "on" : ""} onClick={() => setMgTarget("916")}>9:16</button>
                      <button className={mgTarget === "11" ? "on" : ""} onClick={() => setMgTarget("11")}>1:1</button>
                    </div>
                  </div>
                  <div className="field"><label>Nhạc nền (tuỳ chọn — thay âm gốc)</label>
                    <select value={mgMusic} onChange={(e) => setMgMusic(e.target.value)}>
                      <option value="">— giữ âm thanh gốc (acrossfade) —</option>
                      {audioFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                    </select>
                  </div>
                  <button className="btn pri big" disabled={!(batch && batchSel.length >= 2)}
                    onClick={() => runMulti("merge", {
                      transition: mgTrans, duration: mgDur, target: mgTarget,
                      music: mgMusic || null,
                    })}>🎬 Ghép {batch ? batchSel.length : 0} clip</button>
                </>
              )}

              {tool === "beatsync" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    🥁 AI dò nhịp bài nhạc rồi cắt video đúng nhịp — chọn 1 clip hoặc bật
                    "Chọn nhiều" để xoay vòng nhiều clip (đang chọn: {batch ? batchSel.length : (selected ? 1 : 0)}).
                  </div>
                  <div className="field"><label>Bài nhạc (bắt buộc)</label>
                    <select value={bsMusic} onChange={(e) => setBsMusic(e.target.value)}>
                      <option value="">— chọn nhạc trong kho media —</option>
                      {audioFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                    </select>
                  </div>
                  <div className="field"><label>Khung hình</label>
                    <div className="seg">
                      <button className={bsTarget === "916" ? "on" : ""} onClick={() => setBsTarget("916")}>9:16</button>
                      <button className={bsTarget === "169" ? "on" : ""} onClick={() => setBsTarget("169")}>16:9</button>
                      <button className={bsTarget === "11" ? "on" : ""} onClick={() => setBsTarget("11")}>1:1</button>
                    </div>
                  </div>
                  <div className="field">
                    <div className="sl-h"><span>Số đoạn tối đa</span><b>{bsMaxSeg}</b></div>
                    <input type="range" min={10} max={120} step={5} value={bsMaxSeg}
                      onChange={(e) => setBsMaxSeg(parseInt(e.target.value))} />
                  </div>
                  <button className="btn pri big" disabled={!bsMusic}
                    onClick={() => runMulti("beatsync", {
                      music: bsMusic, target: bsTarget, max_segments: bsMaxSeg,
                    })}>🥁 Cắt theo nhịp</button>
                </>
              )}

              {tool === "audio_enhance" && (
                <>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={aeDenoise} onChange={(e) => setAeDenoise(e.target.checked)} />Khử ồn nền (afftdn)</label>
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={aeLoudness} onChange={(e) => setAeLoudness(e.target.checked)} />Chuẩn hoá -16 LUFS (chuẩn TikTok/YouTube)</label>
                  </div>
                  <div className="field"><label>Mức xử lý</label>
                    <div className="seg">
                      <button className={!aeStrong ? "on" : ""} onClick={() => setAeStrong(false)}>Chuẩn (-16 LUFS)</button>
                      <button className={aeStrong ? "on" : ""} onClick={() => setAeStrong(true)}>Mạnh (-14 LUFS + nén)</button>
                    </div>
                  </div>
                  <div className="hint">Video → giữ nguyên hình, chỉ xử lý tiếng. File audio → xuất mp3 sạch.
                    Đo LUFS trước/sau hiện ở kết quả job. Chạy batch được.</div>
                  <br />
                  <button className="btn pri big" onClick={() => run("audio_enhance", {
                    denoise: aeDenoise, loudness: aeLoudness, strength: aeStrong ? "strong" : "normal",
                  })}>▶ Xử lý âm thanh</button>
                </>
              )}

              {tool === "brand" && (
                <>
                  <div className="field"><label>Tiêu đề mở đầu (hiện 3s, fade)</label>
                    <input className="ttsbox" style={{ height: 34, resize: "none" }} maxLength={120}
                      value={brTitle} placeholder="VD: 5 mẹo quay video bằng điện thoại"
                      onChange={(e) => setBrTitle(e.target.value)} />
                  </div>
                  <div className="field"><label>Chữ ký nhỏ góc dưới (suốt video)</label>
                    <input className="ttsbox" style={{ height: 34, resize: "none" }} maxLength={60}
                      value={brSign} placeholder="VD: @kenhcuakiem"
                      onChange={(e) => setBrSign(e.target.value)} />
                  </div>
                  <div className="field"><label>Logo watermark (ảnh png/jpg trong kho)</label>
                    <select value={brLogo} onChange={(e) => setBrLogo(e.target.value)}>
                      <option value="">— không dùng logo —</option>
                      {imageFiles.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                    </select>
                  </div>
                  {brLogo && (
                    <>
                      <div className="field"><label>Vị trí logo</label>
                        <div className="seg">
                          {[["tl", "↖"], ["tr", "↗"], ["bl", "↙"], ["br", "↘"]].map(([v, l]) => (
                            <button key={v} className={brCorner === v ? "on" : ""} onClick={() => setBrCorner(v)}>{l}</button>
                          ))}
                        </div>
                      </div>
                      <div className="field">
                        <div className="sl-h"><span>Độ đậm logo</span><b>{Math.round(brOpacity * 100)}%</b></div>
                        <input type="range" min={0.1} max={1} step={0.05} value={brOpacity}
                          onChange={(e) => setBrOpacity(parseFloat(e.target.value))} />
                      </div>
                    </>
                  )}
                  <button className="btn pri big" disabled={!brTitle && !brSign && !brLogo}
                    onClick={() => run("brand", {
                      title: brTitle, sign: brSign, logo: brLogo || null,
                      corner: brCorner, opacity: brOpacity,
                    })}>🏷️ Đóng dấu video</button>
                </>
              )}

              {tool === "audiogram" && (
                <>
                  <div className="hint" style={{ marginBottom: 10 }}>
                    📻 Biến file audio (hoặc tiếng của video) thành video sóng nhạc màu thương hiệu —
                    hợp với TTS, podcast, nhạc.
                  </div>
                  <div className="field"><label>Khung hình</label>
                    <div className="seg">
                      <button className={agTarget === "11" ? "on" : ""} onClick={() => setAgTarget("11")}>1:1</button>
                      <button className={agTarget === "916" ? "on" : ""} onClick={() => setAgTarget("916")}>9:16</button>
                      <button className={agTarget === "169" ? "on" : ""} onClick={() => setAgTarget("169")}>16:9</button>
                    </div>
                  </div>
                  <div className="field"><label>Tiêu đề trên video (tuỳ chọn)</label>
                    <input className="ttsbox" style={{ height: 34, resize: "none" }} maxLength={80}
                      value={agTitle} placeholder="VD: Podcast tập 12 — Học AI từ số 0"
                      onChange={(e) => setAgTitle(e.target.value)} />
                  </div>
                  <button className="btn pri big" onClick={() => run("audiogram", {
                    target: agTarget, title: agTitle,
                  })}>📻 Tạo audiogram</button>
                </>
              )}

              {tool === "export" && (
                <>
                  <div className="pcards">
                    {[["tiktok", "TikTok/Reels", "1080×1920", 18, 30],
                      ["youtube", "YouTube", "1920×1080", 40, 22],
                      ["square", "Instagram", "1080×1080", 26, 26],
                      ["reels45", "Feed 4:5", "1080×1350", 24, 30],
                      ["gif", "GIF 480p", "chia sẻ nhanh", 30, 17],
                      ["mp3", "MP3 320k", "chỉ âm thanh", 30, 10]].map(([k, n, m, w, h]) => (
                      <button key={k as string} className={"pcard" + (preset === k ? " on" : "")}
                        onClick={() => setPreset(k as string)}>
                        <div className="pr"><i style={{ width: w as number, height: h as number }} /></div>
                        <div className="pn">{n}</div><div className="pm">{m}</div>
                      </button>
                    ))}
                  </div>
                  <div className="hint" style={{ margin: "10px 0" }}>
                    Không watermark · vĩnh viễn · mã hoá H.264 trên máy bạn · 0 credit
                  </div>
                  <button className="btn pri big" onClick={() => run("export", { preset })}>▶ Bắt đầu xuất</button>
                </>
              )}
            </div>
          )}

          {rightTab === "jobs" && (
            <div className="rbody">
              {jobs.map((j) => (
                <div key={j.id} className={"job " + j.status}>
                  <div className="jhead">
                    <span>{TOOL_NAMES[j.type] ?? j.type}</span>
                    <span className={"jstatus " + j.status}>
                      {j.status === "queued" && "CHỜ"}
                      {j.status === "running" && "ĐANG CHẠY"}
                      {j.status === "done" && "✓ XONG"}
                      {j.status === "error" && "✗ LỖI"}
                      {j.status === "cancelled" && "⊘ ĐÃ HỦY"}
                    </span>
                    {(j.status === "queued" || j.status === "running") && (
                      <button className="jcancel" title="Hủy việc này"
                        onClick={async () => { await cancelJob(j.id); setJobs(await getJobs()); }}>✕</button>
                    )}
                  </div>
                  <div className="jinput">{j.input}</div>
                  {j.status === "running" && (
                    <div className={"bar" + (j.progress < 0 ? " indet" : "")}>
                      <i style={j.progress >= 0 ? { width: `${j.progress}%` } : undefined} />
                    </div>
                  )}
                  <div className="jmsg">{j.status === "error" ? j.error : j.message}</div>
                  {j.outputs.length > 0 && (
                    <div className="jouts">
                      {j.outputs.map((o) => (
                        <div className="outrow" key={o.url}>
                          <a href={o.url} download className="out">⬇ {o.name}</a>
                          {o.name.endsWith(".mp4") && (
                            <button className="pvbtn" onClick={() => openPreview(j, o.url, o.name)}>▶ Xem trước</button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {jobs.length === 0 && <div className="nores">Chưa có việc nào.<br />Chọn video → chọn công cụ AI → Chạy.</div>}
            </div>
          )}
        </aside>
      </div>

      {/* ================= STATUS ================= */}
      <footer className="status">
        <span className={running > 0 ? "amb" : "ok"}>{running > 0 ? `▶ ĐANG XỬ LÝ (${running})` : "✓ SẴN SÀNG"}</span>
        <span className="sp" />
        {gpu && <span>GPU <b className="amb">{gpu.name.replace("NVIDIA GeForce ", "")}</b>{gpu.type === "apple"
          ? ` · ${Math.round(gpu.vram_total_mb / 1024)} GB hợp nhất`
          : ` · VRAM ${((gpu as any).vram_used_mb / 1024).toFixed(1)}/${Math.round(gpu.vram_total_mb / 1024)} GB`}</span>}
        <span className="sp" />
        <span>{media.length} tệp · {doneCount} việc xong</span>
        <span className="sp" />
        <span className="ok">0 byte đã rời máy</span>
        <span style={{ marginLeft: "auto" }}>xử lý cục bộ · không credit · không watermark</span>
      </footer>

      {toast && <div className="toasts"><div className="toast">{toast}</div></div>}
    </div>
  );
}
