// Local Studio desktop shell — mở cửa sổ trỏ vào backend FastAPI local.
// Tự khởi động backend (uvicorn trong venv) nếu cổng 8765 chưa mở,
// và tắt backend do chính nó sinh ra khi đóng app.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

const PORT: u16 = 8765;

fn port_open() -> bool {
    TcpStream::connect_timeout(&([127, 0, 0, 1], PORT).into(), Duration::from_millis(300)).is_ok()
}

fn project_root() -> PathBuf {
    if let Ok(dir) = std::env::var("LOCAL_STUDIO_DIR") {
        return PathBuf::from(dir);
    }
    let home = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).unwrap_or_default();
    PathBuf::from(home).join("local-studio-ai")
}

fn spawn_backend() -> Option<Child> {
    if port_open() {
        return None; // backend đã chạy sẵn (vd. qua start.sh)
    }
    let root = project_root();
    let py = if cfg!(windows) {
        root.join("backend/.venv/Scripts/python.exe")
    } else {
        root.join("backend/.venv/bin/python")
    };
    let child = Command::new(py)
        .args([
            "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1",
            "--port", &PORT.to_string(),
            "--app-dir",
        ])
        .arg(root.join("backend"))
        .spawn()
        .ok()?;
    for _ in 0..120 {
        if port_open() {
            break;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Some(child)
}

fn main() {
    let backend: Mutex<Option<Child>> = Mutex::new(spawn_backend());

    tauri::Builder::default()
        .build(tauri::generate_context!())
        .expect("không khởi tạo được Tauri")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(mut child) = backend.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}
