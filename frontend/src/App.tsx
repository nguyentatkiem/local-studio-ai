import { useCallback, useEffect, useRef, useState } from "react";
import {
  authStatus, createJob, fmtDur, fmtSize, getHealth, getJobs, getMedia,
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

type ToolKey = "transcribe" | "silence_cut" | "upscale" | "export";

const TOOLS: { key: ToolKey; icon: string; name: string; desc: string; gpu: boolean }[] = [
  { key: "transcribe", icon: "💬", name: "Caption tự động", desc: "whisper word-level · .srt/.ass", gpu: false },
  { key: "silence_cut", icon: "✂️", name: "Cắt khoảng lặng", desc: "auto-editor · jump-cut", gpu: false },
  { key: "upscale", icon: "🔍", name: "AI Upscale ×2/×4", desc: "Real-ESRGAN ncnn-vulkan", gpu: true },
  { key: "export", icon: "📤", name: "Xuất preset mạng xã hội", desc: "TikTok 9:16 · YouTube · 1:1", gpu: false },
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
    if (!selected) { showToast("⚠️ Hãy chọn một video trong kho media trước"); return; }
    try {
      await createJob(type, selected.name, params);
      setJobs(await getJobs());
      setRightTab("jobs");
      showToast(`🚀 "${TOOL_NAMES[type]}" đã vào hàng đợi — chạy trên máy bạn`);
    } catch (e) { showToast("❌ " + String(e)); }
  };

  const openPreview = (j: Job, url: string, name: string) => {
    const item = media.find((m) => m.name === j.input) ?? null;
    if (item) setSelected(item);
    setPreview({ input: j.input, url, name });
    setAb("sua");
  };

  const feats = health?.features ?? {};
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
        <span className="pname">MVP 0.2 — dựng &amp; xử lý AI trên máy</span>
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
              <div className="lp-title"><span>Trong dự án · {media.length} tệp</span></div>
              <div className={"dropzone" + (uploading ? " busy" : "")}
                onClick={() => fileRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files); }}>
                {uploading ? "Đang nhập tệp..." : <>⬆️ Kéo-thả video vào đây<br /><small>tệp không được sao chép đi đâu cả</small></>}
              </div>
              <input ref={fileRef} type="file" accept="video/*,audio/*" multiple hidden
                onChange={(e) => onFiles(e.target.files)} />
              <div className="mgrid">
                {media.map((m) => (
                  <div key={m.name}
                    className={"mitem" + (selected?.name === m.name ? " sel" : "")}
                    onClick={() => { setSelected(m); setPreview(null); }}>
                    <div className="th">
                      <img src={`/api/thumb/${encodeURIComponent(m.name)}`} loading="lazy" alt=""
                        onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                      <span className="dur">{fmtDur(m.info.duration)}</span>
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
                    <b>{((gpu as any).vram_used_mb / 1024).toFixed(1)} / {Math.round(gpu.vram_total_mb / 1024)} GB</b></div>
                  <div className="meter"><i style={{ width: `${vramPct}%` }} /></div>
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
              <div className="rsec-t">{TOOL_NAMES[tool]} — {selected ? selected.name : "chưa chọn tệp"}</div>

              {tool === "transcribe" && (
                <>
                  <div className="field"><label>Model Whisper</label>
                    <div className="seg">
                      {["tiny", "base", "small"].map((m) => (
                        <button key={m} className={whModel === m ? "on" : ""} onClick={() => setWhModel(m)}>{m}</button>
                      ))}
                    </div>
                  </div>
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
                    model: whModel, burn: whBurn, language: whLang || null,
                    max_words: whMaxWords, font: whFont, effect: whEffect,
                    size: whSize, position: whPos, uppercase: whUpper,
                  })}>▶ Chạy trên máy</button>
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

              {tool === "export" && (
                <>
                  <div className="pcards">
                    {[["tiktok", "TikTok/Reels", "1080×1920", 18, 30],
                      ["youtube", "YouTube", "1920×1080", 40, 22],
                      ["square", "Instagram", "1080×1080", 26, 26]].map(([k, n, m, w, h]) => (
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
                    </span>
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
        {gpu && <span>GPU <b className="amb">{gpu.name.replace("NVIDIA GeForce ", "")}</b> · VRAM {((gpu as any).vram_used_mb / 1024).toFixed(1)}/{Math.round(gpu.vram_total_mb / 1024)} GB</span>}
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
