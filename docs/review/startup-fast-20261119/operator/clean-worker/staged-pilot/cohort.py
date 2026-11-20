"""Case schedules and the decision contract for staged startup measurements."""

PAIR_COUNTS = {"pilot": 2, "screen": 6, "confirmation": 10}
ORIGIN = "pre-exec of staged managed foreground launcher"
CONFIRMATION = {
    "primary": "covers_ms",
    "median_min_saving_ms": 100,
    "min_faster_pairs": 9,
    "guard": "usable_ms",
    "guard_max_median_regression_ms": 50,
    "practical_target_ms": 1000,
}


def schedule(regime, mode):
    if regime not in ("warm", "cold") or mode not in PAIR_COUNTS:
        raise ValueError("Unknown staged cohort")
    if mode == "confirmation" and regime != "cold":
        raise ValueError("Only the cold screen qualified for confirmation")
    cases = (["prime"] if regime == "warm" else []) + ["baseline-warmup", "candidate-warmup"]
    for pair in range(1, PAIR_COUNTS[mode] + 1):
        sides = ("baseline", "candidate") if pair % 2 else ("candidate", "baseline")
        cases.extend(f"{side}-{pair:02}" for side in sides)
    return cases + ["baseline-picture", "candidate-picture"]


def check_protocol(plan):
    mode, regime = plan["mode"], plan["regime"]
    cases = schedule(regime, mode)
    if plan["origin"] != ORIGIN or plan["cases"] != cases or plan["pairs"] != PAIR_COUNTS[mode]:
        raise ValueError("Changed staged protocol")
    if mode == "confirmation" and plan.get("decision") != CONFIRMATION:
        raise ValueError("Changed confirmation thresholds")
    return cases
