#!/bin/bash
# Resume the redeemer across transient stops (e.g. a stray click that desyncs the
# UI). Gives up only when a restart makes no progress at all, so a genuine block
# cannot spin forever.
set -u
cd "$(dirname "$0")" || { echo "ABORT: cannot enter script directory"; exit 1; }

LOG="${PACKRAT_LOG:-$HOME/Desktop/packrat-redeemed.txt}"

pending() {
  uv run python -c "from packrat.store import CsvStore; from packrat.config import DEFAULT_CSV; print(len(CsvStore(DEFAULT_CSV).pending()))"
}

stalls=0
while :; do
  before=$(pending)
  case "$before" in
    ''|*[!0-9]*) echo "ABORT: could not read pending count (got '$before')"; exit 1 ;;
  esac
  if [ "$before" -eq 0 ]; then echo "DONE - nothing pending"; exit 0; fi

  echo "=== $(date -u +%H:%M:%SZ) pending=$before ==="
  PYTHONUNBUFFERED=1 uv run packrat run --batch 10 --log "$LOG"

  after=$(pending)
  case "$after" in
    ''|*[!0-9]*) echo "ABORT: could not read pending count (got '$after')"; exit 1 ;;
  esac
  echo "=== $(date -u +%H:%M:%SZ) pending=$after (was $before) ==="
  if [ "$after" -eq 0 ]; then echo "DONE"; exit 0; fi

  if [ "$after" -ge "$before" ]; then
    stalls=$((stalls + 1))
    echo "no progress (stall $stalls/3)"
    [ "$stalls" -ge 3 ] && { echo "ABORT: three restarts with no progress"; exit 1; }
  else
    stalls=0
  fi
  sleep 10
done
