#!/usr/bin/env bash
set -euo pipefail

R2_LAUNCHER=${1:-/root/aidoku-all-build-20261119/artifacts/final-v2/launch.sh}
CANDIDATE_LAUNCHER=${2:-/root/aidoku-all-bench-20261119/launcher-r3/pair/candidate.sh}
FIXTURE_ROOT=${3:-/root/aidoku-all-bench-20261119/launcher-r3/profile-fixture}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR="$FIXTURE_ROOT/runs/$RUN_ID"
CASES_DIR="$RUN_DIR/cases"
RESULTS_TSV="$RUN_DIR/results.tsv"
REPORT_JSON="$RUN_DIR/report.json"
SUMMARY_TXT="$RUN_DIR/summary.txt"
TMP_ROOT=$(mktemp -d /tmp/aidoku-profile-parity.XXXXXX)

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

mkdir -p "$CASES_DIR"
: >"$RESULTS_TSV"

if [[ ! -x "$R2_LAUNCHER" ]]; then
  echo "Missing executable R2 launcher: $R2_LAUNCHER" >&2
  exit 2
fi
if [[ ! -x "$CANDIDATE_LAUNCHER" ]]; then
  echo "Missing executable candidate launcher: $CANDIDATE_LAUNCHER" >&2
  exit 2
fi

REGULAR_USER=nobody
if ! id "$REGULAR_USER" >/dev/null 2>&1; then
  echo "Required regular user account '$REGULAR_USER' is unavailable." >&2
  exit 2
fi

LAUNCHER_STAGE_DIR="$TMP_ROOT/launchers"
mkdir -p "$LAUNCHER_STAGE_DIR"
chmod 755 "$TMP_ROOT" "$LAUNCHER_STAGE_DIR"

R2_STAGED="$LAUNCHER_STAGE_DIR/r2.sh"
CANDIDATE_STAGED="$LAUNCHER_STAGE_DIR/candidate.sh"
cp "$R2_LAUNCHER" "$R2_STAGED"
cp "$CANDIDATE_LAUNCHER" "$CANDIDATE_STAGED"
chmod 755 "$R2_STAGED" "$CANDIDATE_STAGED"

launchers=(r2 candidate)

run_as_user() {
  local user_name=$1
  shift
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$user_name" -- "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -u "$user_name" -- "$@"
    return
  fi
  if command -v su >/dev/null 2>&1; then
    local cmd_text=''
    printf -v cmd_text '%q ' "$@"
    su -s /bin/sh "$user_name" -c "$cmd_text"
    return
  fi
  echo "No tool available to execute as regular user." >&2
  return 127
}

launcher_path_for() {
  case "$1" in
    r2) printf '%s\n' "$R2_STAGED" ;;
    candidate) printf '%s\n' "$CANDIDATE_STAGED" ;;
    *) return 1 ;;
  esac
}

make_profile_case() {
  local case_root=$1
  local case_name=$2
  local data_dir="$case_root/home/data"
  local profile_path="$data_dir/device-profile"

  case "$case_name" in
    profile_absent_default)
      ;;
    profile_valid_pw5)
      printf 'pw5\n' >"$profile_path"
      ;;
    profile_valid_pw6)
      printf 'pw6\n' >"$profile_path"
      ;;
    profile_valid_scribe)
      printf 'scribe\n' >"$profile_path"
      ;;
    profile_valid_scribe2)
      printf 'scribe2\n' >"$profile_path"
      ;;
    profile_valid_scribe3)
      printf 'scribe3\n' >"$profile_path"
      ;;
    profile_valid_scribe3_nofrontlight)
      printf 'scribe3-nofrontlight\n' >"$profile_path"
      ;;
    profile_valid_pw6_nolf)
      printf 'pw6' >"$profile_path"
      ;;
    profile_valid_pw6_crlf_extra)
      printf 'pw6\r\nignored\n' >"$profile_path"
      ;;
    profile_valid_pw6_cr_only_no_lf)
      printf 'pw6\r' >"$profile_path"
      ;;
    profile_empty)
      : >"$profile_path"
      ;;
    profile_invalid_firstline_second_valid)
      printf 'unknown\npw6\n' >"$profile_path"
      ;;
    profile_invalid_trailing_space)
      printf 'pw6 \n' >"$profile_path"
      ;;
    profile_path_is_directory)
      rm -f "$profile_path"
      mkdir -p "$profile_path"
      ;;
    profile_with_nul)
      printf 'pw6\0suffix\n' >"$profile_path"
      ;;
    profile_cr_nul)
      printf 'pw6\r\0\n' >"$profile_path"
      ;;
    profile_nul_cr)
      printf 'pw6\0\r\n' >"$profile_path"
      ;;
    profile_double_cr)
      printf 'pw6\r\r\n' >"$profile_path"
      ;;
    profile_backslash)
      printf 'pw6\\\n' >"$profile_path"
      ;;
    profile_symlink_valid)
      printf 'pw6\n' >"$data_dir/profile-target"
      ln -s "$data_dir/profile-target" "$profile_path"
      ;;
    profile_valid_unreadable_root_chmod000)
      printf 'pw6\n' >"$profile_path"
      chmod 000 "$profile_path"
      ;;
    profile_unreadable_regular_user)
      printf 'pw6\n' >"$profile_path"
      chown root:root "$profile_path"
      chmod 600 "$profile_path"
      ;;
    *)
      echo "Unknown case: $case_name" >&2
      exit 3
      ;;
  esac
}

run_case_with_launcher() {
  local case_name=$1
  local launcher_name=$2
  local launcher_path=$3

  local report_root="$CASES_DIR/${case_name}__${launcher_name}"
  local exec_root="$report_root"
  local run_mode=root
  if [[ "$case_name" == profile_unreadable_regular_user ]]; then
    run_mode=regular_user
    exec_root="$TMP_ROOT/regular-cases/${case_name}__${launcher_name}"
  fi

  local home_dir="$exec_root/home"
  local app_dir="$home_dir/app"
  local data_dir="$home_dir/data"
  local lock_dir="$exec_root/preexisting-display-lock"
  local stderr_file="$exec_root/stderr.txt"
  local stdout_file="$exec_root/stdout.txt"

  mkdir -p "$app_dir" "$data_dir"
  printf '%s\n' '#!/bin/sh' 'exit 0' >"$app_dir/aidoku"
  chmod 755 "$app_dir/aidoku"

  make_profile_case "$exec_root" "$case_name"

  mkdir -p "$lock_dir"

  if [[ "$run_mode" == regular_user ]]; then
    chmod 755 "$exec_root" "$home_dir" "$app_dir"
    chmod 777 "$data_dir" "$lock_dir"
  fi

  local rc=0
  set +e
  if [[ "$run_mode" == regular_user ]]; then
    run_as_user "$REGULAR_USER" env \
      AIDOKU_HOME="$home_dir" \
      AIDOKU_FRAMEBUFFER=/dev/null \
      AIDOKU_LOCK_DIR="$lock_dir" \
      sh "$launcher_path" --foreground >"$stdout_file" 2>"$stderr_file"
    rc=$?
  else
    AIDOKU_HOME="$home_dir" \
    AIDOKU_FRAMEBUFFER=/dev/null \
    AIDOKU_LOCK_DIR="$lock_dir" \
    sh "$launcher_path" --foreground >"$stdout_file" 2>"$stderr_file"
    rc=$?
  fi
  set -e

  if [[ "$exec_root" != "$report_root" ]]; then
    mkdir -p "$report_root"
    cp -a "$exec_root/." "$report_root/"
    stderr_file="$report_root/stderr.txt"
    stdout_file="$report_root/stdout.txt"
    data_dir="$report_root/home/data"
  fi

  local logs_created=0
  local imports_created=0
  [[ -d "$data_dir/logs" ]] && logs_created=1
  [[ -d "$data_dir/imports" ]] && imports_created=1

  local stderr_sha=''
  stderr_sha=$(sha256sum "$stderr_file" | awk '{print $1}')

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$case_name" "$launcher_name" "$rc" "$logs_created" "$imports_created" "$stderr_sha" "$run_mode" "$report_root" >>"$RESULTS_TSV"
}

cases=(
  profile_absent_default
  profile_valid_pw5
  profile_valid_pw6
  profile_valid_scribe
  profile_valid_scribe2
  profile_valid_scribe3
  profile_valid_scribe3_nofrontlight
  profile_valid_pw6_nolf
  profile_valid_pw6_crlf_extra
  profile_valid_pw6_cr_only_no_lf
  profile_empty
  profile_invalid_firstline_second_valid
  profile_invalid_trailing_space
  profile_path_is_directory
  profile_with_nul
  profile_cr_nul
  profile_nul_cr
  profile_double_cr
  profile_backslash
  profile_symlink_valid
  profile_valid_unreadable_root_chmod000
  profile_unreadable_regular_user
)

for case_name in "${cases[@]}"; do
  for launcher_name in "${launchers[@]}"; do
    launcher_path=$(launcher_path_for "$launcher_name")
    run_case_with_launcher "$case_name" "$launcher_name" "$launcher_path"
  done
done

python3 - "$RESULTS_TSV" "$REPORT_JSON" "$SUMMARY_TXT" "$R2_LAUNCHER" "$CANDIDATE_LAUNCHER" "$CASES_DIR" "$TMP_ROOT" "${#cases[@]}" <<'PY'
import csv
import hashlib
import json
import pathlib
import re
import sys

results_tsv = pathlib.Path(sys.argv[1])
report_json = pathlib.Path(sys.argv[2])
summary_txt = pathlib.Path(sys.argv[3])
r2_path = sys.argv[4]
candidate_path = sys.argv[5]
cases_dir = pathlib.Path(sys.argv[6])
tmp_root = pathlib.Path(sys.argv[7])
expected_case_count = int(sys.argv[8])

rows = []
with results_tsv.open('r', encoding='utf-8') as f:
    reader = csv.reader(f, delimiter='\t')
    for row in reader:
        if not row:
            continue
        case_root = pathlib.Path(row[7])
        stderr_path = case_root / 'stderr.txt'
        stdout_path = case_root / 'stdout.txt'
        raw_stderr = stderr_path.read_text(encoding='utf-8', errors='replace') if stderr_path.exists() else ''
        normalized_stderr = raw_stderr
        normalized_stderr = normalized_stderr.replace(str(cases_dir), '<CASES_DIR>')
        normalized_stderr = normalized_stderr.replace(str(tmp_root), '<TMP_ROOT>')
        normalized_stderr = re.sub(r'(__)(r2|candidate)', r'__LAUNCHER', normalized_stderr)
        rows.append(
            {
                'case': row[0],
                'launcher': row[1],
                'exit_code': int(row[2]),
                'logs_created': row[3] == '1',
                'imports_created': row[4] == '1',
                'stderr_sha256': row[5],
                'run_mode': row[6],
                'case_root': str(case_root),
                'stderr_path': str(stderr_path),
                'stdout_path': str(stdout_path),
                'stderr_text': raw_stderr,
                'stderr_normalized': normalized_stderr,
                'stderr_normalized_sha256': hashlib.sha256(normalized_stderr.encode('utf-8')).hexdigest(),
            }
        )

expected_count = expected_case_count * 2
missing_items = []
if len(rows) != expected_count:
    missing_items.append(f'Row count mismatch: got {len(rows)}, expected {expected_count}.')

by_case = {}
for row in rows:
    by_case.setdefault(row['case'], {})[row['launcher']] = row

for case_name in sorted(by_case):
    for launcher in ('r2', 'candidate'):
        if launcher not in by_case[case_name]:
            missing_items.append(f'Missing launcher result for case={case_name}, launcher={launcher}.')

if len(by_case) != expected_case_count:
    missing_items.append(f'Case count mismatch: got {len(by_case)}, expected {expected_case_count}.')

parity = []
mismatches = []
expected_by_case = {}
for case_name in sorted(by_case):
    entry = by_case[case_name]
    if 'r2' not in entry:
        continue
    expected_by_case[case_name] = {
        'exit_code': entry['r2']['exit_code'],
        'stderr_normalized': entry['r2']['stderr_normalized'],
        'logs_created': entry['r2']['logs_created'],
        'imports_created': entry['r2']['imports_created'],
        'run_mode': entry['r2']['run_mode'],
    }
    if 'candidate' not in entry:
        continue
    r2 = entry['r2']
    candidate = entry['candidate']
    comparison = {
        'case': case_name,
        'exit_code_match': r2['exit_code'] == candidate['exit_code'],
        'logs_created_match': r2['logs_created'] == candidate['logs_created'],
        'imports_created_match': r2['imports_created'] == candidate['imports_created'],
        'run_mode_match': r2['run_mode'] == candidate['run_mode'],
        'stderr_normalized_match': r2['stderr_normalized'] == candidate['stderr_normalized'],
        'expected_from_r2': expected_by_case[case_name],
        'r2': r2,
        'candidate': candidate,
    }
    parity.append(comparison)
    if not (
        comparison['exit_code_match']
        and comparison['logs_created_match']
        and comparison['imports_created_match']
        and comparison['run_mode_match']
        and comparison['stderr_normalized_match']
    ):
        mismatches.append(comparison)

status = 'pass'
if missing_items or mismatches:
    status = 'fail'

report = {
    'status': status,
    'launchers': {
        'r2': r2_path,
        'candidate': candidate_path,
    },
    'expected_case_count': expected_case_count,
    'expected_count': expected_count,
    'results_count': len(rows),
    'missing_items': missing_items,
    'mismatch_count': len(mismatches),
    'mismatches': mismatches,
    'expected_by_case': expected_by_case,
    'results': rows,
    'parity': parity,
}

report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')

lines = []
lines.append(f"status: {status}")
lines.append(f"results_count: {len(rows)}")
lines.append(f"expected_count: {expected_count}")
lines.append(f"expected_case_count: {expected_case_count}")
lines.append(f"missing_items: {len(missing_items)}")
lines.append(f"mismatch_count: {len(mismatches)}")
for item in missing_items:
    lines.append(f"- missing: {item}")
for mismatch in mismatches:
    lines.append(f"- mismatch case={mismatch['case']}")
summary_txt.write_text('\n'.join(lines) + '\n', encoding='utf-8')

print(report_json)
print(summary_txt)
print(status)
if status != 'pass':
    sys.exit(1)
PY

echo "$RUN_DIR"
