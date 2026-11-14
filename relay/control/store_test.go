package main

import (
	"bufio"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestStorePersistsAcrossRestart(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	e.pairNode("7K3M", 1, "office-pc", 7001)
	id := e.r.st.StoreID
	e.start() // a redeploy: same volume
	if e.r.st.StoreID != id {
		t.Fatal("a new store after a restart")
	}
	if _, pws := e.users(); pws[pwB] != "office-pc" {
		t.Fatal("the dynamic client is gone after a restart")
	}
	if r := e.do("GET", "/mu/v1/invites/7K3M", admin(), nil); inviteField(r, "state") != "answered" {
		t.Fatalf("the invite after a restart: %s", r.Raw)
	}
}

func TestStorePrevUsedWhenCurrentIsDamaged(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	e.pairNode("7K3M", 1, "office-pc", 7001)
	e.mustStatus(e.do("PUT", "/mu/v1/clients/lab-pc", admin(), map[string]any{"role": "compute", "password": pwA, "ports": []int{7002}}), 201, "put")
	os.WriteFile(filepath.Join(e.cfg.StateDir, "state.json"), []byte("{garbage"), 0o600)
	e.start()
	if e.r.mode != modeOK {
		t.Fatalf("mode %s: %s", e.r.mode, e.r.modeErr)
	}
	if _, pws := e.users(); pws[pwB] != "office-pc" {
		t.Fatal("state.json.prev was not used")
	}
}

func TestStoreDamagedAndReset(t *testing.T) {
	e := newEnv(t, staticDefault())
	os.WriteFile(filepath.Join(e.cfg.StateDir, "state.json"), []byte("{garbage"), 0o600)
	os.WriteFile(filepath.Join(e.cfg.StateDir, "state.json.prev"), []byte("also garbage"), 0o600)
	e.start()
	if e.r.mode != modeDamaged {
		t.Fatalf("mode %s", e.r.mode)
	}
	if r := e.do("GET", "/health", "", nil); r.Status != 200 {
		t.Fatal("/health with a damaged store")
	}
	if r := e.do("GET", "/mu/v1/clients", admin(), nil); r.Status != 503 || errCode(r) != "store-damaged" {
		t.Fatalf("clients: %s", r.Raw)
	}
	if rules, _ := e.users(); rules["office-pc"] == "" || rules["avi-mac"] == "" {
		t.Fatal("static clients must keep working with a damaged store")
	}
	e.mustStatus(e.do("POST", "/mu/v1/store/reset", admin(), map[string]any{}), 200, "reset")
	e.mustStatus(e.do("GET", "/mu/v1/clients", admin(), nil), 200, "clients after reset")
	m, _ := filepath.Glob(filepath.Join(e.cfg.StateDir, "state.json.damaged-*"))
	if len(m) != 2 {
		t.Fatalf("damaged files kept aside: %v", m)
	}
}

func TestEmptyVolumeIsANewStore(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	e.pairNode("7K3M", 1, "office-pc", 7001)
	old := e.r.st.StoreID
	os.RemoveAll(e.cfg.StateDir)
	os.MkdirAll(e.cfg.StateDir, 0o700)
	e.start()
	if e.r.st.StoreID == old {
		t.Fatal("same store id on an empty volume")
	}
	if _, pws := e.users(); pws[pwB] != "" {
		t.Fatal("a dynamic client survived the empty volume")
	}
	if rules, _ := e.users(); rules["avi-mac"] == "" {
		t.Fatal("the static client did not")
	}
}

// storeCrashChild writes generations as fast as it can and prints each one after save returned (acknowledged).
func storeCrashChild() {
	st := &Store{dir: os.Getenv("MU_STORE_CRASH_CHILD")}
	s, _, err := st.load()
	if err != nil || s == nil {
		s = newState(1)
	}
	out := bufio.NewWriter(os.Stdout)
	for {
		s.Generation++
		s.Clients[fmt.Sprintf("c%d", s.Generation%50)] = &DynClient{Role: RoleCompute, Password: strings.Repeat("p", 40), Ports: []int{7001}}
		if err := st.save(s); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Fprintln(out, s.Generation)
		out.Flush()
	}
}

func TestStoreSurvivesKills(t *testing.T) {
	dir := t.TempDir()
	rng := rand.New(rand.NewSource(1))
	last := int64(0)
	for i := 0; i < 40; i++ {
		cmd := exec.Command(os.Args[0], "-test.run=^$")
		cmd.Env = append(os.Environ(), "MU_STORE_CRASH_CHILD="+dir)
		stdout, _ := cmd.StdoutPipe()
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		acked := last // what the previous child left
		sc := bufio.NewScanner(stdout)
		delay := time.Duration(20+rng.Intn(60)) * time.Millisecond
		go func() {
			time.Sleep(delay)
			cmd.Process.Signal(syscall.SIGKILL)
		}()
		for sc.Scan() {
			if n, err := strconv.ParseInt(sc.Text(), 10, 64); err == nil {
				acked = n
			}
		}
		cmd.Wait()
		s, from, err := (&Store{dir: dir}).load()
		if err == nil && s == nil && acked == 0 && last == 0 {
			continue // killed before its first write
		}
		if err != nil || s == nil {
			t.Fatalf("kill %d: the store does not load: %v", i, err)
		}
		if s.Generation < acked || s.Generation > acked+1 {
			t.Fatalf("kill %d: generation %d from %s, last acknowledged %d", i, s.Generation, from, acked)
		}
		if s.Generation < last {
			t.Fatalf("kill %d: went back from %d to %d", i, last, s.Generation)
		}
		last = s.Generation
	}
	if _, err := os.Stat(filepath.Join(dir, "state.json.prev")); err != nil {
		t.Fatalf("no state.json.prev: %v", err)
	}
}
