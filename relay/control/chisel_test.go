package main

import (
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func fakeChisel(t *testing.T, exitAtOnce bool) (*Supervisor, string, *atomic.Int32) {
	pids := filepath.Join(t.TempDir(), "pids")
	env := append(os.Environ(), "MU_FAKE_CHISEL="+pids)
	if exitAtOnce {
		env = append(env, "MU_FAKE_CHISEL_EXIT=1")
	}
	exits := &atomic.Int32{}
	s := &Supervisor{Path: os.Args[0], Args: []string{"-test.run=^$"}, Env: env, MinInterval: 400 * time.Millisecond, Delay: 50 * time.Millisecond,
		StopWait: 2 * time.Second, Logf: t.Logf, OnExit: func(error) { exits.Add(1) }}
	return s, pids, exits
}

func countPids(p string) int {
	b, _ := os.ReadFile(p)
	return strings.Count(string(b), "\n")
}

func waitFor(d time.Duration, f func() bool) bool {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		if f() {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return f()
}

func TestEvictionRestartsChiselOnceAndBatches(t *testing.T) {
	s, pids, exits := fakeChisel(t, false)
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	defer s.Stop()
	if !waitFor(3*time.Second, func() bool { return countPids(pids) == 1 }) {
		t.Fatal("chisel did not start")
	}
	s.Evict("a removed") // the first one after Delay
	if !waitFor(3*time.Second, func() bool { return countPids(pids) == 2 }) {
		t.Fatalf("no restart after an eviction: %d starts", countPids(pids))
	}
	// two more within the interval: one restart, after the interval
	s.Evict("b removed")
	s.Evict("c removed")
	time.Sleep(100 * time.Millisecond)
	if countPids(pids) != 2 {
		t.Fatal("restarted again inside the interval")
	}
	if !waitFor(3*time.Second, func() bool { return countPids(pids) == 3 }) {
		t.Fatalf("the batched eviction did not happen: %d starts", countPids(pids))
	}
	time.Sleep(600 * time.Millisecond)
	if countPids(pids) != 3 || s.Restarts() != 2 {
		t.Fatalf("starts %d, restarts %d: want 3 and 2", countPids(pids), s.Restarts())
	}
	if exits.Load() != 0 {
		t.Fatal("an eviction counted as chisel dying")
	}
	if !s.Running() {
		t.Fatal("not running after the restarts")
	}
}

func TestChiselDyingIsReported(t *testing.T) {
	s, _, exits := fakeChisel(t, true)
	if err := s.Start(); err != nil {
		t.Fatal(err)
	}
	if !waitFor(3*time.Second, func() bool { return exits.Load() == 1 }) {
		t.Fatal("chisel's own exit was not reported")
	}
	if s.Running() {
		t.Fatal("Running after chisel exited")
	}
}
