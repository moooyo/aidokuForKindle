"""Read only an explicitly published prefix of one private diagnostic plan; export relative times only."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


STUDY = "Ordinary Scribe stock-card startup, one diagnostic configuration; same-visit stage attribution"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def issued_slot(host, work, plan, slot, labels, identities):
    engine, common = host.engine, host.common
    outer = engine.read_json(work / "receipts" / f"{slot:02}.json")
    host.analyze.validate(outer, plan["cohort"], slot)
    rebuilt = None
    unlaunched = 0
    for attempt in sorted((work / "visits").glob(f"{slot:02}-*")):
        common.private_directory(attempt)
        label = attempt.name.partition("-")[2]
        visit = host.binding(plan["cohort"], plan["owner"], slot, label)
        common.need(label not in labels and encoded(engine.read_json(attempt / "attempt.json")) == encoded(visit),
                    "unbound_attempt")
        labels.add(label)
        report = engine.read_json(attempt / "operations.json")
        if engine.untouched_collision(report):
            common.need(not (attempt / "card-intent.json").exists() and not (attempt / "case.tar.gz").exists(),
                        "collision_with_card_evidence")
            unlaunched += 1
            continue
        operations = engine.verify_return(visit, report)
        identity = report["identity"]["lease_token_sha256"]
        common.need(identity not in identities, "reused_lease_identity")
        identities.add(identity)
        if not engine.reached_visit(attempt, visit, operations, report):
            common.need(not (attempt / "case.tar.gz").exists(), "archive_without_card_intent")
            unlaunched += 1
            continue
        common.need(rebuilt is None, "multiple_card_visits_in_slot")
        case = visit["case"]
        names = [step["operation"] for step in operations]
        first = ["guard", "admit", "prepare", "visit:" + case]
        entered = names[:4] == first and all(step.get("outcome") == "ok" for step in operations[:4])
        complete = (entered and names == first + ["collect:" + case, "close", "absent"]
                    and all(step.get("outcome") == "ok" for step in operations)
                    and report.get("completed") == [case] and report.get("operations_passed") is True)
        interval, stages = None, None
        if (attempt / "case.tar.gz").exists():
            observed = host.extract_record(common.read_small(attempt / "case.tar.gz", host.rendered.MAX_ARCHIVE), case)
            if complete:
                interval, stages = observed
        release = report["release"]
        closed = {"ordinary_exit": entered, "native_restored": report.get("close_confirmed") is True,
                  "close": report.get("close_confirmed") is True, "absent": report.get("absence_confirmed") is True,
                  "connections_closed": release["native_restored_and_connections_closed"],
                  "owner_and_token_checked": release["owner_and_token_checked"], "released": report["released"]}
        rebuilt = {"cohort": plan["cohort"], "configuration": host.CONFIGURATION, "slot": slot,
                   "result": "measured" if interval is not None else "not_measured", "interval": interval,
                   "stages": stages, "closeout": closed}
    common.need(rebuilt is not None and encoded(rebuilt) == encoded(outer), "unverified_public_slot")
    return rebuilt, unlaunched


def counts(receipts):
    return {"closed_card_visits": len(receipts), "replayed_endpoint_archives": sum(row["interval"] is not None for row in receipts),
            "visits_with_stages": sum(row["stages"] is not None for row in receipts),
            "visits_with_initial": sum(row["stages"] is not None and row["stages"]["initial"] is not None for row in receipts)}


def audit(work, through):
    host = load_module("frozen_stock_stage_host", work / "plan" / "host.py")
    common = host.common
    common.private_directory(work)
    plan_dir = common.private_directory(work / "plan")
    plan = host.checked_plan(work, host.engine.read_json(plan_dir / "plan.json")["owner"], payload=False)
    common.private_directory(work / "visits")
    common.private_directory(work / "receipts")
    common.need(type(through) is int and 1 <= through <= host.SLOTS, "invalid_closed_prefix")
    receipts, labels, identities = [], set(), set()
    unlaunched = 0
    for slot in range(1, through + 1):
        receipt, excluded = issued_slot(host, work, plan, slot, labels, identities)
        receipts.append(receipt)
        unlaunched += excluded
    return {"study": STUDY, "receipts": receipts, "analysis": host.analyze.summarize(receipts, plan["cohort"]),
            "audit": {**counts(receipts), "no_card_attempts_excluded": unlaunched}}


def check_public(path):
    analyze = load_module("public_stock_stage_analysis", Path(__file__).with_name("analyze.py"))
    require = analyze.require
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=analyze.unique_keys)
    require(set(result) == {"study", "receipts", "analysis", "audit"} and result["study"] == STUDY, "unexpected study")
    rows = result["receipts"]
    require(isinstance(rows, list) and 1 <= len(rows) <= analyze.SLOTS, "invalid receipts")
    calculated = analyze.summarize(rows, rows[0]["cohort"])
    require(encoded(result["analysis"]) == encoded(calculated), "analysis differs from receipts")
    excluded = result["audit"]["no_card_attempts_excluded"]
    require(type(excluded) is int and excluded >= 0 and encoded(result["audit"]) == encoded({**counts(rows),
            "no_card_attempts_excluded": excluded}), "invalid audit counts")
    return calculated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    private = modes.add_parser("private", help="Use the trusted frozen worker plan after closeout")
    private.add_argument("work", type=Path)
    private.add_argument("--through", required=True, type=int)
    private.add_argument("--export", action="store_true", help="Emit sanitized receipts and analysis instead of counts")
    public = modes.add_parser("public", help="Recompute stage attribution without worker or device access")
    public.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "public":
            analysis = check_public(args.result)
            result = {"public_analysis_matches": True, "measured_formal": analysis["measured_formal"]}
        else:
            bundle = audit(args.work, args.through)
            result = bundle if args.export else {"private_receipts_match": True, **bundle["audit"]}
        print(json.dumps(result, indent=2, allow_nan=False))
    except Exception:
        sys.exit("Closed diagnostic evidence audit failed; no record exported.")
