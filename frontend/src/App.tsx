import { useCallback, useEffect, useRef, useState } from "react";
import {
  authStatus, cancelJob, createJob, fmtDur, fmtSize, getHealth, getJobs, getMedia,
  login, logout, openOutputs, uploadFile,
  type Health, type Job, type MediaItem,
} from "./api";

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
  | "merge" | "beatsync" | "audio_enhance" | "brand" | "audiogram";

const TOOLS: { key: ToolKey; icon: string; name: string; desc: string; gpu: boolean }[] = [
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
  { key: "music", icon: "🎵", name: "Nhạc nền + ducking", desc: "tự nén nhạc khi có giọng nói", gpu: false },
  { key: "audio_enhance", icon: "🎚️", name: "Chuẩn hoá âm thanh", desc: "khử ồn + loudnorm -16 LUFS", gpu: false },
  { key: "brand", icon: "🏷️", name: "Tiêu đề & Logo", desc: "title mở đầu · chữ ký · watermark PNG", gpu: false },
  { key: "audiogram", icon: "📻", name: "Audiogram sóng nhạc", desc: "audio/TTS → video đăng MXH", gpu: false },
  { key: "stabilize", icon: "🧷", name: "Chống rung", desc: "deshake · video quay tay", gpu: false },
  { key: "export", icon: "📤", name: "Xuất preset mạng xã hội", desc: "TikTok · YouTube · 4:5 · GIF · MP3", gpu: false },
];
const TOOL_NAMES = Object.fromEntries(TOOLS.map((t) => [t.key, t.name]));

const FONTS = ["Arial", "Arial Black", "Impact", "Segoe UI", "Verdana", "Tahoma",
  "Georgia", "Times New Roman", "Comic Sans MS", "Consolas", "Bahnschrift"];

type Preview = { input: string; url: string; name: string };

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
        <span className="pname">v0.5 — dựng &amp; xử lý AI trên máy</span>
        <div className="spacer" />
        <div className={"offline" + (health ? "" : " err")}>
          <span className="d" /><span>{health ? "OFFLINE · FOOTAGE KHÔNG RỜI MÁY" : "MẤT KẾT NỐI BACKEND"}</span>
        </div>
        {gpu && <span className="gpuchip">⚡ {gpu.name.replace("NVIDIA GeForce ", "")} · {Math.round(gpu.vram_total_mb / 1024)}GB</span>}
        <button className="btn" onClick={() => openOutputs()}>📂 Kết quả</button>
        <button className="btn pri" onClick={() => { setTool("export"); setRightTab("tool"); setLeftTab("ai"); }}>
          Xuất bản
        </button>
        <button className="btn" title="Đăng xuất admin"
          onClick={async () => { await logout(); setAuthed(false); }}>🔒</button>
      </header>

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
                </div>
              )}
              <div className="lp-title">Bấm để mở tuỳ chọn · chạy trên máy</div>
              {TOOLS.map((t) => (
                <button key={t.key}
                  className={"aitool" + (tool === t.key ? " sel" : "") +
                    ((t.key === "transcribe" && !feats.transcribe) ||
                     (t.key === "silence_cut" && !feats.silence_cut) ||
                     (t.key === "upscale" && !feats.upscale_fast) ||
                     (t.key === "rife" && !feats.rife) ||
                     (t.key === "bg_remove" && !feats.bg_remove) ||
                     (t.key === "tts" && !feats.tts) ||
                     (t.key === "auto_edit" && !feats.auto_edit) ||
                     (t.key === "beatsync" && !feats.beatsync) ||
                     (["reframe", "speed", "color", "music", "stabilize",
                       "merge", "audio_enhance", "brand", "audiogram"].includes(t.key) && !feats.ffmpeg) ||
                     (t.key === "export" && !feats.export) ? " disabled" : "")}
                  onClick={() => { setTool(t.key); setRightTab("tool"); }}>
                  <span className="ai-ic">{t.icon}</span>
                  <span className="ai-t"><span className="ai-n">{t.name}</span><span className="ai-d">{t.desc}</span></span>
                  <span className={"ai-g" + (t.gpu ? "" : " cpu")}>{t.gpu ? "GPU" : "CPU"}</span>
                </button>
              ))}
              <div className="hint">💡 Job xếp hàng chạy nền — cứ thêm nhiều việc rồi để máy tự xử lý. 0 credit, không giới hạn.</div>
            </div>
          )}
        </section>

        {/* ---------- STAGE ---------- */}
        <section className="stagewrap">
          <div className="player">
            {stageSrc ? (
              <div className="canvas">
                {selected && !preview && (
                  <span className="reslab">{selected.info.width}×{selected.info.height} · {Math.round(selected.info.fps)}fps</span>
                )}
                {preview && (
                  <span className={"reslab" + (ab === "sua" ? " up" : "")}>
                    {ab === "sua" ? "BẢN SỬA" : "BẢN GỐC"}
                  </span>
                )}
                <video key={stageSrc} src={stageSrc} controls autoPlay={!!preview} />
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
            <span className="selchip">{running > 0 ? `▶ ${running} việc đang chạy nền` : "máy đang rảnh"}</span>
          </div>
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
              <div className="rsec-t">{TOOL_NAMES[tool]} — {
                tool === "tts" ? "không cần tệp nguồn"
                : batch && batchSel.length > 0 ? `${batchSel.length} tệp (hàng loạt)`
                : selected ? selected.name : "chưa chọn tệp"}</div>

              {tool === "transcribe" && (
                <>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small", "medium"].map((m) => (
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
                  <button className="btn pri big" onClick={() => run("transcribe", {
                    model: whModel, engine: whEngine || null, burn: whBurn, language: whLang || null,
                    max_words: whMaxWords, font: whFont, effect: whEffect,
                    size: whSize, position: whPos, uppercase: whUpper,
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
                      <button className={ttsVoice === "vi" ? "on" : ""} onClick={() => setTtsVoice("vi")}>🇻🇳 Tiếng Việt</button>
                      <button className={ttsVoice === "en" ? "on" : ""} onClick={() => setTtsVoice("en")}>🇺🇸 English</button>
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

              {tool === "music" && (
                <>
                  <div className="field"><label>Nhạc nền (tệp audio trong kho media)</label>
                    <select value={musicFile} onChange={(e) => setMusicFile(e.target.value)}>
                      <option value="">— chọn tệp nhạc —</option>
                      {media.filter((m) => !m.info.width).map((m) => (
                        <option key={m.name} value={m.name}>{m.name}</option>
                      ))}
                    </select>
                  </div>
                  {media.filter((m) => !m.info.width).length === 0 &&
                    <div className="hint">Chưa có tệp nhạc — kéo thả file .mp3/.wav vào kho media trước.</div>}
                  <div className="field">
                    <div className="sl-h"><span>Âm lượng nhạc</span><b>{Math.round(musicVol * 100)}%</b></div>
                    <input type="range" min={0.05} max={1} step={0.05} value={musicVol}
                      onChange={(e) => setMusicVol(parseFloat(e.target.value))} />
                  </div>
                  <div className="optrow">
                    <label className="chk"><input type="checkbox" checked={musicDuck} onChange={(e) => setMusicDuck(e.target.checked)} />
                      Ducking — tự nén nhạc khi có giọng nói</label>
                  </div>
                  <button className="btn pri big" disabled={!musicFile}
                    onClick={() => run("music", { music: musicFile, volume: musicVol, duck: musicDuck })}>
                    ▶ Trộn nhạc nền
                  </button>
                </>
              )}

              {tool === "stabilize" && (
                <>
                  <div className="hint">Giảm rung cho video quay tay bằng bộ lọc deshake — chạy nhanh, không cần GPU.</div>
                  <br />
                  <button className="btn pri big" onClick={() => run("stabilize", {})}>▶ Chạy trên máy</button>
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
                  <div className="hint">Video → giữ nguyên hình, chỉ xử lý tiếng. File audio → xuất mp3 sạch. Chạy batch được.</div>
                  <br />
                  <button className="btn pri big" onClick={() => run("audio_enhance", {
                    denoise: aeDenoise, loudness: aeLoudness,
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
