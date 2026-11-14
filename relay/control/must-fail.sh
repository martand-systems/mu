#!/usr/bin/env bash
# The test of the tests (docs/design/pairing.md §13.1): mu-relayd built with -tags faults, once per fault in
# MU_RELAY_FAULT. Each fault switches off one protection; the tests named for it must then fail. Exit 0 only if every
# fault is noticed by every test named for it, and the suite passes with no fault.
#
#   relay/control/must-fail.sh          (needs Go; with MU_RELAYD_CHISEL=<chisel 1.12.0> also the integration rows)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export GOFLAGS=-mod=readonly GOPROXY=off GOTOOLCHAIN=local
N_FAIL=0
# fault | tests that must fail
MATRIX='
allow-empty|TestAuthFileNeverEmpty TestDeleteAndLastClient
unanchored-rule|TestRuleSemantics TestRulesEqualEntrypoint
wide-ports|TestRuleSemantics TestDynamicClientChecks
override-any-role|TestRoleMismatch
no-port-check|TestCreateChecks TestDynamicClientChecks
ignore-tombstones|TestDeleteAndLastClient
ignore-revoked|TestRevokedAtStart TestAuthFileNeverEmpty
copy-static-password|TestGrantsNeverCopyPasswords
no-prev|TestStorePrevUsedWhenCurrentIsDamaged
no-reload|TestInviteNodeEndToEnd
no-evict|TestContestRemovesClient TestAbort TestReplaceSwapsAtAccept TestDeleteAndLastClient
no-lockout|TestWrongCodeSameAsUnknownAndLockout
no-expiry|TestDeadlines
no-contest|TestSessionContinuityAndContested TestContestRemovesClient
reclaim-replaces|TestSessionContinuityAndContested
replace-at-answer|TestReplaceSwapsAtAccept
xff-first|TestRateLimitAndAddress
admin-prefix|TestAdminAuth
allow-unknown|TestStrictRequests
allow-upgrade|TestUpgradeRefused
log-secrets|TestNoSecretsInLogs
tamper-offer|TestOfferAndAnswerUnchanged
tamper-answer|TestOfferAndAnswerUnchanged
evil-text|TestErrorTextIsFixed
'
go test -count=1 -tags faults ./... > /dev/null 2>&1 || { echo "FAIL  the faults build fails with no fault set"; N_FAIL=$((N_FAIL + 1)); }
while IFS='|' read -r f tests; do
  [ -n "$f" ] || continue
  out="$(MU_RELAY_FAULT="$f" go test -count=1 -tags faults -run "^($(echo "$tests" | tr ' ' '|'))\$" -v ./... 2>&1)"
  missed=""
  for t in $tests; do
    printf '%s\n' "$out" | grep -q -- "^--- FAIL: $t " || missed="$missed $t"
  done
  if [ -z "$missed" ]; then printf 'ok    %-22s fails %s\n' "$f" "$tests"
  else printf 'FAIL  %-22s did not fail:%s\n' "$f" "$missed"; N_FAIL=$((N_FAIL + 1)); fi
done <<< "$MATRIX"
# With the real chisel (MU_RELAYD_CHISEL): the integration test's rows, each with its fault.
if [ -n "${MU_RELAYD_CHISEL:-}" ]; then
  while IFS='|' read -r f rows; do
    [ -n "$f" ] || continue
    out="$(MU_RELAY_FAULT="$f" go test -count=1 -tags faults -run '^TestIntegrationWithChisel$' -v ./... 2>&1)"
    missed=""
    for row in $rows; do printf '%s\n' "$out" | grep -q "integration_test.go:[0-9]*: $row " || missed="$missed $row"; done
    if [ -z "$missed" ]; then printf 'ok    %-22s fails %s (real chisel)\n' "$f" "$rows"
    else printf 'FAIL  %-22s did not fail:%s (real chisel)\n' "$f" "$missed"; N_FAIL=$((N_FAIL + 1)); fi
  done <<'ROWS'
allow-empty|I7
no-evict|I6
no-reload|I3
wide-ports|I5
ROWS
else
  echo "(MU_RELAYD_CHISEL not set: the integration rows were not run)"
fi
echo
if [ "$N_FAIL" -eq 0 ]; then echo "every fault was noticed"; else echo "$N_FAIL fault(s) not noticed"; exit 1; fi
