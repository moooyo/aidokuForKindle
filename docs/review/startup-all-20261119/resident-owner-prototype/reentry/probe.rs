//! Isolated Linux participant; small typed host adapters replace native hardware and catalog origin.
#![allow(dead_code)]
extern crate self as slint_backend_kindle;
use std::{io, os::{fd::{AsRawFd,FromRawFd,RawFd},unix::net::{UnixStream,UnixDatagram}}, sync::{Mutex,atomic::{AtomicU8,Ordering}},time::Instant};
use serde_json::{json,Value};
mod native_presentation;
// The separate real Slint test is responsible for emitting this single-use backend fence.
// This narrow adapter isolates the process and native-writer test from device resources.
pub struct LibraryIoClosed { round:u64 }
impl LibraryIoClosed {pub fn round(&self)->u64{self.round}pub fn reopen(self, fresh:Option<(NativePortraitOrientation,NativePortraitOrientation,NativeFramebufferObservation)>)->io::Result<()>{match fresh {Some((NativePortraitOrientation::Down,NativePortraitOrientation::Down,NativeFramebufferObservation{rotation:2}))if self.round==0=>Ok(()),_=>Err(io::Error::other("Wrong fixture rebind calibration"))}}}
static PHASE: AtomicU8=AtomicU8::new(0);
static CALLS:Mutex<Vec<&'static str>>=Mutex::new(Vec::new());
fn call(v:&'static str){CALLS.lock().unwrap().push(v)}
#[derive(Clone,Copy,Debug,Eq,PartialEq)]pub enum NativePortraitOrientation{Up,Down}
#[derive(Clone,Copy,Debug,Eq,PartialEq)]pub struct NativePortraitSensorObservation{pub pose:Option<NativePortraitOrientation>}
#[derive(Clone,Copy,Eq,PartialEq)]pub struct NativeFramebufferObservation{pub rotation:u32}
impl NativeFramebufferObservation{pub fn layout_token(&self)->String{"1:2:3".into()}}
pub fn probe_native_framebuffer()->io::Result<NativeFramebufferObservation>{call("getvar");Ok(NativeFramebufferObservation{rotation:if PHASE.load(Ordering::SeqCst)==0 {0}else{2}})}
pub fn observe_native_portrait_sensor()->io::Result<Option<NativePortraitSensorObservation>>{call("sensor");Ok(Some(NativePortraitSensorObservation{pose:None}))}
mod reader_timing {pub fn mark(_: &str){}}
mod platform {#[derive(Copy,Clone,PartialEq)]pub enum DeviceProfile{Pw5,Scribe}impl DeviceProfile{pub fn id(self)->&'static str{match self{Self::Pw5=>"pw5",Self::Scribe=>"scribe"}}}}
mod native_query {
    use super::*;
    #[derive(Copy,Clone)]pub enum Property{SleepModules,PowerState, DisplayMode,Portrait}
    impl Property {pub fn legacy_arguments(&self)->&[&str]{panic!("No host system service may be called in this fixture")}}
    pub struct Session;impl Session {
        pub fn try_open(_:Instant,_:&impl Fn()->bool)->Option<Self>{Some(Self)} pub fn try_open_startup(v:Instant,c:&impl Fn()->bool)->Option<Self>{Self::try_open(v,c)}
        pub fn supports_sleep_modules(&self)->bool{true}pub fn query(&mut self, p:Property,_:Instant,_:&impl Fn()->bool)->anyhow::Result<String>{
           Ok(match p {Property::PowerState=>{call("power");"active"},Property::DisplayMode=>{call("mode");"Y8"},Property::Portrait=>{call("direction");if PHASE.load(Ordering::SeqCst)==0{"U"}else{"D"}}, Property::SleepModules=>{call("sleep");"screensaver"}}.into())}
        pub fn finish(self,_:Instant,_:&impl Fn()->bool)->anyhow::Result<()>{call("finish");Ok(())}
    }
}
// Deliberate host-only adapter. No native handler, device path or vendor process is impersonated.
mod native_library_visit {use super::*;pub struct Birth{pub pid:u32,pub birth:u64}pub struct NativeLibraryVisit{dev:u64, ino:u64,pub waiter:Birth,pub su:Birth,pub handler:Birth}pub fn observe(s:&UnixStream)->io::Result<NativeLibraryVisit>{use std::os::unix::fs::MetadataExt;let m=std::fs::metadata(format!("/proc/self/fd/{}",s.as_raw_fd()))?;let mut c=libc::ucred{pid:0,uid:0,gid:0};let mut n=std::mem::size_of_val(&c) as libc::socklen_t;
if unsafe{libc::getsockopt(s.as_raw_fd(),libc::SOL_SOCKET,libc::SO_PEERCRED,(&mut c as *mut libc::ucred).cast(),&mut n)}!=0{return Err(io::Error::last_os_error());}
let stat=std::fs::read_to_string(format!("/proc/{}/stat",c.pid))?;let birth=stat.rsplit_once(") ").unwrap().1.split_ascii_whitespace().nth(19).unwrap().parse::<u64>().unwrap();let pid=c.pid as u32;
Ok(NativeLibraryVisit{dev:m.dev(),ino:m.ino(),waiter:Birth{pid,birth},su:Birth{pid,birth},handler:Birth{pid,birth}})}impl NativeLibraryVisit{pub fn revalidate(&self,s:&UnixStream)->io::Result<()>{let other=observe(s)?;if self.dev==other.dev&&self.ino==other.ino{Ok(())}else{Err(io::ErrorKind::PermissionDenied.into())}}}}
fn report(r:anyhow::Result<Value>)->Value{let (ok,value,reason)=match r{Ok(v)=>(true,v,None),Err(e)=>(false,Value::Null,Some(format!("{e:#}")))};json!({"ok":ok,"value":value,"error":reason,"observations":std::mem::take(&mut *CALLS.lock().unwrap())})}
fn obtain_fd()->anyhow::Result<UnixStream>{let n=std::env::var("FIX_ENTRY_FD")?.parse::<RawFd>()?;anyhow::ensure!(n>2);Ok(unsafe{UnixStream::from_raw_fd(n)})}
fn packet(s:&UnixDatagram)->anyhow::Result<(String,Option<UnixStream>)>{use std::mem;let mut data=[0u8;96];let mut aux=[0usize;16];let mut iov=libc::iovec{iov_base:data.as_mut_ptr().cast(),iov_len:data.len()};let mut msg:libc::msghdr=unsafe{mem::zeroed()};msg.msg_iov=&mut iov;msg.msg_iovlen=1;msg.msg_control=aux.as_mut_ptr().cast();msg.msg_controllen=mem::size_of_val(&aux);let n=unsafe{libc::recvmsg(s.as_raw_fd(),&mut msg,libc::MSG_CMSG_CLOEXEC)};anyhow::ensure!(n>0 && msg.msg_flags&(libc::MSG_TRUNC|libc::MSG_CTRUNC)==0,"Malformed host control packet");let h=unsafe{libc::CMSG_FIRSTHDR(&msg)};let fd=if h.is_null(){None}else{let h1=unsafe{&*h};anyhow::ensure!(h1.cmsg_level==libc::SOL_SOCKET&&h1.cmsg_type==libc::SCM_RIGHTS&&h1.cmsg_len==unsafe{libc::CMSG_LEN(mem::size_of::<RawFd>() as u32)} as usize);let raw=unsafe{std::ptr::read_unaligned(libc::CMSG_DATA(h).cast::<RawFd>())};Some(unsafe{UnixStream::from_raw_fd(raw)})};Ok((std::str::from_utf8(&data[..n as usize])?.to_owned(),fd))}
fn main()->anyhow::Result<()> {
  use crate::native_presentation::{NativePresentationContext as Context,library_return as lib};use platform::DeviceProfile::Pw5;
  let args=std::env::args().skip(1).collect::<Vec<_>>();let round=1;
  if args[0]!="gui"{let result=(||->anyhow::Result<Value>{match args[0].as_str(){"cold"=>native_presentation::capture_startup(Pw5,||false)?, "home"=>lib::confirm_home(0,&lib::Entry::from_waiter(obtain_fd()?)?)?,"next"=>{PHASE.store(1,Ordering::SeqCst);lib::capture(Pw5,round,&lib::Entry::from_waiter(obtain_fd()?)?,||false)?},"take"=>lib::confirm_takeover(round,&lib::Entry::from_waiter(obtain_fd()?)?)?, _=>anyhow::bail!("Unknown fixture service")};Ok(Value::Null)})();println!("{}",report(result));return Ok(())}
  let chan=unsafe{UnixDatagram::from_raw_fd(args[1].parse()?)};let path=std::path::PathBuf::from(std::env::var_os("AIDOKU_LAUNCHER_DIR").unwrap());let mut current=None;let mut closed=None;let mut new=None;
  chan.send(json!({"pid":std::process::id()}).to_string().as_bytes())?;
  loop{let (command,received)=packet(&chan)?;if command=="quit"{break;}let result=(|| ->anyhow::Result<Value> {match command.as_str(){
      "initial"=>{let ctx=Context::from_environment(Pw5)?.ok_or(anyhow::anyhow!("Unmanaged GUI"))?;let observation=ctx.observe_startup()?;current=Some(ctx);Ok(json!({"scanout":format!("{:?}",observation.scanout)}))},
      "close"=>{let e=lib::Entry::from_waiter(received.unwrap())?;closed=Some(lib::record_closed(lib::Showing::Initial{context:current.take().unwrap(),entry:e},LibraryIoClosed{round:0})?);Ok(Value::Null)},
      "try-original"=>{let _=Context::from_environment(Pw5)?;Ok(Value::Null)},
      "prepared"=>{let e=lib::Entry::from_waiter(received.unwrap())?;closed.as_ref().unwrap().verify_commit(&path,e)?;Ok(Value::Null)},
      "apply"=>{let e=lib::Entry::from_waiter(received.unwrap())?;PHASE.store(1,Ordering::SeqCst);let(ctx,observation)=closed.take().unwrap().rebind(&path,e)?;new=Some(ctx);Ok(json!({"scanout":format!("{:?}",observation.scanout),"rotation":observation.framebuffer.rotation,"pid":std::process::id()}))},
      "observe"=>{new.as_ref().unwrap().observe()?; Ok(Value::Null)},_=>anyhow::bail!("Unknown GUI fixture request")}})();chan.send(report(result).to_string().as_bytes())?;}
  Ok(())
}