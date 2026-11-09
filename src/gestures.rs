//! Pointer pagination observed before forwarding input to Slint.
//!
//! Ordinary taps and stationary holds pass through unchanged. Once a drag is
//! recognized, the caller sends `WindowEvent::PointerExited` to cancel the child
//! control's press and hold, then suppresses raw events while `consume` is true.
//! A valid horizontal swipe changes the page only on release. This keeps the
//! contents stationary throughout the gesture and avoids intercepting child taps.

use std::time::{Duration, Instant};

pub const SWIPE_DISTANCE: f32 = 33.0;
pub const MAX_SWIPE_DURATION: Duration = Duration::from_millis(900);

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PaginationDecision {
    /// Cancel the existing child press before forwarding any further input.
    pub cancel_children: bool,
    /// Suppress the associated raw pointer event after handling cancellation.
    pub consume: bool,
    /// Page delta: left swipe advances by one; right swipe goes back by one.
    pub turn: Option<i32>,
}

#[derive(Debug, Clone, Copy)]
struct Press {
    x: f32,
    y: f32,
    at: Instant,
    captured: bool,
}

#[derive(Debug, Default)]
pub struct PointerPagination {
    press: Option<Press>,
}

impl PointerPagination {
    /// Start observing without preventing the child control from being pressed.
    pub fn press(&mut self, x: f32, y: f32, now: Instant, enabled: bool) -> PaginationDecision {
        self.press = (enabled && x.is_finite() && y.is_finite()).then_some(Press {
            x,
            y,
            at: now,
            captured: false,
        });
        PaginationDecision::default()
    }

    /// Cancel child activation once movement becomes a drag, without paging yet.
    pub fn move_to(&mut self, x: f32, y: f32, now: Instant, enabled: bool) -> PaginationDecision {
        if !enabled || !x.is_finite() || !y.is_finite() {
            return self.abort();
        }
        let Some(press) = self.press.as_mut() else {
            return PaginationDecision::default();
        };
        if now.checked_duration_since(press.at).is_none() {
            return self.abort();
        }
        let delta_x = (f64::from(x) - f64::from(press.x)).abs();
        let delta_y = (f64::from(y) - f64::from(press.y)).abs();
        let drag = delta_x >= f64::from(SWIPE_DISTANCE) || delta_y >= f64::from(SWIPE_DISTANCE);
        let cancel_children = drag && !press.captured;
        press.captured |= drag;
        PaginationDecision {
            cancel_children,
            consume: press.captured,
            turn: None,
        }
    }

    /// Commit only a horizontal, sufficiently large swipe within the time limit.
    pub fn release(&mut self, x: f32, y: f32, now: Instant, enabled: bool) -> PaginationDecision {
        let Some(press) = self.press.take() else {
            return PaginationDecision::default();
        };
        if !enabled || !x.is_finite() || !y.is_finite() {
            return PaginationDecision {
                consume: press.captured,
                ..PaginationDecision::default()
            };
        }
        let Some(elapsed) = now.checked_duration_since(press.at) else {
            return PaginationDecision {
                consume: press.captured,
                ..PaginationDecision::default()
            };
        };
        let delta_x = f64::from(x) - f64::from(press.x);
        let delta_y = f64::from(y) - f64::from(press.y);
        let horizontal =
            delta_x.abs() >= f64::from(SWIPE_DISTANCE) && delta_x.abs() > delta_y.abs();
        let drag = delta_x.abs() >= f64::from(SWIPE_DISTANCE)
            || delta_y.abs() >= f64::from(SWIPE_DISTANCE);
        PaginationDecision {
            cancel_children: drag && !press.captured,
            consume: press.captured || drag,
            turn: (horizontal && elapsed <= MAX_SWIPE_DURATION).then_some(if delta_x < 0.0 {
                1
            } else {
                -1
            }),
        }
    }

    /// Clear observation when the underlying pointer sequence is cancelled.
    pub fn cancel(&mut self) {
        self.press = None;
    }

    fn abort(&mut self) -> PaginationDecision {
        PaginationDecision {
            consume: self.press.take().is_some_and(|press| press.captured),
            ..PaginationDecision::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(start: Instant, milliseconds: u64) -> Instant {
        start + Duration::from_millis(milliseconds)
    }

    #[test]
    fn normal_child_taps_pass_through() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        assert_eq!(
            gesture.press(140.0, 360.0, start, true),
            PaginationDecision::default()
        );
        assert_eq!(
            gesture.move_to(144.0, 364.0, at(start, 60), true),
            PaginationDecision::default()
        );
        assert_eq!(
            gesture.release(144.0, 364.0, at(start, 100), true),
            PaginationDecision::default()
        );
    }

    #[test]
    fn stationary_long_holds_preserve_child_callbacks() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(140.0, 360.0, start, true);
        assert_eq!(
            gesture.move_to(143.0, 362.0, at(start, 600), true),
            PaginationDecision::default()
        );
        assert_eq!(
            gesture.release(143.0, 362.0, at(start, 1_200), true),
            PaginationDecision::default()
        );
    }

    #[test]
    fn left_swipe_cancels_child_once_and_advances_on_release() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(480.0, 420.0, start, true);
        assert_eq!(
            gesture.move_to(440.0, 420.0, at(start, 100), true),
            PaginationDecision {
                cancel_children: true,
                consume: true,
                turn: None
            }
        );
        assert_eq!(
            gesture.move_to(300.0, 420.0, at(start, 200), true),
            PaginationDecision {
                cancel_children: false,
                consume: true,
                turn: None
            }
        );
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 300), true),
            PaginationDecision {
                cancel_children: false,
                consume: true,
                turn: Some(1)
            }
        );
    }

    #[test]
    fn right_swipe_goes_to_the_previous_list_page() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(180.0, 420.0, start, true);
        gesture.move_to(300.0, 420.0, at(start, 100), true);
        assert_eq!(
            gesture.release(480.0, 420.0, at(start, 300), true).turn,
            Some(-1)
        );
    }

    #[test]
    fn release_without_a_move_still_cancels_child_before_paging() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(480.0, 420.0, start, true);
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 300), true),
            PaginationDecision {
                cancel_children: true,
                consume: true,
                turn: Some(1)
            }
        );
    }

    #[test]
    fn returning_to_the_origin_consumes_release_without_paging() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(480.0, 420.0, start, true);
        gesture.move_to(400.0, 420.0, at(start, 100), true);
        assert_eq!(
            gesture.release(480.0, 420.0, at(start, 300), true),
            PaginationDecision {
                cancel_children: false,
                consume: true,
                turn: None
            }
        );
    }

    #[test]
    fn vertical_and_diagonal_drags_do_not_activate_children_or_page() {
        let start = Instant::now();
        for (x, y) in [(240.0, 600.0), (100.0, 560.0)] {
            let mut gesture = PointerPagination::default();
            gesture.press(240.0, 420.0, start, true);
            assert!(gesture.move_to(x, y, at(start, 100), true).cancel_children);
            assert_eq!(
                gesture.release(x, y, at(start, 300), true),
                PaginationDecision {
                    cancel_children: false,
                    consume: true,
                    turn: None
                }
            );
        }
    }

    #[test]
    fn distance_and_time_limits_are_inclusive() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(100.0, 100.0, start, true);
        assert_eq!(
            gesture.release(67.0, 100.0, at(start, 900), true).turn,
            Some(1)
        );
        gesture.press(100.0, 100.0, start, true);
        assert_eq!(
            gesture.release(67.01, 100.0, at(start, 900), true),
            PaginationDecision::default()
        );
        gesture.press(100.0, 100.0, start, true);
        assert_eq!(
            gesture.release(67.0, 100.0, at(start, 901), true),
            PaginationDecision {
                cancel_children: true,
                consume: true,
                turn: None
            }
        );
    }

    #[test]
    fn captured_swipe_expiration_never_becomes_a_child_click() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(480.0, 420.0, start, true);
        gesture.move_to(400.0, 420.0, at(start, 100), true);
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 901), true),
            PaginationDecision {
                cancel_children: false,
                consume: true,
                turn: None
            }
        );
    }

    #[test]
    fn disabling_pagination_clears_observation() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        assert_eq!(
            gesture.press(480.0, 420.0, start, false),
            PaginationDecision::default()
        );
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 300), true),
            PaginationDecision::default()
        );
        gesture.press(480.0, 420.0, start, true);
        gesture.move_to(400.0, 420.0, at(start, 100), true);
        assert!(gesture.release(180.0, 420.0, at(start, 300), false).consume);
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 400), true),
            PaginationDecision::default()
        );
    }

    #[test]
    fn cancellation_and_orphan_events_do_not_page() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(480.0, 420.0, start, true);
        gesture.cancel();
        assert_eq!(
            gesture.move_to(180.0, 420.0, at(start, 100), true),
            PaginationDecision::default()
        );
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 300), true),
            PaginationDecision::default()
        );
    }

    #[test]
    fn invalid_coordinates_and_clock_order_do_not_page() {
        let start = Instant::now();
        let mut gesture = PointerPagination::default();
        gesture.press(f32::NAN, 420.0, start, true);
        assert_eq!(
            gesture.release(180.0, 420.0, at(start, 100), true),
            PaginationDecision::default()
        );
        gesture.press(480.0, 420.0, at(start, 100), true);
        assert_eq!(gesture.release(180.0, 420.0, start, true).turn, None);
        gesture.press(480.0, 420.0, start, true);
        assert_eq!(
            gesture
                .move_to(f32::INFINITY, 420.0, at(start, 100), true)
                .turn,
            None
        );
    }
}
