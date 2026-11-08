fn main() {
    // tauri-build links the Visual C++ runtime into Iris.exe only when STATIC_VCRUNTIME=true
    // (scripts/build_staging.py sets it), but does not ask cargo to rerun when it changes: a
    // cached build would keep needing VCRUNTIME140.dll, which a clean Windows does not have.
    println!("cargo:rerun-if-env-changed=STATIC_VCRUNTIME");
    tauri_build::build()
}
