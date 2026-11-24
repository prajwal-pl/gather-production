use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::oneshot;

static RPC_COUNTER: AtomicU64 = AtomicU64::new(1);

// ─── Persistent flag store (survives WebView cache clears in dev) ─────────────

fn flags_path(app: &AppHandle) -> PathBuf {
    if let Some(path) = std::env::var_os("GATHER_DATA_DIR").filter(|path| !path.is_empty()) {
        return PathBuf::from(path).join("desktop-flags.json");
    }
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("flags.json")
}

fn read_flags(app: &AppHandle) -> serde_json::Value {
    let path = flags_path(app);
    fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(serde_json::json!({}))
}

fn write_flags(app: &AppHandle, flags: &serde_json::Value) -> Result<(), String> {
    let path = flags_path(app);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    let payload =
        serde_json::to_string_pretty(flags).map_err(|e| format!("cannot serialize flags: {e}"))?;
    // Write through a temp file so a crash mid-write cannot corrupt the store.
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, payload).map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
    fs::rename(&tmp, &path).map_err(|e| format!("cannot replace {}: {e}", path.display()))?;
    Ok(())
}

#[tauri::command]
fn get_flag(key: String, app: AppHandle) -> Option<String> {
    read_flags(&app)
        .get(&key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

#[tauri::command]
fn set_flag(key: String, value: String, app: AppHandle) -> Result<(), String> {
    let mut flags = read_flags(&app);
    flags[key] = serde_json::Value::String(value);
    write_flags(&app, &flags)
}

#[tauri::command]
fn delete_flag(key: String, app: AppHandle) -> Result<(), String> {
    let mut flags = read_flags(&app);
    if let Some(obj) = flags.as_object_mut() {
        obj.remove(&key);
    }
    write_flags(&app, &flags)
}

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
    let state = app.state::<Mutex<EngineState>>();
    let mut guard = state.lock().unwrap();
    if let Some(child) = guard.child.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Ok(());
        }
    }
    guard.child = None;
    guard.stdin = None;

    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let venv = repo.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let sidecar = std::env::current_exe().ok().map(|path| {
        path.with_file_name(if cfg!(windows) {
            "gather-engine.exe"
        } else {
            "gather-engine"
        })
    });
    let engine = std::env::var_os("GATHER_ENGINE")
        .map(PathBuf::from)
        .or_else(|| sidecar.filter(|path| !cfg!(debug_assertions) && path.is_file()));
    let program = engine.clone().unwrap_or_else(|| {
        std::env::var_os("GATHER_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if cfg!(debug_assertions) && venv.is_file() {
                    venv
                } else {
                    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
                }
            })
    });
    let mut command = Command::new(&program);
    if engine.is_none() {
        command.args(["-m", "gather.engine"]);
        if cfg!(debug_assertions) {
            command
                .current_dir(&repo)
                .env("PYTHONPATH", repo.join("src"));
        }
    }
    let mut args: Vec<String> = Vec::new();
    if std::env::var("GATHER_FIXTURE").as_deref() == Ok("1") {
        args.push("--fixture".to_string());
    }
    // Scheduler off by default in dev; set GATHER_SCHEDULER=1 to enable.
    if std::env::var("GATHER_SCHEDULER").as_deref() != Ok("1") {
        args.push("--no-scheduler".to_string());
    }
    if let Ok(data_dir) = std::env::var("GATHER_DATA_DIR") {
        args.push("--data-dir".to_string());
        args.push(data_dir);
    }
    if let Ok(secrets) = std::env::var("GATHER_SECRETS") {
        args.push("--secrets".to_string());
        args.push(secrets);
    } else if cfg!(debug_assertions) {
        args.extend(["--secrets".into(), "file".into()]);
    }

    let mut child = command
        .args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to start {}: {e}", program.display()))?;

    let stdin = child.stdin.take().ok_or("Failed to get engine stdin")?;
    let stdout = child.stdout.take().ok_or("Failed to get engine stdout")?;
    let stderr = child.stderr.take().ok_or("Failed to get engine stderr")?;

    let stdin_arc = Arc::new(Mutex::new(stdin));

    guard.pending = Arc::new(Mutex::new(HashMap::new()));
    guard.stderr_tail = Arc::new(Mutex::new(Vec::new()));
    guard.stdin = Some(stdin_arc);
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
            if tail.len() >= 40 {
                tail.remove(0);
            }
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
                        } else if let Some(event_name) =
                            parsed.get("event").and_then(|e| e.as_str())
                        {
                            let _ = app_handle.emit(&event_name.replace('.', ":"), &parsed);
                        }
                    }
                }
                Err(_) => break,
                _ => {}
            }
        }
        pending.lock().unwrap().clear();
        let _ = app_handle.emit("engine:stopped", ());
    });

    Ok(())
}

// ─── Tauri commands ──────────────────────────────────────────────────────────

/// Send a raw JSON-lines request to the engine stdin.
#[tauri::command]
fn engine_send(payload: String, state: tauri::State<'_, Mutex<EngineState>>) -> Result<(), String> {
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
///
/// The EngineState lock is dropped BEFORE awaiting the oneshot — holding it
/// across .await would deadlock because the stdout reader thread needs to lock
/// `pending` (inside EngineState) to deliver the response.
#[tauri::command]
async fn engine_rpc(
    method: String,
    params: Option<serde_json::Value>,
    state: tauri::State<'_, Mutex<EngineState>>,
) -> Result<serde_json::Value, String> {
    let id = format!("r{}", RPC_COUNTER.fetch_add(1, Ordering::Relaxed));
    let request = serde_json::json!({
        "id": id,
        "method": method,
        "params": params.unwrap_or(serde_json::json!({}))
    });
    let (tx, rx) = oneshot::channel::<serde_json::Value>();

    let (stdin_arc, pending) = {
        let guard = state.lock().unwrap();
        let stdin = guard.stdin.clone().ok_or("Engine is not running")?;
        let pending = guard.pending.clone();
        (stdin, pending)
        // guard dropped here
    };

    pending.lock().unwrap().insert(id.clone(), tx);

    {
        let mut stdin = stdin_arc.lock().unwrap();
        if let Err(e) = stdin.write_all(format!("{request}\n").as_bytes()) {
            pending.lock().unwrap().remove(&id);
            return Err(format!("Write failed: {e}"));
        }
    }

    let timeout = if matches!(method.as_str(), "health" | "onboarding.status") {
        5
    } else {
        300
    };
    let response = match tokio::time::timeout(std::time::Duration::from_secs(timeout), rx).await {
        Ok(Ok(response)) => response,
        Ok(Err(_)) => return Err("Engine closed before responding".into()),
        Err(_) => {
            pending.lock().unwrap().remove(&id);
            return Err("Engine response timed out. Refresh to check the operation result.".into());
        }
    };
    if let Some(err) = response.get("error") {
        let message = err
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or_else(|| err.as_str().unwrap_or("unknown error"));
        return Err(message.to_string());
    }
    Ok(response
        .get("result")
        .cloned()
        .unwrap_or(serde_json::Value::Null))
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

#[tauri::command]
fn engine_restart(app: AppHandle) -> Result<(), String> {
    spawn_engine(&app)
}

// ─── App entry point ─────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Mutex::new(EngineState::new()))
        .invoke_handler(tauri::generate_handler![
            engine_send,
            engine_rpc,
            engine_running,
            engine_restart,
            engine_diagnostics,
            get_flag,
            set_flag,
            delete_flag
        ])
        .setup(|app| {
            if let Err(e) = spawn_engine(app.handle()) {
                eprintln!("[gather-desktop] engine spawn failed: {e}");
                app.state::<Mutex<EngineState>>()
                    .lock()
                    .unwrap()
                    .stderr_tail
                    .lock()
                    .unwrap()
                    .push(e);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
