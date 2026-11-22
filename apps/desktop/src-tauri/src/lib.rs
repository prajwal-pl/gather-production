use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{AppHandle, Emitter, Manager};

// ─── Engine process state ────────────────────────────────────────────────────

pub struct EngineState {
    child: Option<Child>,
    stdin: Option<Arc<Mutex<ChildStdin>>>,
}

impl EngineState {
    pub fn new() -> Self {
        Self {
            child: None,
            stdin: None,
        }
    }
}

// ─── Spawn the Python engine and bridge stdout → Tauri events ───────────────

fn spawn_engine(app: &AppHandle) -> Result<(), String> {
    // Determine the python executable to use: prefer the one in the venv
    let python = if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    };

    let mut child = Command::new(python)
        .args(["-m", "gather.engine", "--fixture", "--no-scheduler"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit()) // stderr goes to Tauri's console / log
        .spawn()
        .map_err(|e| format!("Failed to spawn engine: {e}"))?;

    let stdin = child
        .stdin
        .take()
        .ok_or("Failed to get engine stdin")?;
    let stdout = child
        .stdout
        .take()
        .ok_or("Failed to get engine stdout")?;

    let stdin_arc = Arc::new(Mutex::new(stdin));

    // Store state in the app
    let state = app.state::<Mutex<EngineState>>();
    let mut guard = state.lock().unwrap();
    guard.stdin = Some(stdin_arc.clone());
    guard.child = Some(child);
    drop(guard);

    // Reader thread: forward each JSON line from engine stdout as a Tauri event
    let app_handle = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(text) => {
                    if text.is_empty() {
                        continue;
                    }
                    // Parse the JSON to find out if it is a push event or a
                    // response, then emit the right Tauri event name.
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                        if let Some(event_name) = parsed.get("event").and_then(|e| e.as_str()) {
                            // Push event from engine — emit by name
                            let _ = app_handle.emit(event_name, &parsed);
                        } else if parsed.get("id").is_some() {
                            // RPC response — the frontend is awaiting by request id
                            let _ = app_handle.emit("engine:response", &parsed);
                        }
                    }
                }
                Err(_) => break,
            }
        }
        // Engine exited — notify the frontend
        let _ = app_handle.emit("engine:stopped", ());
    });

    Ok(())
}

// ─── Tauri commands ──────────────────────────────────────────────────────────

/// Send a raw JSON-lines request to the engine stdin.
/// The frontend generates request ids and listens for engine:response events.
#[tauri::command]
fn engine_send(
    payload: String,
    state: tauri::State<'_, Mutex<EngineState>>,
) -> Result<(), String> {
    let guard = state.lock().unwrap();
    if let Some(stdin_arc) = &guard.stdin {
        let mut stdin = stdin_arc.lock().unwrap();
        let line = format!("{}\n", payload.trim());
        stdin
            .write_all(line.as_bytes())
            .map_err(|e| format!("Write to engine failed: {e}"))?;
    } else {
        return Err("Engine is not running".into());
    }
    Ok(())
}

/// Check whether the engine process is alive.
#[tauri::command]
fn engine_running(state: tauri::State<'_, Mutex<EngineState>>) -> bool {
    let mut guard = state.lock().unwrap();
    if let Some(child) = &mut guard.child {
        matches!(child.try_wait(), Ok(None))
    } else {
        false
    }
}

// ─── App entry point ─────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Mutex::new(EngineState::new()))
        .invoke_handler(tauri::generate_handler![engine_send, engine_running])
        .setup(|app| {
            // Spawn the engine when the Tauri app starts.
            // We log errors but don't crash — the UI shows "stopped" and can
            // inform the user to check that the Python package is installed.
            if let Err(e) = spawn_engine(app.handle()) {
                eprintln!("[gather-desktop] engine spawn failed: {e}");
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
