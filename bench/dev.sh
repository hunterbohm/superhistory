#!/usr/bin/env bash
# One labelled pass of the check over the three dev halves at once: month A, LongMemEval S dev and LoCoMo dev.
#
#     bench/dev.sh LABEL [--slice]
#
# --slice runs the stratified slices (LongMemEval 100, LoCoMo 150) instead of the full dev halves; the month runs
# whole either way. BENCH_PYTHON names a Python with LoCoMo's scorer packages (regex, numpy, nltk).
set -uo pipefail
label=${1:?usage: bench/dev.sh LABEL [--slice]}
here=$(cd "$(dirname "$0")" && pwd)
py=${BENCH_PYTHON:-python3}
logs=${BENCH_LOGS:-$HOME/.cache/kb-bench/logs/hillclimb}
mkdir -p "$logs"
lme=() loc=()
if [[ ${2:-} == --slice ]]; then lme=(--slice 100) loc=(--slice 150); fi
"$py" "$here/run.py" month --month a --label "$label" --tools check --workers 6 >"$logs/$label-month.log" 2>&1 &
"$py" "$here/public/longmemeval.py" --set s --split dev --label "$label" "${lme[@]}" --workers 8 \
  >"$logs/$label-longmemeval.log" 2>&1 &
"$py" "$here/public/locomo.py" --split dev --label "$label" "${loc[@]}" --workers 8 >"$logs/$label-locomo.log" 2>&1 &
status=0
for job in $(jobs -p); do wait "$job" || status=1; done
tail -n 4 "$logs/$label"-*.log
exit $status
