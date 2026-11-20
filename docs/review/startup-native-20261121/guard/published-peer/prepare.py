"""Build a separate private guard only from an explicitly retained BiliComics rollout."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile


HERE = Path(__file__).resolve().parent
DOCUMENT = "#!/bin/sh\n# Name: BiliComics for Kindle\nset -eu\nexec sh /mnt/us/bilicomics/launch.sh --library\n"
SOURCE = "shelf-private-rgb-v1-tIiP8C4F"


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def read(path):
    need(path.resolve(strict=True) == path and path.is_file() and 0 < path.stat().st_size <= 256 * 1024, "missing_or_unsafe_receipt")
    return path.read_text()


def receipt(folder, name):
    def unique(items):
        decoded = dict(items)
        need(len(decoded) == len(items), "duplicate_receipt_field")
        return decoded
    result = json.loads(read(folder / name), object_pairs_hook=unique)
    need(isinstance(result, dict), "invalid_receipt")
    return result


def accepted(folder, confirmed_run):
    need(folder.resolve(strict=True) == folder and folder.name == confirmed_run and re.fullmatch("bili-prod-[a-f0-9]{8}", confirmed_run),
         "wrong_owner_confirmed_run")
    admission, view, click, acquired, released, final = [receipt(folder, name) for name in (
        "admission.json", "document-view.json", "click.json", "started.json", "released.json", "public-rollout.json")]
    need(admission.get("run") == view.get("run") == confirmed_run and admission.get("protocol") == "production-library-rollout-1" and
         admission.get("measured_run") == "lib-p-b4a63eed" and admission.get("profile") == "scribe" and
         admission.get("firmware") == "5.19.5" and admission.get("source_id") == SOURCE and admission.get("expected_document") == DOCUMENT,
         "different_release_selection")
    need(final.get("run") == confirmed_run and final.get("measured_source_id") == SOURCE and final.get("measured_comparison") == admission["measured_run"]
         and all(final.get(key) is True for key in ("production_installation_retained", "staging_removed", "native_restored_and_connections_closed",
             "restored_home_and_pointer")) and final.get("exact_lease_released_utc") == released.get("released_at_utc") and
         final.get("initial_selected_rows") == 0 and final.get("final_selected_rows") == 1 and
         final.get("sole_actual_document") == "/mnt/us/documents/bilicomics.sh" and final.get("casefold_observed") == view.get("doc_alias_mode"),
         "no_owner_retention_and_closeout")
    if os.path.lexists(folder / "failure.json"):
        failure = receipt(folder, "failure.json")
        smoke = receipt(folder, "recovered-smoke.json")
        need(confirmed_run == "bili-prod-f3690b72" and final.get("initial_observe_attempt_failed") is True and
             failure.get("type") == "ValueError" and failure.get("detail") == "Scoped rollout command failed (status 1); output withheld" and
             smoke.get("new_click_sent") is False and smoke.get("verified_on_original_lease") is True and
             smoke.get("original_observer_rejected_tmp_parent_alias") is True and not os.path.lexists(folder / "smoke.json"),
             "unqualified_or_unrelated_recovery")
    else:
        smoke = receipt(folder, "smoke.json")
        need(smoke.get("production_runtime_verified") is True and final.get("initial_observe_attempt_failed") is False and
             not os.path.lexists(folder / "recovered-smoke.json"), "ambiguous_runtime_proof")
    need(final.get("normal_smoke") == smoke and all(smoke.get(key) is True for key in ("ordinary_library_click", "exact_production_arguments",
         "matching_lifecycle_ready", "framebuffer_and_input_fds", "managed_exit", "no_user_data_exported")) and
         click.get("target") == "open-card" and click.get("ok") is True and click.get("buttons_clear_after") is True,
         "no_successful_normal_entry")
    for marker in ("keep-intended", "recovery-verified"):
        path = folder / marker
        need(path.is_file() and not path.is_symlink() and path.stat().st_size == 0, "missing_owner_keep_or_recovery")
    need(acquired.get("operation") == "acquire" and acquired.get("state") == "held" and
         released.get("operation") == "release" and released.get("state") == "released" and
         acquired.get("ok") is True and all(released.get(key) is True for key in ("ok", "native_restored_and_connections_closed",
             "owner_and_token_checked", "only_owned_owner_json_removed_then_rmdir", "lease_directory_absent_after_release")) and
         acquired.get("project") == released.get("project") == "BiliComics" and
         acquired.get("run_id") == released.get("run_id") == confirmed_run and isinstance(admission.get("owner"), str) and
         admission["owner"] and acquired.get("owner_thread") == released.get("owner_thread") == admission["owner"] and
         isinstance(acquired.get("lease_token_sha256"), str) and acquired["lease_token_sha256"] and
         acquired["lease_token_sha256"] == released.get("lease_token_sha256"), "not_same_owner_release")
    start = datetime.fromisoformat(acquired["acquired_at_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(released["released_at_utc"].replace("Z", "+00:00"))
    need(start.tzinfo is not None and end.tzinfo is not None and end >= start, "unordered_owner_release")
    mode, boot, volume, lower, upper = [view.get(key) for key in ("doc_alias_mode", "kindle_boot", "doc_volume", "doc_lower_id", "doc_upper_id")]
    need(all(isinstance(value, str) for value in (mode, boot, volume, lower, upper)) and
         re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot) and re.fullmatch(r"[0-9]{1,20}", volume) and
         re.fullmatch(volume + r":[1-9][0-9]{0,19}:1", lower), "invalid_document_witness")
    need((mode == "absent" and upper == "absent") or (mode == "same-inode" and upper == lower) or
         (mode == "casefold-proven" and upper != lower and re.fullmatch(volume + r":[1-9][0-9]{0,19}:1", upper)),
         "wrong_alias_mode_or_volume")
    return dict(ALIAS_MODE=mode)


def replace_once(contents, old, new):
    need(contents.count(old) == 1, "scope_changed_reinspect_guard")
    return contents.replace(old, new)


def rendered(base, fields):
    body = read(base)
    component = read(HERE / "bili_files.sh.in").rstrip("\n")
    for field, value in fields.items():
        need("@" + field + "@" in component, "missing_witness_field")
        component = component.replace("@" + field + "@", value)
    need(not re.search("@[A-Z_]+@", component), "unbound_file_guard")
    absent_loop = '''    for path in "$media"/bilicomics "$media"/documents/bilicomics.sh "$media"/documents/BiliComics.sh \\
        "$media"/documents/bilicomics.sdr "$media"/documents/BiliComics.sdr \\
        "$media"/documents/bilicomics.sh.sdr "$media"/documents/BiliComics.sh.sdr; do
        absent "$path" || return 1
    done'''
    body = replace_once(body, absent_loop, component)
    body = replace_once(body, "SELECT count(*) FROM Entries WHERE p_location COLLATE NOCASE='/mnt/us/documents/bilicomics.sh';",
                        "SELECT count(*)=1 AND max(p_location COLLATE BINARY='/mnt/us/documents/bilicomics.sh' "
                        "AND p_mimeType='text/x-shellscript' AND p_titles_0_nominal='BiliComics for Kindle') "
                        "FROM Entries WHERE p_location COLLATE NOCASE='/mnt/us/documents/bilicomics.sh';")
    body = replace_once(body, "('/mnt/us/documents/Aidoku.sh','/mnt/us/documents/weread.sh') ORDER BY p_location;",
                        "('/mnt/us/documents/Aidoku.sh','/mnt/us/documents/weread.sh','/mnt/us/documents/bilicomics.sh') ORDER BY p_location;")
    return replace_once(body, "printf '0\\n1\\n1'", "printf '1\\n1\\n1'")


def produce(base, folder, run, output_parent):
    fields = accepted(folder, run)
    body = rendered(base, fields)
    need(output_parent.resolve(strict=True) == output_parent and output_parent.is_dir() and
         not output_parent.stat().st_mode & 0o077, "use_private_output_parent")
    target = Path(tempfile.mkdtemp(prefix="frozen-bili-guard-", dir=output_parent))
    with (target / "guard.sh").open("x") as handle:
        handle.write(body)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("owner_rollout", type=Path)
    parser.add_argument("private_parent", type=Path)
    parser.add_argument("--owner-confirmed-retained", required=True, metavar="EXACT_RUN",
                        help="Use only after an explicit final retention statement from the BiliComics owner for this run.")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        target = produce(args.base, args.owner_rollout, args.owner_confirmed_retained, args.private_parent)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        message = str(exc) if type(exc) is ValueError and re.fullmatch("[a-z_]+", str(exc)) else "missing_or_malformed_evidence"
        raise SystemExit("refused " + message) from None
    print(target / "guard.sh")
