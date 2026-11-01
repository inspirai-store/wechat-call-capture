use std::io::{self, Write};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use windows_capture::capture::{Context, GraphicsCaptureApiHandler};
use windows_capture::frame::Frame;
use windows_capture::graphics_capture_api::InternalCaptureControl;
use windows_capture::graphics_capture_picker::{Error as PickerError, GraphicsCapturePicker};
use windows_capture::settings::{
    ColorFormat, CursorCaptureSettings, DirtyRegionSettings, DrawBorderSettings,
    MinimumUpdateIntervalSettings, SecondaryWindowSettings, Settings,
};

type Error = Box<dyn std::error::Error + Send + Sync>;
struct Packet {
    width: u32,
    height: u32,
    timestamp: i64,
    pixels: Vec<u8>,
}
#[derive(Default)]
struct State {
    latest: Option<Packet>,
    closed: bool,
    writer_failed: bool,
}
type Mailbox = Arc<(Mutex<State>, Condvar)>;
struct Capture {
    mailbox: Mailbox,
}

impl GraphicsCaptureApiHandler for Capture {
    type Flags = Mailbox;
    type Error = Error;
    fn new(ctx: Context<Self::Flags>) -> Result<Self, Self::Error> {
        Ok(Self { mailbox: ctx.flags })
    }
    fn on_frame_arrived(
        &mut self,
        frame: &mut Frame,
        control: InternalCaptureControl,
    ) -> Result<(), Self::Error> {
        let (lock, signal) = &*self.mailbox;
        if lock.lock().unwrap().writer_failed {
            control.stop();
            return Ok(());
        }
        let width = frame.width();
        let height = frame.height();
        let timestamp = frame.timestamp()?.Duration;
        let buffer = frame.buffer()?;
        let mut scratch = Vec::new();
        let pixels = buffer.as_nopadding_buffer(&mut scratch).to_vec();
        let mut state = lock.lock().unwrap();
        state.latest = Some(Packet {
            width,
            height,
            timestamp,
            pixels,
        });
        signal.notify_one();
        Ok(())
    }
    fn on_closed(&mut self) -> Result<(), Self::Error> {
        eprintln!("Capture target closed");
        Ok(())
    }
}

fn write_packets(mailbox: Mailbox) {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    loop {
        let (lock, signal) = &*mailbox;
        let mut state = lock.lock().unwrap();
        while state.latest.is_none() && !state.closed {
            state = signal.wait(state).unwrap();
        }
        let packet = match state.latest.take() {
            Some(p) => p,
            None => break,
        };
        drop(state);
        let result = (|| -> io::Result<()> {
            output.write_all(&packet.width.to_le_bytes())?;
            output.write_all(&packet.height.to_le_bytes())?;
            output.write_all(&packet.timestamp.to_le_bytes())?;
            output.write_all(&(packet.pixels.len() as u32).to_le_bytes())?;
            output.write_all(&packet.pixels)?;
            output.flush()
        })();
        if result.is_err() {
            lock.lock().unwrap().writer_failed = true;
            break;
        }
    }
}

fn run() -> Result<(), Error> {
    // This private diagnostic path captures only a window created by our integration test.
    let args: Vec<String> = std::env::args().collect();
    if args.len() == 3 && args[1] == "--diagnostic-window" {
        let handle: usize = args[2].parse()?;
        let window =
            windows_capture::window::Window::from_raw_hwnd(handle as *mut std::ffi::c_void);
        return start(window);
    }
    let item = match GraphicsCapturePicker::pick_item() {
        Ok(Some(item)) => item,
        Ok(None) | Err(PickerError::Canceled) => {
            eprintln!("Window selection canceled");
            std::process::exit(2);
        }
        Err(error) => return Err(error.into()),
    };
    start(item)
}

fn start<T: TryInto<windows_capture::settings::GraphicsCaptureItemType>>(
    item: T,
) -> Result<(), Error> {
    let mailbox: Mailbox = Arc::default();
    let writer_mailbox = mailbox.clone();
    let writer = thread::spawn(move || write_packets(writer_mailbox));
    let settings = Settings::new(
        item,
        CursorCaptureSettings::Default,
        DrawBorderSettings::Default,
        SecondaryWindowSettings::Default,
        MinimumUpdateIntervalSettings::Default,
        DirtyRegionSettings::Default,
        ColorFormat::Bgra8,
        mailbox.clone(),
    );
    let result = Capture::start(settings);
    let (lock, signal) = &*mailbox;
    {
        let mut state = lock.lock().unwrap();
        state.closed = true;
        state.latest = None;
        signal.notify_one();
    }
    let _ = writer.join();
    result.map_err(|e| format!("Capture failed: {e}").into())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}
