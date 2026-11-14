package main

// The store on the named volume (docs/design/pairing.md §6.6): state.json, state.json.prev, events.log.

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const storeFormat = 1

type State struct {
	Format     int                   `json:"format"`
	StoreID    string                `json:"store_id"`
	Created    int64                 `json:"created"`
	Generation int64                 `json:"generation"`
	Clients    map[string]*DynClient `json:"clients"`
	Grants     map[string][]int      `json:"grants"`
	Tombstones map[string]*Tombstone `json:"tombstones"`
	Invites    map[string]*Invite    `json:"invites"`
}

type DynClient struct {
	Role     string `json:"role"`
	Password string `json:"password"`
	Ports    []int  `json:"ports"`
	Created  int64  `json:"created"`
	Via      string `json:"via"`
}

type Tombstone struct {
	Removed int64 `json:"removed"`
}

type ClaimRec struct {
	At      int64  `json:"at"`
	From    string `json:"from"`
	Outcome string `json:"outcome"` // first, resumed, refused
}

type EventRec struct {
	At    int64  `json:"at"`
	Event string `json:"event"`
	From  string `json:"from,omitempty"`
}

type Invite struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Name      string `json:"name"`
	Port      int    `json:"port,omitempty"`
	Ports     []int  `json:"ports,omitempty"`
	Inviter   string `json:"inviter"`
	Replace   bool   `json:"replace"`
	State     string `json:"state"`
	Contested bool   `json:"contested"`
	Accepted  bool   `json:"accepted"`
	Client    string `json:"client"` // none, pending, created, removed

	Created   int64 `json:"created"`
	Expires   int64 `json:"expires"`
	AnswerBy  int64 `json:"answer_by"`
	ConfirmBy int64 `json:"confirm_by"`
	EndedAt   int64 `json:"ended_at,omitempty"`
	Version   int64 `json:"version"`

	ClaimHash   string     `json:"claim_hash"`
	SessionHash string     `json:"session_hash,omitempty"`
	WrongClaims int        `json:"wrong_claims"`
	Claims      []ClaimRec `json:"claims"`
	Events      []EventRec `json:"events"`

	Offer      map[string]string `json:"offer"`
	OfferMAC   string            `json:"offer_mac"`
	Answer     map[string]string `json:"answer,omitempty"`
	AnswerMAC  string            `json:"answer_mac,omitempty"`
	AnswerPass string            `json:"answer_pass_hash,omitempty"` // SHA-256 of the password, to recognise a retry
	Pending    string            `json:"pending_password,omitempty"`
	Confirm    map[string]string `json:"confirm,omitempty"`
	ConfirmMAC string            `json:"confirm_mac,omitempty"`
	Contest    map[string]string `json:"contest,omitempty"`
	ContestMAC string            `json:"contest_mac,omitempty"`

	// What the answer (or accept) changed, so that an abort or a contest can undo exactly that.
	Replaced     *DynClient `json:"replaced,omitempty"`      // the dynamic client it replaced
	ReplacedNone bool       `json:"replaced_none,omitempty"` // it replaced nothing dynamic (a new name, or a static one)
	GrantedPorts []int      `json:"granted_ports,omitempty"` // ports it added to the inviter
}

func newState(now int64) *State {
	b := make([]byte, 8)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return &State{Format: storeFormat, StoreID: hex.EncodeToString(b), Created: now,
		Clients: map[string]*DynClient{}, Grants: map[string][]int{}, Tombstones: map[string]*Tombstone{},
		Invites: map[string]*Invite{}}
}

func (s *State) fill() {
	if s.Clients == nil {
		s.Clients = map[string]*DynClient{}
	}
	if s.Grants == nil {
		s.Grants = map[string][]int{}
	}
	if s.Tombstones == nil {
		s.Tombstones = map[string]*Tombstone{}
	}
	if s.Invites == nil {
		s.Invites = map[string]*Invite{}
	}
}

func (s *State) clone() *State {
	b, err := json.Marshal(s)
	if err != nil {
		panic(err)
	}
	var c State
	if err := json.Unmarshal(b, &c); err != nil {
		panic(err)
	}
	c.fill()
	return &c
}

func parseState(b []byte) (*State, error) {
	var s State
	if err := decodeStrict(b, &s); err != nil {
		return nil, err
	}
	if s.Format != storeFormat || len(s.StoreID) != 16 {
		return nil, fmt.Errorf("format %d, store id %q", s.Format, s.StoreID)
	}
	s.fill()
	return &s, nil
}

type Store struct {
	dir string
}

func (st *Store) path(name string) string { return filepath.Join(st.dir, name) }

// writable checks that the directory exists and that this process can create files in it.
func (st *Store) writable() error {
	fi, err := os.Stat(st.dir)
	if err != nil {
		return err
	}
	if !fi.IsDir() {
		return errors.New("not a directory")
	}
	f, err := os.CreateTemp(st.dir, ".probe-*")
	if err != nil {
		return err
	}
	name := f.Name()
	f.Close()
	return os.Remove(name)
}

// load: state.json, else state.json.prev. (nil, nil) when neither exists: a new store.
func (st *Store) load() (*State, string, error) {
	var errs []string
	for _, name := range []string{"state.json", "state.json.prev"} {
		b, err := os.ReadFile(st.path(name))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			errs = append(errs, name+": "+err.Error())
			continue
		}
		s, err := parseState(b)
		if err != nil {
			errs = append(errs, name+": "+err.Error())
			continue
		}
		return s, name, nil
	}
	if len(errs) > 0 {
		return nil, "", fmt.Errorf("store damaged: %v", errs)
	}
	return nil, "", nil
}

// save writes a new generation so that state.json exists at every point (review finding 11): the new file is
// written and synced aside, the current one is hard-linked to .prev, then the new one is renamed into place.
func (st *Store) save(s *State) error {
	b, err := json.MarshalIndent(s, "", " ")
	if err != nil {
		return err
	}
	tmp := st.path("state.json.tmp")
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(append(b, '\n')); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if !fault("no-prev") {
		cur := st.path("state.json")
		if _, err := os.Stat(cur); err == nil {
			ptmp := st.path("state.json.prev.tmp")
			os.Remove(ptmp)
			if err := os.Link(cur, ptmp); err != nil {
				return err
			}
			if err := os.Rename(ptmp, st.path("state.json.prev")); err != nil {
				return err
			}
		}
	} else {
		os.Remove(st.path("state.json.prev"))
	}
	if err := os.Rename(tmp, st.path("state.json")); err != nil {
		return err
	}
	if d, err := os.Open(st.dir); err == nil {
		d.Sync()
		d.Close()
	}
	return nil
}

// moveAside renames both files to state.json.damaged-<time>[.prev] (POST /store/reset).
func (st *Store) moveAside(now time.Time) {
	suffix := now.UTC().Format("20060102T150405Z")
	os.Rename(st.path("state.json"), st.path("state.json.damaged-"+suffix))
	os.Rename(st.path("state.json.prev"), st.path("state.json.damaged-"+suffix+".prev"))
}

const eventLogMax = 1 << 20
const eventLogKeep = 512 << 10

// appendEvent adds one line to events.log; it is cut to its last 512 KiB when it passes 1 MiB.
func (st *Store) appendEvent(line []byte) {
	p := st.path("events.log")
	f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return
	}
	f.Write(append(line, '\n'))
	fi, _ := f.Stat()
	f.Close()
	if fi != nil && fi.Size() > eventLogMax {
		b, err := os.ReadFile(p)
		if err != nil || len(b) <= eventLogKeep {
			return
		}
		b = b[len(b)-eventLogKeep:]
		for i, c := range b {
			if c == '\n' {
				b = b[i+1:]
				break
			}
		}
		tmp := p + ".tmp"
		if os.WriteFile(tmp, b, 0o600) == nil {
			os.Rename(tmp, p)
		}
	}
}

func (st *Store) readEvents(n int) []json.RawMessage {
	b, err := os.ReadFile(st.path("events.log"))
	if err != nil {
		return nil
	}
	var lines [][]byte
	start := 0
	for i, c := range b {
		if c == '\n' {
			if i > start {
				lines = append(lines, b[start:i])
			}
			start = i + 1
		}
	}
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	out := make([]json.RawMessage, 0, len(lines))
	for _, l := range lines {
		if json.Valid(l) {
			out = append(out, json.RawMessage(l))
		}
	}
	return out
}
