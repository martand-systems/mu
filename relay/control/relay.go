package main

// The relay's state: the store, the static clients, the auth file, and the one lock every change goes through.

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Evicter interface {
	Evict(reason string)
	Running() bool
}

type Config struct {
	StateDir    string
	RunDir      string
	AdminToken  string
	Revoked     map[string]bool
	Static      []Entry
	ReloadDelay time.Duration // after an auth-file write, before the request is answered (300 ms)
	Now         func() time.Time
	Logf        func(format string, a ...any)
}

const (
	modeOK         = "ok"
	modeUnwritable = "unwritable"
	modeDamaged    = "damaged"
)

type Relay struct {
	cfg      Config
	mu       sync.Mutex
	st       *State
	mode     string
	modeErr  string
	store    *Store
	sentinel string
	authPath string
	authNow  []byte
	shadowed []Shadowed
	changed  chan struct{}
	sup      Evicter
	limits   *Limits
	token    [32]byte
	polls    map[string]int
}

type apiError struct {
	Status int
	Code   string
	Msg    string
	Extra  map[string]any // more members of the answer (the offer, for a claimer refused as invite-used)
}

func (e *apiError) Error() string { return e.Code + ": " + e.Msg }

func errf(status int, code, format string, a ...any) *apiError {
	return &apiError{Status: status, Code: code, Msg: fmt.Sprintf(format, a...)}
}

var errNoChange = errors.New("no change")

func NewRelay(cfg Config, sup Evicter) (*Relay, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Logf == nil {
		cfg.Logf = func(string, ...any) {}
	}
	if cfg.Revoked == nil {
		cfg.Revoked = map[string]bool{}
	}
	r := &Relay{cfg: cfg, store: &Store{dir: cfg.StateDir}, sentinel: newSentinelPassword(),
		authPath: filepath.Join(cfg.RunDir, "users.json"), changed: make(chan struct{}), sup: sup,
		limits: newLimits(cfg.Now), token: sha256.Sum256([]byte(cfg.AdminToken)), polls: map[string]int{}}
	r.mode = modeOK
	now := r.unix()
	if err := r.store.writable(); err != nil {
		r.mode, r.modeErr = modeUnwritable, err.Error()
		r.st = newState(now)
		cfg.Logf("the state volume %s is not writable (%v): static clients only, pairing off (docs/design/pairing.md §6.6)", cfg.StateDir, err)
	} else if s, from, err := r.store.load(); err != nil {
		r.mode, r.modeErr = modeDamaged, err.Error()
		r.st = newState(now)
		cfg.Logf("%v: static clients only, pairing off until POST /mu/v1/store/reset (docs/design/pairing.md §6.6)", err)
	} else if s == nil {
		r.st = newState(now)
		if err := r.store.save(r.st); err != nil {
			r.mode, r.modeErr = modeUnwritable, err.Error()
		} else {
			r.event("", "store-created", "", "store "+r.st.StoreID)
		}
	} else {
		r.st = s
		if from != "state.json" {
			cfg.Logf("state.json could not be read; loaded %s (generation %d)", from, s.Generation)
		}
	}
	// RELAY_REVOKED: no dynamic client, grant or tombstone of those names survives a start.
	if len(cfg.Revoked) > 0 && r.mode == modeOK && !fault("ignore-revoked") {
		s := r.st.clone()
		changed := false
		for name := range cfg.Revoked {
			if _, ok := s.Clients[name]; ok {
				r.removeClientState(s, name)
				changed = true
				cfg.Logf("client %s deleted: it is in RELAY_REVOKED", name)
			}
			if _, ok := s.Grants[name]; ok {
				delete(s.Grants, name)
				changed = true
			}
			if _, ok := s.Tombstones[name]; ok {
				delete(s.Tombstones, name)
				changed = true
			}
		}
		if changed {
			s.Generation++
			if err := r.store.save(s); err != nil {
				return nil, fmt.Errorf("saving the store after RELAY_REVOKED: %w", err)
			}
			r.st = s
		}
	}
	b, sh, err := r.renderFor(r.st)
	if err != nil {
		return nil, fmt.Errorf("the auth file failed its check: %w", err)
	}
	if err := r.writeAuth(b, true); err != nil {
		return nil, err
	}
	r.shadowed = sh
	for _, x := range sh {
		cfg.Logf("static client %s left out: %s", x.Name, x.Reason)
	}
	return r, nil
}

func (r *Relay) unix() int64 { return r.cfg.Now().Unix() }

// renderFor returns the auth file for a state, checked.
func (r *Relay) renderFor(s *State) ([]byte, []Shadowed, error) {
	st := s
	if r.mode != modeOK {
		st = newState(0) // static clients only
	}
	entries, sh := merge(r.cfg.Static, st, r.cfg.Revoked)
	b := render(entries, r.sentinel)
	if err := checkAuthFile(b); err != nil {
		return nil, nil, err
	}
	return b, sh, nil
}

func (r *Relay) entries(s *State) []Entry {
	st := s
	if r.mode != modeOK {
		st = newState(0)
	}
	e, _ := merge(r.cfg.Static, st, r.cfg.Revoked)
	return e
}

// writeAuth replaces the auth file by rename (chisel watches the directory and reloads 100 ms after the last event).
func (r *Relay) writeAuth(b []byte, initial bool) error {
	if !initial && fault("no-reload") {
		return nil
	}
	tmp := r.authPath + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, r.authPath); err != nil {
		return err
	}
	r.authNow = b
	return nil
}

// txn collects what a change does besides the state: log lines and evictions.
type txn struct {
	evict  []string
	events []evt
}

type evt struct{ invite, event, from, detail string }

func (t *txn) evictFor(reason string) { t.evict = append(t.evict, reason) }
func (t *txn) log(invite, event, from, detail string) {
	t.events = append(t.events, evt{invite, event, from, detail})
}

// mutate applies f to a copy of the state. If f fails nothing changes. Otherwise the new auth file is checked, the
// copy is saved (a new generation), the auth file is replaced, chisel is restarted if f asked for it, and waiting
// long polls are woken. Called with r.mu held.
func (r *Relay) mutate(f func(s *State, t *txn) error) error {
	if r.mode != modeOK {
		if r.mode == modeDamaged {
			return errf(503, "store-damaged", "the relay's store could not be read: POST /mu/v1/store/reset, then mu relay restore")
		}
		return errf(503, "store-unwritable", "the state volume is not writable by uid 65532 (docs/design/pairing.md §7)")
	}
	s := r.st.clone()
	t := &txn{}
	if err := f(s, t); err != nil {
		if err == errNoChange {
			return nil
		}
		return err
	}
	b, sh, err := r.renderFor(s)
	if err != nil {
		r.cfg.Logf("refused a change: the auth file failed its check: %v", err)
		return errf(500, "internal", "the change would make an auth file that fails its check")
	}
	s.Generation++
	if err := r.store.save(s); err != nil {
		r.cfg.Logf("could not save the store: %v", err)
		return errf(503, "store-unwritable", "the store could not be saved")
	}
	r.st = s
	r.shadowed = sh
	authChanged := string(b) != string(r.authNow)
	if authChanged {
		if err := r.writeAuth(b, false); err != nil {
			r.cfg.Logf("could not write the auth file: %v", err)
		}
	}
	for _, e := range t.events {
		r.event(e.invite, e.event, e.from, e.detail)
	}
	if len(t.evict) > 0 && r.sup != nil && !fault("no-evict") {
		r.sup.Evict(strings.Join(t.evict, "; "))
	}
	close(r.changed)
	r.changed = make(chan struct{})
	if authChanged && r.cfg.ReloadDelay > 0 {
		// chisel debounces 100 ms; a client that connects right after this answer must find itself there.
		r.mu.Unlock()
		time.Sleep(r.cfg.ReloadDelay)
		r.mu.Lock()
	}
	return nil
}

// event writes one line to the container log and to events.log. Never a secret (§6.9).
func (r *Relay) event(invite, event, from, detail string) {
	line := "mu-relayd: "
	if invite != "" {
		line += "invite " + invite + " "
	}
	line += event
	if detail != "" {
		line += " " + detail
	}
	if from != "" {
		line += " from=" + from
	}
	r.cfg.Logf("%s", strings.TrimPrefix(line, "mu-relayd: "))
	rec := map[string]any{"at": r.unix(), "event": event}
	if invite != "" {
		rec["invite"] = invite
	}
	if from != "" {
		rec["from"] = from
	}
	if detail != "" {
		rec["detail"] = detail
	}
	if b, err := json.Marshal(rec); err == nil && r.mode == modeOK {
		r.store.appendEvent(b)
	}
}

// ---------------------------------------------------------------------------------------------------------- clients

// clientInfo is a client as the merged view sees it.
type clientInfo struct {
	Entry
	Created int64
	Via     string
}

func (r *Relay) lookup(s *State, name string) (clientInfo, bool) {
	for _, e := range r.entries(s) {
		if e.Name == name {
			ci := clientInfo{Entry: e}
			if d, ok := s.Clients[name]; ok {
				ci.Created, ci.Via = d.Created, d.Via
			}
			return ci, true
		}
	}
	return clientInfo{}, false
}

// computePortOwner: which compute client (merged) has the port, ignoring `except`.
func (r *Relay) computePortOwner(s *State, port int, except string) string {
	for _, e := range r.entries(s) {
		if e.Role == RoleCompute && e.Name != except && e.Ports[0] == port {
			return e.Name
		}
	}
	return ""
}

func (r *Relay) isStatic(name string) (Entry, bool) {
	for _, e := range r.cfg.Static {
		if e.Name == name {
			return e, true
		}
	}
	return Entry{}, false
}

// removeClientState deletes a dynamic client, and for a compute client its port from every workstation (review
// finding 11).
func (r *Relay) removeClientState(s *State, name string) {
	c, ok := s.Clients[name]
	if !ok {
		return
	}
	delete(s.Clients, name)
	if c.Role == RoleCompute && len(c.Ports) == 1 {
		r.stripPort(s, c.Ports[0])
	}
}

func (r *Relay) stripPort(s *State, port int) {
	for w, ps := range s.Grants {
		s.Grants[w] = removeInt(ps, port)
		if len(s.Grants[w]) == 0 {
			delete(s.Grants, w)
		}
	}
	for _, c := range s.Clients {
		if c.Role == RoleWorkstation && len(c.Ports) > 1 {
			c.Ports = removeInt(c.Ports, port)
		}
	}
}

func removeInt(xs []int, x int) []int {
	out := xs[:0:0]
	for _, y := range xs {
		if y != x {
			out = append(out, y)
		}
	}
	return out
}

// grantPort gives a workstation one more port: a grant for a static one, its own ports for a dynamic one.
// Reports whether anything was added.
func (r *Relay) grantPort(s *State, ws string, port int) bool {
	if d, ok := s.Clients[ws]; ok {
		if containsInt(d.Ports, port) {
			return false
		}
		d.Ports = sortedInts(append(d.Ports, port))
		return true
	}
	if e, ok := r.isStatic(ws); ok {
		if containsInt(e.Ports, port) || containsInt(s.Grants[ws], port) {
			return false
		}
		if fault("copy-static-password") { // what the design did before review finding 2
			s.Clients[ws] = &DynClient{Role: RoleWorkstation, Password: e.Password, Ports: sortedInts(append(append([]int(nil), e.Ports...), port)), Via: "copy"}
			return true
		}
		s.Grants[ws] = sortedInts(append(s.Grants[ws], port))
		return true
	}
	return false
}

func (r *Relay) ungrantPort(s *State, ws string, port int) {
	if d, ok := s.Clients[ws]; ok {
		if len(d.Ports) > 1 {
			d.Ports = removeInt(d.Ports, port)
		}
		return
	}
	if ps, ok := s.Grants[ws]; ok {
		s.Grants[ws] = removeInt(ps, port)
		if len(s.Grants[ws]) == 0 {
			delete(s.Grants, ws)
		}
	}
}

func sortedInts(xs []int) []int {
	out := append([]int(nil), xs...)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	// dedupe
	w := 0
	for i, x := range out {
		if i == 0 || x != out[w-1] {
			out[w] = x
			w++
		}
	}
	return out[:w]
}

func (r *Relay) realClientCount(s *State) int { return len(r.entries(s)) }
