"""Independently replay a published prefix of one closed single-package transition experiment."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


STUDY = "Ordinary Scribe card: initial full submission to complete Library, one diagnostic configuration"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def counters(views):
    return {"checked_visits": len(views), "replayed_endpoints": sum(row["interval"] is not None for row in views),
            "replayed_transition_records": sum(row["transition"] is not None for row in views)}


def read_slot(host, work, plan, slot, labels, lease_ids):
    engine, common = host.engine, host.common
    public = engine.read_json(work / "receipts" / f"{slot:02}.json")
    host.analyze.validate(public, plan["cohort"], slot)
    result, no_card = None, 0
    for path in sorted((work / "visits").glob(f"{slot:02}-*")):
        common.private_directory(path)
        label = path.name.partition("-")[2]
        visit = engine.binding(plan["cohort"], plan["owner"], slot, label)
        common.need(label not in labels and encoded(engine.read_json(path / "attempt.json")) == encoded(visit), "unbound_attempt")
        labels.add(label)
        record = engine.read_json(path / "operations.json")
        if engine.untouched_collision(record):
            common.need(not (path / "card-intent.json").exists() and not (path / "case.tar.gz").exists(), "collision_with_card")
            no_card += 1
            continue
        operations = engine.verify_return(visit, record)
        token = record["identity"]["lease_token_sha256"]
        common.need(token not in lease_ids, "reused_lease")
        lease_ids.add(token)
        if not engine.reached_visit(path, visit, operations, record):
            common.need(not (path / "case.tar.gz").exists(), "archive_without_card_intent")
            no_card += 1
            continue
        common.need(result is None, "multiple_card_visits_for_slot")
        case = visit["case"]
        order = ["guard", "admit", "prepare", "visit:" + case, "collect:" + case, "close", "absent"]
        names = [step["operation"] for step in operations]
        exited = names[:4] == order[:4] and all(step.get("outcome") == "ok" for step in operations[:4])
        complete = (names == order and all(step.get("outcome") == "ok" for step in operations)
                    and record.get("completed") == [case] and record.get("operations_passed") is True)
        interval, stages, detail = None, None, None
        if (path / "case.tar.gz").exists():
            observed = host.extract_record(common.read_small(path / "case.tar.gz", host.reader.MAX_ARCHIVE), case)
            if complete:
                interval, stages, detail = observed
        closed = {"ordinary_exit": exited, "native_restored": record.get("close_confirmed") is True,
                  "close": record.get("close_confirmed") is True, "absent": record.get("absence_confirmed") is True,
                  "connections_closed": record["release"]["native_restored_and_connections_closed"],
                  "owner_and_token_checked": record["release"]["owner_and_token_checked"], "released": record["released"]}
        result = {"cohort": plan["cohort"], "configuration": "diagnostic", "slot": slot,
                  "result": "measured" if interval is not None else "not_measured", "interval": interval,
                  "stages": stages, "transition": detail, "closeout": closed}
    common.need(result is not None and encoded(result) == encoded(public), "unverified_public_slot")
    return result, no_card


def audit(work, through):
    host = load_module("closed_transition_host", work / "plan" / "host.py")
    common = host.common
    common.private_directory(work)
    plan_dir = common.private_directory(work / "plan")
    plan = host.checked_plan(work, host.engine.read_json(plan_dir / "plan.json")["owner"], payload=False)
    common.private_directory(work / "receipts")
    common.private_directory(work / "visits")
    common.need(type(through) is int and 1 <= through <= host.analyze.SLOTS, "invalid_prefix")
    receipts, labels, lease_ids, no_card = [], set(), set(), 0
    for slot in range(1,through + 1):
        receipt, excluded = read_slot(host, work, plan, slot, labels, lease_ids)
        receipts.append(receipt)
        no_card += excluded
    return {"study": STUDY, "receipts": receipts, "analysis": host.analyze.summarize(receipts,plan["cohort"]),
            "audit": {**counters(receipts), "no_card_attempts_excluded": no_card}}


def check_public(path):
    analyze = load_module("public_transition_analysis", Path(__file__).with_name("analyze.py"))
    bundle = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=analyze.unique_keys)
    require = analyze.require
    require(set(bundle) == {"study", "receipts", "analysis", "audit"} and bundle["study"] == STUDY, "wrong experiment")
    rows = bundle["receipts"]
    require(isinstance(rows,list) and 1 <= len(rows) <= analyze.SLOTS, "invalid receipts")
    report = analyze.summarize(rows,rows[0]["cohort"])
    require(encoded(bundle["analysis"]) == encoded(report), "unverified public analysis")
    no_card = bundle["audit"]["no_card_attempts_excluded"]
    require(type(no_card) is int and no_card >= 0 and encoded(bundle["audit"]) == encoded({**counters(rows),
            "no_card_attempts_excluded": no_card}), "unverified audit counters")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    private = modes.add_parser("private", help="Use the trusted closed worker plan and an explicit issued prefix")
    private.add_argument("work", type=Path)
    private.add_argument("--through", type=int, required=True)
    private.add_argument("--export", action="store_true", help="Print the sanitized records and analysis instead of counts")
    public = modes.add_parser("public", help="Replay using the public receipts, without worker or device access")
    public.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "public":
            report = check_public(args.result)
            result = {"public_analysis_matches": True, "measured_initial_to_library": report["measured_initial_to_library"]}
        else:
            bundle = audit(args.work, args.through)
            result = bundle if args.export else {"private_receipts_match": True, **bundle["audit"]}
        print(json.dumps(result, indent=2, allow_nan=False))
    except Exception:
        sys.exit("Closed transition evidence audit failed; no timings exported.")
