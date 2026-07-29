export type MediaInfo = {
  duration: number;
  size: number;
  width: number | null;
  height: number | null;
  fps: number;
  has_audio: boolean;
  vcodec: string | null;
};

export type MediaItem = { name: string; url: string; info: MediaInfo };

export type JobOutput = { name: string; url: string; size: number };

export type Job = {
  id: string;
  type: string;
  input: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;
  message: string;
  outputs: JobOutput[];
  error: string | null;
  created: number;
};

export type Health = {
  status: string;
  version: string;
  workers?: number;
  gpu: { name: string; vram_total_mb: number; vram_used_mb?: number; type?: string } | null;
  features: Record<string, boolean>;
};

export async function authStatus(): Promise<{ auth_required: boolean; authenticated: boolean }> {
  const r = await fetch("/api/auth-status");
  return r.json();
}

export async function login(password: string): Promise<void> {
  const r = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: "Đăng nhập thất bại" }));
    throw new Error(e.detail);
  }
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" });
}

export async function getHealth(): Promise<Health> {
  const r = await fetch("/api/health");
  if (r.status === 401) throw new Error("401");
  return r.json();
}

export async function getMedia(): Promise<MediaItem[]> {
  const r = await fetch("/api/media");
  return r.json();
}

export async function getJobs(): Promise<Job[]> {
  const r = await fetch("/api/jobs");
  return r.json();
}

export async function uploadFile(file: File): Promise<MediaItem> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error("Upload thất bại");
  return r.json();
}

export async function createJob(
  type: string,
  file: string,
  params: Record<string, unknown>
): Promise<Job> {
  const r = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, file, params }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(e.detail || "Tạo job thất bại");
  }
  return r.json();
}

export async function askDirector(message: string): Promise<{
  reply: string; jobs: Job[]; errors: string[];
}> {
  const r = await fetch("/api/director", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(e.detail || "Đạo diễn không phản hồi");
  }
  return r.json();
}

export async function resetDirector(): Promise<void> {
  await fetch("/api/director/reset", { method: "POST" });
}

export type ViralCluster = {
  start: number; end: number; text: string;
  words: { t: string; s: number; e: number }[];
};

export async function viralAnalyze(file: string, model: string, engine: string): Promise<Job> {
  const r = await fetch("/api/viral/analyze", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file, model, language: "vi", engine }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(e.detail || "Phân tích thất bại");
  }
  return (await r.json()).job;
}

export async function fetchCuesJson(url: string): Promise<{ w: number; h: number; clusters: ViralCluster[] }> {
  const r = await fetch(url);
  return r.json();
}

export type ClaudeSettings = {
  enabled: boolean; model: string; cli_present: boolean;
  cli_path: string; model_aliases: string[];
};

export async function getClaudeSettings(): Promise<ClaudeSettings> {
  const r = await fetch("/api/settings/claude");
  if (!r.ok) throw new Error("Không đọc được cấu hình Claude");
  return r.json();
}

export async function saveClaudeSettings(
  patch: { enabled?: boolean; model?: string }
): Promise<ClaudeSettings> {
  const r = await fetch("/api/settings/claude", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error("Lưu cấu hình thất bại");
  return r.json();
}

export async function testClaude(): Promise<{ ok: boolean; model: string; ms: number; reply: string }> {
  const r = await fetch("/api/settings/claude/test", { method: "POST" });
  const d = await r.json().catch(() => ({ detail: r.statusText }));
  if (!r.ok) throw new Error(d.detail || "Test thất bại");
  return d;
}

export async function cancelJob(id: string): Promise<void> {
  await fetch(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

export async function openOutputs(): Promise<void> {
  await fetch("/api/open-outputs", { method: "POST" });
}

export function fmtSize(b: number): string {
  if (b > 1e9) return (b / 1e9).toFixed(2) + " GB";
  if (b > 1e6) return (b / 1e6).toFixed(1) + " MB";
  return (b / 1e3).toFixed(0) + " KB";
}

export function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}
