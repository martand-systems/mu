//go:build faults

package main

import (
	"os"
	"strings"
	"sync"
	"time"
)

// Built only with -tags faults (tests and the S4 replica's must-fail runs). MU_RELAY_FAULT is a comma-separated list
// of the faults in docs/design/pairing.md §6.10; each switches off one protection so that its test can be seen to fail.
var faultSet = func() map[string]bool {
	m := map[string]bool{}
	for _, f := range strings.Split(os.Getenv("MU_RELAY_FAULT"), ",") {
		if f = strings.TrimSpace(f); f != "" {
			m[f] = true
		}
	}
	return m
}()

const faultsBuilt = true

// With the fault "from-file" the active faults are also read from <state dir>/test-fault (comma-separated, re-read at
// most once a second), so that a test can switch them while the relay runs (tools/test-jobs/e2e.sh, the Q rows).
var (
	faultFileMu   sync.Mutex
	faultFilePath = "/var/lib/mu-relay/test-fault"
	faultFileAt   time.Time
	faultFileSet  map[string]bool
)

func fault(name string) bool {
	if faultSet[name] {
		return true
	}
	if !faultSet["from-file"] {
		return false
	}
	faultFileMu.Lock()
	defer faultFileMu.Unlock()
	if time.Since(faultFileAt) > time.Second {
		faultFileAt = time.Now()
		faultFileSet = map[string]bool{}
		if b, err := os.ReadFile(faultFilePath); err == nil {
			for _, f := range strings.Split(strings.ReplaceAll(string(b), "\n", ","), ",") {
				if f = strings.TrimSpace(f); f != "" {
					faultFileSet[f] = true
				}
			}
		}
	}
	return faultFileSet[name]
}

// setFaults replaces the active faults (tests only).
func setFaults(names ...string) {
	faultSet = map[string]bool{}
	for _, n := range names {
		faultSet[n] = true
	}
}
