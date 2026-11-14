package main

// chisel as mu-relayd's child (docs/design/pairing.md §6.7, §6.8). chisel 1.12.0 keeps a removed user's established
// tunnels and reverse listeners, so a removal is enforced by restarting it ("eviction").

import (
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"
)

type Supervisor struct {
	Path        string
	Args        []string
	Env         []string
	MinInterval time.Duration // at most one restart per this long (10 s)
	StopWait    time.Duration // SIGTERM, then SIGKILL after this long (3 s)
	Logf        func(format string, a ...any)
	OnExit      func(err error) // chisel exited without being asked to

	mu        sync.Mutex
	cmd       *exec.Cmd
	done      chan struct{}
	lastStart time.Time
	timer     *time.Timer
	pending   []string
	stopping  bool
	restarts  int
}

func (s *Supervisor) Start() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.startLocked()
}

func (s *Supervisor) startLocked() error {
	cmd := exec.Command(s.Path, s.Args...)
	cmd.Env = s.Env
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	done := make(chan struct{})
	s.cmd, s.done, s.lastStart = cmd, done, time.Now()
	go func() {
		err := cmd.Wait()
		close(done)
		s.mu.Lock()
		expected := s.stopping || s.cmd != cmd
		s.mu.Unlock()
		if !expected && s.OnExit != nil {
			s.OnExit(err)
		}
	}()
	return nil
}

// Running reports whether chisel is up (GET /health).
func (s *Supervisor) Running() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cmd == nil || s.done == nil {
		return false
	}
	select {
	case <-s.done:
		return false
	default:
		return true
	}
}

// Restarts counts the evictions done (tests).
func (s *Supervisor) Restarts() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.restarts
}

// Evict restarts chisel now, or as soon as MinInterval has passed since the last start; requests in between are
// batched into that one restart. It returns when the restart happened or was scheduled.
func (s *Supervisor) Evict(reason string) {
	if fault("no-evict") {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stopping || s.cmd == nil {
		return
	}
	s.pending = append(s.pending, reason)
	if s.timer != nil {
		return // already scheduled
	}
	wait := time.Until(s.lastStart.Add(s.MinInterval))
	if wait <= 0 {
		s.restartLocked()
		return
	}
	s.timer = time.AfterFunc(wait, func() {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.timer = nil
		if !s.stopping {
			s.restartLocked()
		}
	})
}

func (s *Supervisor) restartLocked() {
	reasons := s.pending
	s.pending = nil
	old, done := s.cmd, s.done
	s.cmd = nil // the exit of the old one is expected
	s.stopLocked(old, done)
	if err := s.startLocked(); err != nil {
		s.Logf("chisel did not start again: %v", err)
		if s.OnExit != nil {
			go s.OnExit(err)
		}
		return
	}
	s.restarts++
	s.Logf("chisel restarted (%s)", joinReasons(reasons))
}

func (s *Supervisor) stopLocked(cmd *exec.Cmd, done chan struct{}) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	cmd.Process.Signal(syscall.SIGTERM)
	select {
	case <-done:
	case <-time.After(s.StopWait):
		cmd.Process.Kill()
		<-done
	}
}

// Stop ends chisel for good (SIGTERM to mu-relayd).
func (s *Supervisor) Stop() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.stopping = true
	if s.timer != nil {
		s.timer.Stop()
		s.timer = nil
	}
	s.stopLocked(s.cmd, s.done)
}

func joinReasons(rs []string) string {
	out := ""
	seen := map[string]bool{}
	for _, r := range rs {
		if seen[r] {
			continue
		}
		seen[r] = true
		if out != "" {
			out += "; "
		}
		out += r
	}
	return out
}
