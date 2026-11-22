use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::oneshot;

// ─── Engine process state ────────────────────────────────────────────────────

type PendingMap = Arc<Mutex<HashMap<String, oneshot::Sender<serde_json::Value>>>>;

pub struct EngineState {
    child: Option<Child>,
    stdin: Option<Arc<Mutex<ChildStdin>>>,
    pending: PendingMap,
    stderr_tail: Arc<Mutex<Vec<String>>>,
}

impl EngineState {
    pub fn new() -> Self {
        Self {
            child: None,
            stdin: None,
            pending: Arc::new(Mutex::new(HashMap::new())),
            stderr_tail: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

// ─── Spawn the Python engine and bridge stdout → Tauri events ───────────────

fn spawn_engine(app: &AppHandle) -> Result<(), String> {
    let python = std::env::var("GATHER_PYTHON").unwrap_or_else(|_| {
        if cfg!(target_os = "windows") { "python".into() } else { "python3".into() }
    });

    let mut child = Command::new(&python)
        .args(["-m", "gather.engine", "--fixture", "--no-scheduler"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn engine ({python}): {e}"))?;

    let stdin = child.stdin.take().ok_or("Failed to get engine stdin")?;
    let stdout = child.stdout.take().ok_or("Failed to get engine stdout")?;
    let stderr = child.stderr.take().ok_or("Failed to get engine stderr")?;

    let stdin_arc = Arc::new(Mutex::new(stdin));

    let state = app.state::<Mutex<EngineState>>();
    let mut guard = state.lock().unwrap();
    guard.stdin = Some(stdin_arc.clone());
    guard.child = Some(child);
    let pending = guard.pending.clone();
    let stderr_tail = guard.stderr_tail.clone();
    drop(guard);

    // stderr reader — keep last 40 lines
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[engine] {line}");
            let mut tail = stderr_tail.lock().unwrap();
            if tail.len() >= 40 { tail.remove(0); }
            tail.push(line);
        }
    });

    // stdout reader — resolve pending RPCs or emit push events
    let app_handle = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(text) if !text.is_empty() => {
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
                        if let Some(id) = parsed.get("id").and_then(|v| v.as_str()) {
                            // RPC response — wake the waiting engine_rpc call
                            if let Some(tx) = pending.lock().unwrap().remove(id) {
                                let _ = tx.send(parsed.clone());
                            }
                            let _ = app_handle.emit("engine:response", &parsed);
                        } else if let Some(event_name) = parsed.get("event").and_then(|e| e.as_str()) {
                            let _ = app_handle.emit(event_name, &parsed);
                        }
                    }
                }
                Err(_) => break,
                _ => {}
            }
        }
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

/// Generic async RPC: send a request to the engine and await the correlated response.
#[tauri::command]
async fn engine_rpc(
    method: String,
    params: Option<serde_json::Value>,
    state: tauri::State<'_, Mutex<EngineState>>,
) -> Result<serde_json::Value, String> {
    let id = format!(
        "r{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .subsec_nanos()
    );
    let request = serde_json::json!({
        "id": id,
        "method": method,
        "params": params.unwrap_or(serde_json::json!({}))
    });
    let (tx, rx) = oneshot::channel::<serde_json::Value>();
    {
        let guard = state.lock().unwrap();
        guard.pending.lock().unwrap().insert(id.clone(), tx);
        if let Some(stdin_arc) = &guard.stdin {
            let mut stdin = stdin_arc.lock().unwrap();
            stdin
                .write_all(format!("{request}\n").as_bytes())
                .map_err(|e| {
                    guard.pending.lock().unwrap().remove(&id);
                    format!("Write failed: {e}")
                })?;
        } else {
            guard.pending.lock().unwrap().remove(&id);
            return Err("Engine is not running".into());
        }
    }
    let response = rx.await.map_err(|_| "Engine closed before responding".to_string())?;
    if let Some(err) = response.get("error") {
        return Err(err.to_string());
    }
    Ok(response.get("result").cloned().unwrap_or(serde_json::Value::Null))
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

/// Last 40 stderr lines from the engine process.
#[tauri::command]
fn engine_diagnostics(state: tauri::State<'_, Mutex<EngineState>>) -> String {
    state.lock().unwrap().stderr_tail.lock().unwrap().join("\n")
}

// ─── Onboarding / connections stubs ─────────────────────────────────────────

#[tauri::command]
fn connections_connect(provider: String) -> Result<(), String> {
    Err(format!("connections_connect({provider}) not yet implemented"))
}

#[tauri::command]
fn onboarding_status() -> serde_json::Value {
    serde_json::json!({ "complete": false })
}

#[tauri::command]
fn onboarding_complete() -> Result<(), String> {
    Ok(())
}

// ─── App entry point ─────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Mutex::new(EngineState::new()))
        .invoke_handler(tauri::generate_handler![engine_send, engine_rpc, engine_running, engine_diagnostics, connections_connect, onboarding_status, onboarding_complete])
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
