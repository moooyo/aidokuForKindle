"""Audit a specified closed prefix with the frozen draw operator; never request a lease or contact a device."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile


STUDY = "Ordinary Kindle Scribe Library submission: GUI-thread CPU versus wall time in the accepted software draw"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def counts(rows):
    return {"checked_visits": len(rows), "replayed_endpoints": sum(row["interval"] is not None for row in rows),
            "replayed_draw_records": sum(row["draw"] is not None for row in rows)}


def closed_slot(host, work, scratch, plan, slot, used_labels, used_leases):
    engine, common = host.engine, host.common
    public = engine.read_json(work / "receipts" / f"{slot:02}.json")
    host.analyze.validate(public, plan["cohort"], slot)
    launched, no_card = 0, 0
    for attempt in sorted((work / "visits").glob(f"{slot:02}-*")):
        common.private_directory(attempt)
        label = attempt.name.partition("-")[2]
        visit = engine.binding(plan["cohort"], plan["owner"], slot, label)
        common.need(label not in used_labels and canonical(engine.read_json(attempt / "attempt.json")) == canonical(visit),
                    "unbound_attempt")
        used_labels.add(label)
        record = engine.read_json(attempt / "operations.json")
        bundle = attempt / "case.tar.gz"
        if engine.untouched_collision(record):
            common.need(not (attempt / "card-intent.json").exists() and not bundle.exists(), "collision_with_card_evidence")
            no_card += 1
            continue
        operations = engine.verify_return(visit, record)
        lease_id = record["identity"]["lease_token_sha256"]
        common.need(lease_id not in used_leases, "reused_lease")
        used_leases.add(lease_id)
        if not engine.reached_visit(attempt, visit, operations, record):
            common.need(not bundle.exists(), "archive_without_card_intent")
            no_card += 1
            continue
        launched += 1
        common.need(launched == 1, "duplicate_card_visit")
        if bundle.exists():
            observed = host.extract_record(common.read_small(bundle, host.reader.MAX_ARCHIVE), visit["case"])
            if public["interval"] is not None:
                common.need(canonical(observed) == canonical([public["interval"], public["draw"]]), "archive_disagrees")
        host.publish(scratch, visit, attempt, record)
    common.need(launched == 1, "missing_card_visit")
    rebuilt = engine.read_json(scratch / "receipts" / f"{slot:02}.json")
    common.need(canonical(public) == canonical(rebuilt), "public_receipt_disagrees")
    return rebuilt, no_card


def audit(work, through):
    host = module("frozen_draw_operator", work / "plan" / "host.py")
    common = host.common
    common.private_directory(work)
    plan_dir = common.private_directory(work / "plan")
    plan = host.checked_plan(work, host.engine.read_json(plan_dir / "plan.json")["owner"], payload=False)
    common.need(plan.get("diagnostic") == "release-draw-accounting" and type(through) is int
                and 1 <= through <= host.analyze.SLOTS, "wrong_plan_or_prefix")
    common.private_directory(work / "visits")
    common.private_directory(work / "receipts")
    rows, used_labels, used_leases, excluded = [], set(), set(), 0
    with tempfile.TemporaryDirectory(prefix="aidoku-replay-draw-") as folder:
        scratch = Path(folder)
        (scratch / "receipts").mkdir(mode=0o700)
        for slot in range(1, through + 1):
            rebuilt, no_card = closed_slot(host, work, scratch, plan, slot, used_labels, used_leases)
            rows.append(rebuilt)
            excluded += no_card
    return {"study": STUDY, "receipts": rows, "analysis": host.analyze.summarize(rows, plan["cohort"]),
            "audit": {**counts(rows), "no_card_attempts_excluded": excluded}}


def check_public(path):
    analyze = module("public_draw_analysis", Path(__file__).with_name("analyze.py"))
    bundle = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=analyze.unique_keys)
    require = analyze.require
    require(set(bundle) == {"study", "receipts", "analysis", "audit"} and bundle["study"] == STUDY, "wrong experiment")
    rows = bundle["receipts"]
    require(isinstance(rows, list) and 1 <= len(rows) <= analyze.SLOTS, "invalid receipts")
    report = analyze.summarize(rows, rows[0]["cohort"])
    require(canonical(bundle["analysis"]) == canonical(report), "public analysis differs from receipts")
    excluded = bundle["audit"]["no_card_attempts_excluded"]
    require(type(excluded) is int and excluded >= 0 and canonical(bundle["audit"]) == canonical({**counts(rows),
            "no_card_attempts_excluded": excluded}), "invalid audit totals")
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    modes = cli.add_subparsers(dest="mode", required=True)
    private = modes.add_parser("private", help="Rebuild from a trusted frozen worker plan after verified return")
    private.add_argument("work", type=Path)
    private.add_argument("--through", type=int, required=True)
    private.add_argument("--export", action="store_true", help="Emit the allow-listed observations, not just the counts")
    public = modes.add_parser("public", help="Recompute the public report without a private plan")
    public.add_argument("result", type=Path)
    args = cli.parse_args()
    try:
        if args.mode == "public":
            report = check_public(args.result)
            value = {"public_analysis_matches": True, "measured_formal_draws": report["measured_formal_draws"]}
        else:
            bundle = audit(args.work, args.through)
            value = bundle if args.export else {"private_receipts_match": True, **bundle["audit"]}
        print(json.dumps(value, indent=2, allow_nan=False))
    except Exception:
        sys.exit("Draw evidence audit failed; no timings exported.")
