//! Owns the timer context required by reqwest's async read-timeout body when
//! that body is consumed through its blocking Response adapter on std threads.

use std::sync::OnceLock;

use anyhow::{Result, anyhow};
use tokio::runtime::Runtime;

static BODY_TIMERS: OnceLock<std::result::Result<Runtime, String>> = OnceLock::new();

pub(super) fn with_context<T>(read: impl FnOnce() -> T) -> Result<T> {
    let runtime = BODY_TIMERS
        .get_or_init(|| {
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(1)
                .thread_name("aidoku-body-timer")
                .enable_time()
                .build()
                .map_err(|error| error.to_string())
        })
        .as_ref()
        .map_err(|error| anyhow!("The source body timer is unavailable: {error}"))?;
    // The blocking adapter polls ReadTimeoutBody on the calling thread. Its
    // lazy Sleep must register with a live timer driver; callers do not need
    // to provide a Tokio runtime or change their blocking API contract.
    let _timer_context = runtime.enter();
    Ok(read())
}

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
        time::{Duration, Instant},
    };

    use super::*;

    fn response(delay: Duration) -> (reqwest::blocking::Response, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/", listener.local_addr().unwrap());
        let worker = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            socket
                .set_write_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut request = [0; 4096];
            let _ = socket.read(&mut request);
            if socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\n")
                .is_err()
            {
                return;
            }
            for byte in b"ABCD" {
                thread::sleep(delay);
                if socket.write_all(&[*byte]).is_err() {
                    break;
                }
            }
        });
        let client = reqwest::blocking::ClientBuilder::from(
            reqwest::Client::builder()
                .no_proxy()
                .read_timeout(Duration::from_millis(100)),
        )
        .timeout(Duration::from_secs(1))
        .build()
        .unwrap();
        (
            client
                .get(url)
                .timeout(Duration::from_secs(1))
                .send()
                .unwrap(),
            worker,
        )
    }

    #[test]
    fn blocking_std_threads_have_a_live_body_timer_without_caller_runtime() {
        assert!(tokio::runtime::Handle::try_current().is_err());
        let (mut response, worker) = response(Duration::from_millis(40));
        let mut bytes = Vec::new();
        with_context(|| response.read_to_end(&mut bytes))
            .unwrap()
            .unwrap();
        assert_eq!(bytes, b"ABCD");
        assert!(tokio::runtime::Handle::try_current().is_err());
        worker.join().unwrap();
    }

    #[test]
    fn body_idle_deadline_still_precedes_the_longer_total_request_budget() {
        let (mut response, worker) = response(Duration::from_millis(250));
        let started = Instant::now();
        let read = with_context(|| response.read_to_end(&mut Vec::new())).unwrap();
        assert!(read.is_err());
        assert!(started.elapsed() < Duration::from_millis(800));
        drop(response);
        worker.join().unwrap();
    }
}
