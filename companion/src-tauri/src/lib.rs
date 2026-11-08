use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicBool, AtomicU64, Ordering},
    thread,
    time::Duration,
};

use serde::{Deserialize, Serialize};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    webview::PageLoadEvent,
    Manager, PhysicalPosition, WebviewWindow,
};
use tauri_plugin_window_state::StateFlags;

static EXITING: AtomicBool = AtomicBool::new(false);
static WINDOW_READY: AtomicBool = AtomicBool::new(false);
static WALK_GENERATION: AtomicU64 = AtomicU64::new(0);
static WALK_RIGHT_FIRST: AtomicBool = AtomicBool::new(true);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct PixelPoint {
    x: i32,
    y: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct WindowWalkPlan {
    start: PixelPoint,
    target: PixelPoint,
    distance: u32,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct WindowWalkStart {
    started: bool,
    reason: String,
    direction: String,
    distance: u32,
    duration_ms: u64,
}

#[derive(Debug, Deserialize)]
#[serde(default)]
struct IrisConfig {
    websocket_port: u16,
    companion_loopback_port: u16,
    lan_access: bool,
    companion_always_on_top: bool,
    companion_scale: f64,
    companion_reduced_motion: bool,
    companion_show_controls: bool,
    companion_locomotion_enabled: bool,
    companion_quiet_mode: bool,
    companion_life: String,
}

impl Default for IrisConfig {
    fn default() -> Self {
        Self {
            websocket_port: 8765,
            companion_loopback_port: 8766,
            lan_access: false,
            companion_always_on_top: true,
            companion_scale: 1.0,
            companion_reduced_motion: false,
            companion_show_controls: true,
            companion_locomotion_enabled: false,
            companion_quiet_mode: false,
            companion_life: "calm".to_string(),
        }
    }
}

#[derive(Debug, Default, Deserialize)]
#[serde(default)]
struct CompanionRuntime {
    api_port: u16,
    iris_os_url: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CompanionPreferences {
    reduced_motion: bool,
    show_controls: bool,
    always_on_top: bool,
    scale: f64,
    locomotion_enabled: bool,
    quiet_mode: bool,
    life: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CompanionBootstrap {
    api_base: String,
    ws_url: String,
    token: String,
    preferences: CompanionPreferences,
}

fn copy_missing_profile_entries(source: &PathBuf, destination: &PathBuf) -> Result<(), String> {
    fs::create_dir_all(destination).map_err(|error| error.to_string())?;
    for item in fs::read_dir(source).map_err(|error| error.to_string())? {
        let item = item.map_err(|error| error.to_string())?;
        let source_path = item.path();
        let destination_path = destination.join(item.file_name());
        let kind = item.file_type().map_err(|error| error.to_string())?;
        if kind.is_dir() {
            copy_missing_profile_entries(&source_path, &destination_path)?;
        } else if kind.is_file() && !destination_path.exists() {
            fs::copy(&source_path, &destination_path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn resolve_explicit_iris_home(value: &str, home: &Path) -> Result<Option<PathBuf>, String> {
    let explicit = value.trim();
    if explicit.is_empty() {
        return Ok(None);
    }
    let iris = if explicit == "~" || explicit.starts_with("~/") || explicit.starts_with("~\\") {
        if explicit == "~" {
            home.to_path_buf()
        } else {
            home.join(&explicit[2..])
        }
    } else {
        PathBuf::from(explicit)
    };
    if !iris.is_absolute() {
        return Err("IRIS_HOME debe ser una ruta absoluta o comenzar por ~/".into());
    }
    Ok(Some(iris))
}

fn incomplete_migration_source(iris: &Path, home: &Path) -> Option<PathBuf> {
    let marker = iris.join(".legacy-migration-incomplete");
    let source_name = fs::read_to_string(marker).ok()?;
    let source = home.join(source_name.trim());
    (source.exists() && source != iris).then_some(source)
}

fn iris_home() -> Result<PathBuf, String> {
    let home =
        dirs::home_dir().ok_or_else(|| "No se encontró el directorio del usuario".to_string())?;
    if let Ok(explicit) = std::env::var("IRIS_HOME") {
        if let Some(iris) = resolve_explicit_iris_home(&explicit, &home)? {
            fs::create_dir_all(&iris).map_err(|error| error.to_string())?;
            return Ok(iris);
        }
    }
    let iris = home.join(".iris");
    let legacy = home.join(".alice");
    if let Some(source) = incomplete_migration_source(&iris, &home) {
        return Ok(source);
    }
    if !iris.exists() && legacy.exists() {
        if fs::rename(&legacy, &iris).is_err() && !iris.exists() && legacy.exists() {
            // Core may win the rename between exists() and rename(). Copy
            // only while the source still exists; otherwise its atomic rename
            // already completed the migration for both processes.
            copy_missing_profile_entries(&legacy, &iris)?;
        }
    }
    fs::create_dir_all(&iris).map_err(|error| error.to_string())?;
    if legacy.exists() {
        // A preview build may already have created .iris while the complete
        // profile still lived in .alice. Canonical files win; only missing
        // memories, voices and credentials are copied.
        if let Err(error) = copy_missing_profile_entries(&legacy, &iris) {
            if !iris.join("config.json").exists() {
                return Err(error);
            }
        }
    }
    Ok(iris)
}

fn read_json<T: for<'de> Deserialize<'de> + Default>(name: &str) -> T {
    let Ok(path) = iris_home().map(|home| home.join(name)) else {
        return T::default();
    };
    let Ok(document) = fs::read_to_string(path) else {
        return T::default();
    };
    serde_json::from_str(&document).unwrap_or_default()
}

fn valid_token(value: &str) -> bool {
    (32..=128).contains(&value.len())
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "-_".contains(character))
}

fn read_token(path: &PathBuf) -> Result<String, String> {
    let mut token = String::new();
    OpenOptions::new()
        .read(true)
        .open(path)
        .and_then(|mut file| file.read_to_string(&mut token))
        .map_err(|error| error.to_string())?;
    let token = token.trim().to_string();
    if !valid_token(&token) {
        return Err("El token local del companion no es válido".into());
    }
    Ok(token)
}

fn read_token_with_retry(path: &PathBuf) -> Result<String, String> {
    for _ in 0..20 {
        if let Ok(token) = read_token(path) {
            return Ok(token);
        }
        thread::sleep(Duration::from_millis(10));
    }
    Err("IRIS Core no terminó de crear el token local".into())
}

fn get_or_create_token() -> Result<String, String> {
    let path = iris_home()?.join("companion_token");
    if path.exists() {
        return read_token_with_retry(&path);
    }

    let mut random = [0_u8; 32];
    getrandom::fill(&mut random).map_err(|error| error.to_string())?;
    let candidate: String = random.iter().map(|byte| format!("{byte:02x}")).collect();

    #[cfg(unix)]
    let opened = {
        use std::os::unix::fs::OpenOptionsExt;
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
    };
    #[cfg(not(unix))]
    let opened = OpenOptions::new().write(true).create_new(true).open(&path);

    match opened {
        Ok(mut file) => {
            file.write_all(candidate.as_bytes())
                .map_err(|error| error.to_string())?;
            file.write_all(b"\n").map_err(|error| error.to_string())?;
            file.sync_all().map_err(|error| error.to_string())?;
            Ok(candidate)
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            // Python Core may win the create-new race. Wait briefly for its
            // buffered write to become visible, without ever replacing it.
            read_token_with_retry(&path)
        }
        Err(error) => Err(error.to_string()),
    }
}

fn endpoint_snapshot() -> (IrisConfig, u16, String) {
    let config: IrisConfig = read_json("config.json");
    let runtime: CompanionRuntime = read_json("companion_runtime.json");
    let fallback_port = if config.lan_access {
        config.companion_loopback_port
    } else {
        config.websocket_port
    };
    let port = if runtime.api_port >= 1024 {
        runtime.api_port
    } else {
        fallback_port
    };
    let iris_url = if runtime.iris_os_url.starts_with("http://")
        || runtime.iris_os_url.starts_with("https://")
    {
        runtime.iris_os_url
    } else {
        format!("http://127.0.0.1:{}/face/", config.websocket_port)
    };
    (config, port, iris_url)
}

/// A value edited by hand must not switch on more than was chosen.
fn companion_life(saved: &str) -> String {
    match saved {
        "off" | "calm" | "active" => saved,
        _ => "calm",
    }
    .to_string()
}

#[tauri::command]
fn companion_bootstrap() -> Result<CompanionBootstrap, String> {
    let (config, port, _) = endpoint_snapshot();
    let token = get_or_create_token()?;
    Ok(CompanionBootstrap {
        api_base: format!("http://127.0.0.1:{port}"),
        ws_url: format!("ws://127.0.0.1:{port}/ws"),
        token,
        preferences: CompanionPreferences {
            reduced_motion: config.companion_reduced_motion,
            show_controls: config.companion_show_controls,
            always_on_top: config.companion_always_on_top,
            scale: config.companion_scale.clamp(0.75, 1.5),
            locomotion_enabled: config.companion_locomotion_enabled,
            quiet_mode: config.companion_quiet_mode,
            life: companion_life(&config.companion_life),
        },
    })
}

#[tauri::command]
fn open_iris_os() -> Result<(), String> {
    let (_, _, url) = endpoint_snapshot();
    open_external_url(&url)
}

#[tauri::command]
fn hide_companion(window: WebviewWindow) -> Result<(), String> {
    cancel_active_walk();
    window.hide().map_err(|error| error.to_string())
}

#[tauri::command]
fn start_window_drag(window: WebviewWindow) -> Result<(), String> {
    cancel_active_walk();
    window.start_dragging().map_err(|error| error.to_string())
}

#[tauri::command]
fn set_always_on_top(window: WebviewWindow, enabled: bool) -> Result<(), String> {
    window
        .set_always_on_top(enabled)
        .map_err(|error| error.to_string())
}

fn axis_limits(origin: i32, area_extent: u32, window_extent: u32) -> (i64, i64) {
    let minimum = i64::from(origin);
    let maximum =
        (minimum + i64::from(area_extent.saturating_sub(window_extent))).min(i64::from(i32::MAX));
    (minimum, maximum)
}

fn clamp_axis(current: i32, origin: i32, area_extent: u32, window_extent: u32) -> i32 {
    let (minimum, maximum) = axis_limits(origin, area_extent, window_extent);
    i64::from(current).clamp(minimum, maximum) as i32
}

fn build_window_walk_plan(
    current: PixelPoint,
    window_width: u32,
    window_height: u32,
    work_origin: PixelPoint,
    work_width: u32,
    work_height: u32,
    requested_distance: u32,
    minimum_distance: u32,
    prefer_right: bool,
) -> Option<WindowWalkPlan> {
    let start = PixelPoint {
        x: clamp_axis(current.x, work_origin.x, work_width, window_width),
        y: clamp_axis(current.y, work_origin.y, work_height, window_height),
    };
    let (minimum_x, maximum_x) = axis_limits(work_origin.x, work_width, window_width);
    let left_room = i64::from(start.x) - minimum_x;
    let right_room = maximum_x - i64::from(start.x);
    let minimum = i64::from(minimum_distance);

    let direction = if prefer_right && right_room >= minimum {
        1_i64
    } else if !prefer_right && left_room >= minimum {
        -1_i64
    } else if right_room >= minimum {
        1_i64
    } else if left_room >= minimum {
        -1_i64
    } else {
        return None;
    };
    let room = if direction > 0 { right_room } else { left_room };
    let distance = i64::from(requested_distance).min(room).max(0) as u32;
    if distance < minimum_distance {
        return None;
    }
    let target_x = i64::from(start.x) + direction * i64::from(distance);
    Some(WindowWalkPlan {
        start,
        target: PixelPoint {
            x: target_x as i32,
            y: start.y,
        },
        distance,
    })
}

fn cancel_active_walk() {
    WALK_GENERATION.fetch_add(1, Ordering::SeqCst);
}

#[tauri::command]
fn cancel_window_walk() {
    cancel_active_walk();
}

#[tauri::command]
fn start_window_walk(
    window: WebviewWindow,
    duration_ms: Option<u64>,
) -> Result<WindowWalkStart, String> {
    let generation = WALK_GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
    let duration_ms = duration_ms.unwrap_or(3_100).clamp(800, 5_000);
    let monitor = window
        .current_monitor()
        .map_err(|error| error.to_string())?
        .or_else(|| window.primary_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return Ok(WindowWalkStart {
            started: false,
            reason: "no-monitor".into(),
            direction: "none".into(),
            distance: 0,
            duration_ms,
        });
    };

    let position = window.outer_position().map_err(|error| error.to_string())?;
    let size = window.outer_size().map_err(|error| error.to_string())?;
    let work_area = monitor.work_area();
    let scale = monitor.scale_factor().clamp(0.5, 4.0);
    let requested_distance = (112.0 * scale).round().clamp(56.0, 448.0) as u32;
    let minimum_distance = (36.0 * scale).round().clamp(24.0, 144.0) as u32;
    let prefer_right = WALK_RIGHT_FIRST.fetch_xor(true, Ordering::SeqCst);
    let plan = build_window_walk_plan(
        PixelPoint {
            x: position.x,
            y: position.y,
        },
        size.width,
        size.height,
        PixelPoint {
            x: work_area.position.x,
            y: work_area.position.y,
        },
        work_area.size.width,
        work_area.size.height,
        requested_distance,
        minimum_distance,
        prefer_right,
    );
    let Some(plan) = plan else {
        return Ok(WindowWalkStart {
            started: false,
            reason: "insufficient-work-area".into(),
            direction: "none".into(),
            distance: 0,
            duration_ms,
        });
    };

    let direction = if plan.target.x >= plan.start.x {
        "right"
    } else {
        "left"
    };
    let walk_window = window.clone();
    thread::spawn(move || {
        let steps = (duration_ms / 16).clamp(1, 320);
        for step in 0..=steps {
            if WALK_GENERATION.load(Ordering::SeqCst) != generation
                || !walk_window.is_visible().unwrap_or(false)
                || walk_window.is_minimized().unwrap_or(true)
            {
                break;
            }
            let progress = step as f64 / steps as f64;
            let eased = progress * progress * (3.0 - 2.0 * progress);
            let x = f64::from(plan.start.x) + f64::from(plan.target.x - plan.start.x) * eased;
            if walk_window
                .set_position(PhysicalPosition::new(x.round() as i32, plan.start.y))
                .is_err()
            {
                break;
            }
            if step < steps {
                thread::sleep(Duration::from_millis(16));
            }
        }
    });

    Ok(WindowWalkStart {
        started: true,
        reason: "started".into(),
        direction: direction.into(),
        distance: plan.distance,
        duration_ms,
    })
}

fn open_external_url(url: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    let status = Command::new("rundll32")
        .args(["url.dll,FileProtocolHandler", url])
        .status();
    #[cfg(target_os = "macos")]
    let status = Command::new("open").arg(url).status();
    #[cfg(all(unix, not(target_os = "macos")))]
    let status = Command::new("xdg-open").arg(url).status();

    status
        .map_err(|error| error.to_string())
        .and_then(|result| {
            if result.success() {
                Ok(())
            } else {
                Err("El sistema no pudo abrir IRIS OS".into())
            }
        })
}

fn show_companion<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(window) = app.get_webview_window("companion") {
        if let Err(error) = window.unminimize() {
            eprintln!("IRIS Companion could not restore its window: {error}");
        }
        match window.show() {
            Ok(()) => {
                WINDOW_READY.store(true, Ordering::SeqCst);
                if let Err(error) = ensure_window_visible(&window) {
                    eprintln!("IRIS Companion could not restore a visible position: {error}");
                    let recovery_handle = app.clone();
                    thread::spawn(move || {
                        for delay_ms in [150_u64, 350, 750] {
                            thread::sleep(Duration::from_millis(delay_ms));
                            let retry_handle = recovery_handle.clone();
                            let _ = recovery_handle.run_on_main_thread(move || {
                                if let Some(window) = retry_handle.get_webview_window("companion") {
                                    let _ = ensure_window_visible(&window);
                                }
                            });
                        }
                    });
                }
                let _ = window.set_focus();
            }
            Err(error) => eprintln!("IRIS Companion could not show its window: {error}"),
        }
    }
}

fn ensure_window_visible<R: tauri::Runtime>(window: &WebviewWindow<R>) -> tauri::Result<()> {
    let position = window.outer_position()?;
    let size = window.outer_size()?;
    let monitors = window.available_monitors()?;
    if let Some(monitor) = window
        .current_monitor()?
        .or(window.primary_monitor()?)
        .or_else(|| monitors.into_iter().next())
    {
        let work_area = monitor.work_area();
        let origin = &work_area.position;
        let extent = &work_area.size;
        let safe = PhysicalPosition::new(
            clamp_axis(position.x, origin.x, extent.width, size.width),
            clamp_axis(position.y, origin.y, extent.height, size.height),
        );
        if safe != position {
            window.set_position(safe)?;
        }
    }
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        // This must be registered first so a second process never initializes
        // plugins or creates a duplicate listening/camera surface.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if _args.iter().any(|argument| argument == "--iris-exit") {
                cancel_active_walk();
                EXITING.store(true, Ordering::SeqCst);
                app.exit(0);
            } else {
                show_companion(app);
            }
        }))
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::POSITION | StateFlags::SIZE)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            companion_bootstrap,
            open_iris_os,
            hide_companion,
            start_window_drag,
            set_always_on_top,
            start_window_walk,
            cancel_window_walk,
        ])
        .on_page_load(|webview, payload| {
            if webview.label() != "companion"
                || !matches!(payload.event(), PageLoadEvent::Finished)
                || WINDOW_READY.load(Ordering::SeqCst)
            {
                return;
            }
            let config: IrisConfig = read_json("config.json");
            if let Some(window) = webview.app_handle().get_webview_window("companion") {
                let _ = window.set_always_on_top(config.companion_always_on_top);
            }
            show_companion(webview.app_handle());
        })
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Mostrar IRIS", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Ocultar", true, None::<&str>)?;
            let open = MenuItem::with_id(app, "open-os", "Abrir IRIS OS", true, None::<&str>)?;
            let exit = MenuItem::with_id(app, "exit", "Salir del companion", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &hide, &open, &exit])?;
            let mut tray = TrayIconBuilder::with_id("iris-companion-tray")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .tooltip("IRIS Desktop Companion")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_companion(app),
                    "hide" => {
                        cancel_active_walk();
                        if let Some(window) = app.get_webview_window("companion") {
                            let _ = window.hide();
                        }
                    }
                    "open-os" => {
                        let (_, _, url) = endpoint_snapshot();
                        let _ = open_external_url(&url);
                    }
                    "exit" => {
                        cancel_active_walk();
                        EXITING.store(true, Ordering::SeqCst);
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(
                        event,
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        }
                    ) {
                        show_companion(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            // A hidden transparent WebView2 window may need one event-loop turn
            // before it accepts native show/position messages. Retry briefly on
            // the main thread; WINDOW_READY prevents a later retry from
            // reopening a window that the user already hid.
            let app_handle = app.handle().clone();
            thread::spawn(move || {
                for delay_ms in [100_u64, 250, 500, 1_000] {
                    if WINDOW_READY.load(Ordering::SeqCst) {
                        break;
                    }
                    thread::sleep(Duration::from_millis(delay_ms));
                    let retry_handle = app_handle.clone();
                    let _ = app_handle.run_on_main_thread(move || {
                        if !WINDOW_READY.load(Ordering::SeqCst) {
                            show_companion(&retry_handle);
                        }
                    });
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "companion" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    if !EXITING.load(Ordering::SeqCst) {
                        api.prevent_close();
                        cancel_active_walk();
                        let _ = window.hide();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("IRIS Desktop Companion failed to start");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn walk_plan_respects_work_area_and_preferred_direction() {
        let plan = build_window_walk_plan(
            PixelPoint { x: 100, y: 80 },
            400,
            520,
            PixelPoint { x: 0, y: 40 },
            1_920,
            1_000,
            112,
            36,
            true,
        )
        .expect("room to walk");

        assert_eq!(plan.start, PixelPoint { x: 100, y: 80 });
        assert_eq!(plan.target, PixelPoint { x: 212, y: 80 });
        assert_eq!(plan.distance, 112);
    }

    #[test]
    fn walk_plan_reverses_near_edge_and_never_crosses_taskbar_area() {
        let plan = build_window_walk_plan(
            PixelPoint { x: 1_500, y: 1_200 },
            400,
            520,
            PixelPoint { x: 0, y: 0 },
            1_920,
            1_040,
            140,
            36,
            true,
        )
        .expect("left side remains available");

        assert_eq!(plan.start, PixelPoint { x: 1_500, y: 520 });
        assert_eq!(plan.target.x, 1_360);
        assert_eq!(plan.target.y, 520);
    }

    #[test]
    fn walk_plan_skips_windows_that_fill_the_work_area() {
        assert_eq!(
            build_window_walk_plan(
                PixelPoint { x: -50, y: -50 },
                800,
                600,
                PixelPoint { x: 0, y: 0 },
                800,
                600,
                112,
                36,
                false,
            ),
            None,
        );
    }

    #[test]
    fn walk_plan_handles_negative_monitor_coordinates() {
        let plan = build_window_walk_plan(
            PixelPoint { x: -1_850, y: 50 },
            400,
            520,
            PixelPoint { x: -1_920, y: 0 },
            1_920,
            1_040,
            112,
            36,
            false,
        )
        .expect("secondary monitor has room");

        assert!(plan.target.x >= -1_920);
        assert!(plan.target.x + 400 <= 0);
    }

    #[test]
    fn restored_window_is_clamped_fully_inside_the_work_area() {
        assert_eq!(clamp_axis(-70, 0, 1_920, 400), 0);
        assert_eq!(clamp_axis(1_800, 0, 1_920, 400), 1_520);
        assert_eq!(clamp_axis(300, 0, 1_920, 400), 300);
        assert_eq!(clamp_axis(80, 40, 600, 800), 40);
    }

    #[test]
    fn explicit_iris_home_matches_core_normalization() {
        let home = if cfg!(windows) {
            PathBuf::from(r"C:\Users\IRIS")
        } else {
            PathBuf::from("/home/iris")
        };
        assert_eq!(
            resolve_explicit_iris_home("  ~/.iris-custom  ", &home).unwrap(),
            Some(home.join(".iris-custom")),
        );
        assert!(resolve_explicit_iris_home("relative/profile", &home).is_err());
        assert_eq!(resolve_explicit_iris_home("   ", &home).unwrap(), None);
    }

    #[test]
    fn partial_python_migration_keeps_native_companion_on_the_legacy_profile() {
        let root =
            std::env::temp_dir().join(format!("iris-companion-migration-{}", std::process::id(),));
        let iris = root.join(".iris");
        let legacy = root.join(".alice");
        fs::create_dir_all(&iris).unwrap();
        fs::create_dir_all(&legacy).unwrap();
        fs::write(iris.join(".legacy-migration-incomplete"), ".alice").unwrap();

        assert_eq!(incomplete_migration_source(&iris, &root), Some(legacy));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn vida_propia_is_read_from_the_saved_config_without_core() {
        let saved: IrisConfig = serde_json::from_str(r#"{"companion_life": "off"}"#).unwrap();
        assert_eq!(companion_life(&saved.companion_life), "off");
        let older: IrisConfig = serde_json::from_str("{}").unwrap();
        assert_eq!(companion_life(&older.companion_life), "calm");
        assert_eq!(companion_life("nonsense"), "calm");
    }
}
