"""Audit an explicit prefix of closed visits; never dispatch input, use a lease or modify a receipt."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def read_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def audit(work, through):
    host = read_module("jpeg_closed_host", work / "plan" / "host.py")
    common = host.common
    need = common.need
    common.private_directory(work)
    plan_dir = common.private_directory(work / "plan")
    plan = host.checked_plan(work, host.read_json(plan_dir / "plan.json")["owner"], payload=False)
    need(plan.get("origin") == "stock-tap", "not_a_stock_card_plan")
    visits = common.private_directory(work / "visits")
    issued = common.private_directory(work / "receipts")
    need(1 <= through <= len(host.analyze.SCHEDULE), "invalid_prefix")
    receipts, labels, tokens = [], set(), set()
    closures = unrelated = 0
    for slot in range(1, through + 1):
        outer = host.read_json(issued / f"{slot:02}.json")
        host.analyze.validate(outer, plan["cohort"], slot)
        card = None
        for attempt in sorted(visits.glob(f"{slot:02}-*")):
            common.private_directory(attempt)
            label = attempt.name.partition("-")[2]
            visit = host.binding(plan["cohort"], plan["owner"], slot, label)
            need(label not in labels and host.read_json(attempt / "attempt.json") == visit, "unbound_attempt")
            labels.add(label)
            report = host.read_json(attempt / "operations.json")
            if host.untouched_collision(report):
                need(not (attempt / "card-intent.json").exists() and not (attempt / "case.tar.gz").exists(),
                     "collision_with_card_evidence")
                unrelated += 1
                continue
            operations = host.verify_return(visit, report)
            token = report["identity"]["lease_token_sha256"]
            need(token not in tokens, "reused_lease_identity")
            tokens.add(token)
            if not host.reached_visit(attempt, visit, operations, report):
                need(not (attempt / "case.tar.gz").exists(), "archive_without_card_intent")
                unrelated += 1
                continue
            need(card is None, "multiple_card_attempts_in_slot")
            card = (attempt, visit, report, operations)
        need(card is not None, "missing_card_attempt")
        attempt, visit, report, operations = card
        case = visit["case"]
        names = [step["operation"] for step in operations]
        expected = ["guard", "admit", "prepare", "visit:" + case]
        entered = names[:4] == expected and all(step.get("outcome") == "ok" for step in operations[:4])
        completed = (entered and names == expected + ["collect:" + case, "close", "absent"]
                     and all(step.get("outcome") == "ok" for step in operations)
                     and report.get("completed") == [case] and report.get("operations_passed") is True)
        interval = None
        if (attempt / "case.tar.gz").exists():
            observed = host.extract_record(common.read_small(attempt / "case.tar.gz", host.rendered.MAX_ARCHIVE), case)
            if completed:
                interval = observed
        closeout = {"ordinary_exit": entered, "native_restored": report.get("close_confirmed") is True,
                    "close": report.get("close_confirmed") is True, "absent": report.get("absence_confirmed") is True,
                    "connections_closed": report["release"]["native_restored_and_connections_closed"],
                    "owner_and_token_checked": report["release"]["owner_and_token_checked"], "released": report["released"]}
        rebuilt = {"cohort": plan["cohort"], "slot": slot, "arm": visit["arm"], "interval": interval,
                   "closeout": closeout, "result": "measured" if interval is not None else "not_measured"}
        need(encoded(rebuilt) == encoded(outer), "public_slot_differs_from_private_evidence")
        receipts.append(rebuilt)
        closures += 1
    report = host.analyze.summarize(receipts, plan["cohort"])
    return {"study": "Ordinary Kindle Scribe stock-card release to first submitted Library; JPEG capability admission",
            "receipts": receipts, "analysis": report,
            "audit": {"closed_card_attempts": closures, "no_card_attempts_excluded": unrelated,
                      "replayed_submission_archives": sum(receipt["result"] == "measured" for receipt in receipts)}}


def check_public(path):
    analyze = read_module("jpeg_public_analysis", Path(__file__).with_name("analyze.py"))
    bundle = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=analyze.unique_keys)
    receipts = bundle["receipts"]
    require = analyze.require
    require(isinstance(receipts, list) and 1 <= len(receipts) <= len(analyze.SCHEDULE), "invalid public receipts")
    report = analyze.summarize(receipts, receipts[0]["cohort"])
    require(encoded(report) == encoded(bundle["analysis"]), "analysis differs from public receipts")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_subparsers(dest="mode", required=True)
    private = mode.add_parser("private", help="Recalculate a published prefix from the trusted frozen worker plan")
    private.add_argument("work", type=Path)
    private.add_argument("--through", type=int, required=True)
    private.add_argument("--export", action="store_true", help="Print the sanitized receipts and analysis instead of counts")
    public = mode.add_parser("public", help="Recompute the published paired analysis without the private worker")
    public.add_argument("result", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "public":
            result = check_public(args.result)
            output = {"public_analysis_matches": True, "receipts": result["receipt_count"]}
        else:
            result = audit(args.work, args.through)
            output = result if args.export else {"private_receipts_match": True, **result["audit"]}
        print(json.dumps(output, indent=2, allow_nan=False))
    except Exception:
        sys.exit("Closed evidence audit failed; no timings exported.")
