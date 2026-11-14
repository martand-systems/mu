//go:build faults

package main

import (
	"os"
	"strings"
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

func fault(name string) bool { return faultSet[name] }

// setFaults replaces the active faults (tests only).
func setFaults(names ...string) {
	faultSet = map[string]bool{}
	for _, n := range names {
		faultSet[n] = true
	}
}
