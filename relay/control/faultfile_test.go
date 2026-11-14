//go:build faults

package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// The e2e run (tools/test-jobs/e2e.sh) switches faults while the relay runs: MU_RELAY_FAULT=from-file reads them from
// a file, at most once a second.
func TestFaultFromFile(t *testing.T) {
	old, oldPath := faultSet, faultFilePath
	defer func() { faultSet, faultFilePath, faultFileAt = old, oldPath, time.Time{} }()
	faultFilePath = filepath.Join(t.TempDir(), "test-fault")
	setFaults("from-file")
	faultFileAt = time.Time{}
	if fault("tamper-offer") {
		t.Fatal("no file: no fault")
	}
	if err := os.WriteFile(faultFilePath, []byte("tamper-offer\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	faultFileAt = time.Time{}
	if !fault("tamper-offer") || fault("tamper-answer") {
		t.Fatal("the file's fault is on, and only it")
	}
	setFaults()
	if fault("tamper-offer") {
		t.Fatal("without from-file the file is not read")
	}
}
