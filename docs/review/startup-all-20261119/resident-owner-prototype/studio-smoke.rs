use slint_backend_kindle::{NativePortraitOrientation, start_studio, studio_observation};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut studio = start_studio()?;
    let closed = studio.close()?;
    assert_eq!(closed.round(), 0);
    assert!(studio.close().is_err());
    assert!(studio_observation(4).is_err());
    closed.reopen(Some((
        NativePortraitOrientation::Down,
        NativePortraitOrientation::Down,
        studio_observation(2)?,
    )))?;
    studio.second_frame()?;
    studio.shutdown()?;
    println!("First and second checked Slint frames completed with the same window and process");
    Ok(())
}
