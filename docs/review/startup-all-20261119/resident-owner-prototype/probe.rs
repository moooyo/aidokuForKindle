//! Host-only test fixture for the unmodified production identity and writer checks.
//! Every hardware read fails; no successful native calibration can be fabricated here.
#![allow(dead_code)]

extern crate self as slint_backend_kindle;

use std::io::{self, BufRead, Write};
use std::sync::atomic::{AtomicUsize, Ordering};

use serde_json::{Value, json};

mod native_presentation;
mod native_query;

static HARDWARE_CALLS: AtomicUsize = AtomicUsize::new(0);

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum NativePortraitOrientation { Up, Down }
#[derive(Clone, Copy, PartialEq, Debug)]
pub struct NativePortraitSensorObservation { pub pose: Option<NativePortraitOrientation> }
#[derive(Clone, Copy, PartialEq)]
pub struct NativeFramebufferObservation { pub rotation: u32 }
impl NativeFramebufferObservation {
    pub fn layout_token(&self) -> String { unreachable!("The probe never supplies a hardware layout") }
}
fn no_hardware<T>() -> io::Result<T> {
    HARDWARE_CALLS.fetch_add(1, Ordering::SeqCst);
    Err(io::Error::other("Host fixture: no native hardware was accessed"))
}
pub fn probe_native_framebuffer() -> io::Result<NativeFramebufferObservation> { no_hardware() }
pub fn observe_native_portrait_sensor() -> io::Result<Option<NativePortraitSensorObservation>> { no_hardware() }
mod platform {
    #[derive(Copy, Clone, PartialEq)]
    pub enum DeviceProfile { Pw5, Scribe }
    impl DeviceProfile {
        pub fn id(self) -> &'static str { match self { Self::Pw5 => "pw5", Self::Scribe => "scribe" } }
    }
}
mod reader_timing { pub fn mark(_: &str) {} }

fn report<T>(result: anyhow::Result<T>) -> Value {
    json!({"ok":result.is_ok(),"error":result.err().map(|error| error.to_string()),
        "hardware_calls":HARDWARE_CALLS.swap(0, Ordering::SeqCst)})
}
fn send(message: &Value) -> io::Result<()> {
    let mut output = io::stdout().lock();
    writeln!(output,"{message}")?;
    output.flush()
}
fn main() -> anyhow::Result<()> {
    use native_presentation::NativePresentationContext as Context;
    use platform::DeviceProfile::Pw5;
    if std::env::args().nth(1).as_deref() == Some("capture") {
        return Ok(send(&report(native_presentation::capture_startup(Pw5, || false)))?);
    }
    let mut context: Option<Context> = None;
    send(&json!({"pid":std::process::id()}))?;
    for input in io::stdin().lock().lines() {
        let line = input?;
        let result = match line.as_str() {
            "load" => report(Context::from_environment(Pw5).and_then(|next| {
                context = Some(next.ok_or_else(|| anyhow::anyhow!("Unmanaged fixture"))?);
                Ok(())
            })),
            "startup" => report(context.as_ref().unwrap().observe_startup()),
            "resume" => report(context.as_ref().unwrap().observe_resume(1)),
            "quit" => break,
            _ => anyhow::bail!("Unknown fixture command"),
        };
        send(&result)?;
    }
    Ok(())
}
