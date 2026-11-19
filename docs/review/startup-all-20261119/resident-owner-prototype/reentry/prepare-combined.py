#!/usr/bin/env python3
"""Bind the private process test to an actual Slint backend lifecycle and release receipt."""
import argparse
import importlib.util
import json
from pathlib import Path


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('backend',type=Path);p.add_argument('destination',type=Path)
    args=p.parse_args()
    here=Path(__file__).resolve().parent
    spec=importlib.util.spec_from_file_location('prepare',here/'prepare-source.py')
    prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep)
    prep.prepare(args.source,args.destination)
    main=args.destination/'src/main.rs';s=main.read_text()
    a=s.index('// The separate real Slint test is responsible');b=s.index('static PHASE:',a)
    s=s[:a]+'pub use real_backend::{LibraryIoClosed,NativePortraitOrientation,NativePortraitSensorObservation,NativeFramebufferObservation};\n'+s[b:]
    a=s.index('#[derive(Clone,Copy,Debug,Eq,PartialEq)]pub enum NativePortraitOrientation');b=s.index('pub fn observe_native_portrait_sensor',a)
    s=s[:a]+'''pub fn probe_native_framebuffer()->io::Result<NativeFramebufferObservation>{call("getvar");real_backend::studio_observation(if PHASE.load(Ordering::SeqCst)==0 {0}else{2})}
'''+s[b:]
    s=prep.replace(s,'let mut current=None;let mut closed=None;let mut new=None;', 'let mut current=None;let mut closed=None;let mut new=None;let mut studio=None;')
    s=prep.replace(s,'let observation=ctx.observe_startup()?;current=Some(ctx);','let observation=ctx.observe_startup()?;studio=Some(real_backend::start_studio()?);current=Some(ctx);')
    s=prep.replace(s,'LibraryIoClosed{round:0})?', 'studio.as_mut().unwrap().close()?)?')
    s=prep.replace(s,'new=Some(ctx);Ok(json!({"scanout":', 'new=Some(ctx);studio.as_ref().unwrap().second_frame()?;Ok(json!({"same_slint_window_repainted":true,"scanout":')
    s=prep.replace(s,'  Ok(())\n}', '  if let Some(gui)=studio {gui.shutdown()?;}\n  Ok(())\n}')
    main.write_text(s)
    manifest=args.destination/'Cargo.toml';t=manifest.read_text();t=prep.replace(t,'[dependencies]\n',
        '[dependencies]\nreal_backend = { package = "slint-backend-kindle", path = '+json.dumps(str(args.backend.resolve()))+', features = ["resident-studio"] }\n');manifest.write_text(t)