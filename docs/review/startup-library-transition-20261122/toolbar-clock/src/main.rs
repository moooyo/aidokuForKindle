use std::io::{self, BufRead, Read, Write};
use std::os::fd::AsRawFd;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use chrono::{DateTime, Local};

fn epoch_time(value: i64) -> String {
    DateTime::from_timestamp(value, 0)
        .expect("Representable UTC timestamp")
        .with_timezone(&Local)
        .format("%H:%M")
        .to_string()
}

fn local_time() -> String {
    Local::now().format("%H:%M").to_string()
}

fn read_available(reader: &mut impl Read, output: &mut Vec<u8>, deadline: Instant) -> io::Result<()> {
    let mut buffer = [0; 1024];
    loop {
        if Instant::now() >= deadline {
            return Err(io::Error::new(io::ErrorKind::TimedOut, "date timeout"));
        }
        match reader.read(&mut buffer) {
            Ok(0) => return Ok(()),
            Ok(count) if output.len() + count <= 65536 => output.extend_from_slice(&buffer[..count]),
            Ok(_) => return Err(io::Error::other("date output limit")),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => return Ok(()),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
}

fn date_time() -> io::Result<String> {
    let mut child = Command::new("date")
        .arg("+%H:%M")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut stdout = child.stdout.take().ok_or_else(|| io::Error::other("missing pipe"))?;
    let descriptor = stdout.as_raw_fd();
    let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        let error = io::Error::last_os_error();
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    let deadline = Instant::now() + Duration::from_millis(700);
    let mut output = Vec::new();
    loop {
        if let Err(error) = read_available(&mut stdout, &mut output, deadline) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                read_available(&mut stdout, &mut output, deadline)?;
                if !status.success() {
                    return Err(io::Error::other("date failed"));
                }
                return Ok(String::from_utf8_lossy(&output).trim().to_owned());
            }
            Ok(None) => std::thread::sleep(
                deadline.saturating_duration_since(Instant::now()).min(Duration::from_millis(5)),
            ),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        }
    }
}

fn duration(operation: impl FnOnce() -> String) -> u128 {
    let started = Instant::now();
    std::hint::black_box(operation());
    started.elapsed().as_micros()
}

fn main() -> io::Result<()> {
    let arguments: Vec<_> = std::env::args().collect();
    match arguments.get(1).map(String::as_str) {
        Some("epoch") if arguments.len() == 3 => {
            let epoch = arguments[2].parse().expect("Integer UTC timestamp");
            println!("{}", epoch_time(epoch));
        }
        Some("stream") if arguments.len() == 2 => {
            for line in io::stdin().lock().lines() {
                let epoch = line?.parse().expect("Integer UTC timestamp");
                println!("{}", epoch_time(epoch));
                io::stdout().flush()?;
            }
        }
        Some("bench") if arguments.len() == 3 => {
            let samples: usize = arguments[2].parse().expect("Integer sample count");
            for index in 0..samples {
                let (local, date) = if index % 2 == 0 {
                    (duration(local_time), duration(|| date_time().expect("date query")))
                } else {
                    let date = duration(|| date_time().expect("date query"));
                    (duration(local_time), date)
                };
                println!("{local} {date}");
            }
        }
        _ => return Err(io::Error::other("usage: epoch SECONDS | stream | bench COUNT")),
    }
    Ok(())
}
