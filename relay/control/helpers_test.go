package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// TestMain doubles as a fake chisel for the supervisor tests: with MU_FAKE_CHISEL set the test binary writes its pid
// to that file and waits for SIGTERM.
func TestMain(m *testing.M) {
	if p := os.Getenv("MU_FAKE_CHISEL"); p != "" {
		f, _ := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
		fmt.Fprintf(f, "%d\n", os.Getpid())
		f.Close()
		if os.Getenv("MU_FAKE_CHISEL_EXIT") == "1" {
			os.Exit(3)
		}
		c := make(chan os.Signal, 1)
		signal.Notify(c, syscall.SIGTERM)
		<-c
		os.Exit(0)
	}
	if os.Getenv("MU_STORE_CRASH_CHILD") != "" {
		storeCrashChild()
		return
	}
	os.Exit(m.Run())
}

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) Now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *fakeClock) Add(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

type fakeSup struct {
	mu      sync.Mutex
	evicted []string
}

func (f *fakeSup) Evict(reason string) {
	f.mu.Lock()
	f.evicted = append(f.evicted, reason)
	f.mu.Unlock()
}
func (f *fakeSup) Running() bool { return true }
func (f *fakeSup) count() int    { f.mu.Lock(); defer f.mu.Unlock(); return len(f.evicted) }

const testToken = "mra_0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
const pwA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const pwB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
const pwC = "cccccccccccccccccccccccccccccccc"

type env struct {
	t     *testing.T
	r     *Relay
	sup   *fakeSup
	clock *fakeClock
	srv   *httptest.Server
	logs  *bytes.Buffer
	logMu *sync.Mutex
	dir   string
	cfg   Config
}

func staticDefault() []Entry {
	return []Entry{
		{Name: "avi-mac", Role: RoleWorkstation, Password: "static-mac-password-0123456789", Ports: []int{7001}, Source: "static"},
		{Name: "office-pc", Role: RoleCompute, Password: "static-pc-password-01234567890", Ports: []int{7001}, Source: "static"},
	}
}

func newEnv(t *testing.T, static []Entry, revoked ...string) *env {
	t.Helper()
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "state"), 0o700)
	os.MkdirAll(filepath.Join(dir, "run"), 0o700)
	e := &env{t: t, sup: &fakeSup{}, clock: &fakeClock{t: time.Unix(1795000000, 0)}, logs: &bytes.Buffer{}, logMu: &sync.Mutex{}, dir: dir}
	rv := map[string]bool{}
	for _, n := range revoked {
		rv[n] = true
	}
	e.cfg = Config{StateDir: filepath.Join(dir, "state"), RunDir: filepath.Join(dir, "run"), AdminToken: testToken,
		Revoked: rv, Static: static, Now: e.clock.Now,
		Logf: func(f string, a ...any) { e.logMu.Lock(); fmt.Fprintf(e.logs, f+"\n", a...); e.logMu.Unlock() }}
	e.start()
	return e
}

// start (again): a new process on the same volume, as after a redeploy.
func (e *env) start() {
	e.t.Helper()
	if e.srv != nil {
		e.srv.Close()
	}
	r, err := NewRelay(e.cfg, e.sup)
	if err != nil {
		e.t.Fatalf("NewRelay: %v", err)
	}
	e.r = r
	e.srv = httptest.NewServer(r)
	e.t.Cleanup(e.srv.Close)
}

type resp struct {
	Status int
	Body   map[string]any
	Raw    string
	Header http.Header
}

func (e *env) do(method, path, auth string, body any, hdr ...string) resp {
	e.t.Helper()
	var rd io.Reader
	switch b := body.(type) {
	case nil:
	case string:
		rd = strings.NewReader(b)
	case []byte:
		rd = bytes.NewReader(b)
	default:
		j, _ := json.Marshal(b)
		rd = bytes.NewReader(j)
	}
	req, _ := http.NewRequest(method, e.srv.URL+path, rd)
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	for i := 0; i+1 < len(hdr); i += 2 {
		req.Header.Add(hdr[i], hdr[i+1])
	}
	if len(hdr) == 0 {
		req.Header.Set("X-Forwarded-For", "198.51.100.23, 10.0.1.2")
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatalf("%s %s: %v", method, path, err)
	}
	defer res.Body.Close()
	raw, _ := io.ReadAll(res.Body)
	out := resp{Status: res.StatusCode, Raw: string(raw), Header: res.Header}
	json.Unmarshal(raw, &out.Body)
	return out
}

func admin() string { return "Bearer " + testToken }

func (e *env) authFile() map[string][]string {
	e.t.Helper()
	b, err := os.ReadFile(filepath.Join(e.cfg.RunDir, "users.json"))
	if err != nil {
		e.t.Fatal(err)
	}
	var m map[string][]string
	if err := json.Unmarshal(b, &m); err != nil {
		e.t.Fatalf("users.json: %v\n%s", err, b)
	}
	return m
}

// users: name -> rule, password -> name, from the auth file.
func (e *env) users() (map[string]string, map[string]string) {
	rules, pws := map[string]string{}, map[string]string{}
	for k, v := range e.authFile() {
		i := strings.IndexByte(k, ':')
		rules[k[:i]] = strings.Join(v, ",")
		pws[k[i+1:]] = k[:i]
	}
	return rules, pws
}

func (e *env) logText() string {
	e.logMu.Lock()
	defer e.logMu.Unlock()
	b, _ := os.ReadFile(filepath.Join(e.cfg.StateDir, "events.log"))
	return e.logs.String() + string(b)
}

// A test claimer: the claim token, its hash and a session, as pair.py makes them.
type code struct {
	id, claim, hash, session string
}

func newCode(id string, seed byte) code {
	k := sha256.Sum256([]byte{seed, 'k'})
	h := sha256.Sum256(k[:])
	s := sha256.Sum256([]byte{seed, 's'})
	return code{id: id, claim: hex.EncodeToString(k[:]), hash: hex.EncodeToString(h[:]),
		session: base64.RawURLEncoding.EncodeToString(s[:])}
}

func mac(seed string) string {
	h := sha256.Sum256([]byte(seed))
	return hex.EncodeToString(h[:])
}

const testKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ3sAmPLeKEYforTESTSonlyQ7wq2ZrKLnN1p0Fz1oX"

func nodeInvite(c code, name string, port int) map[string]any {
	return map[string]any{
		"id": c.id, "kind": "node", "name": name, "port": port, "inviter": "avi-mac", "claim_hash": c.hash,
		"offer": map[string]string{"kind": "node", "id": c.id, "relay_url": "https://relay.martand.systems",
			"relay_fingerprint": "OEc5abcdefghijklmnopqrstuvwxyz0123456789ABC=", "node_name": name,
			"port": fmt.Sprint(port), "inviter_name": "avi-mac", "inviter_key": testKey,
			"created": "1795000000", "expires": "1795001800", "mu_version": "v2026.11.8"},
		"offer_mac": mac("offer " + c.id), "ttl_s": 1800,
	}
}

func answerFor(c code, name string) map[string]any {
	return map[string]any{
		"answer": map[string]string{"kind": "node", "id": c.id, "offer_digest": mac("digest"), "node_name": name,
			"host_key": testKey, "mu_version": "v2026.11.8"},
		"answer_mac": mac("answer " + c.id), "password": pwB,
	}
}

func (e *env) mustStatus(r resp, want int, what string) {
	e.t.Helper()
	if r.Status != want {
		e.t.Fatalf("%s: status %d, want %d: %s", what, r.Status, want, r.Raw)
	}
}

func errCode(r resp) string { s, _ := r.Body["error"].(string); return s }

func inviteField(r resp, k string) any {
	inv, _ := r.Body["invite"].(map[string]any)
	return inv[k]
}

// pairNode runs an invite to "answered" and returns the code.
func (e *env) pairNode(id string, seed byte, name string, port int) code {
	e.t.Helper()
	c := newCode(id, seed)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, name, port)), 201, "create")
	e.mustStatus(e.do("POST", "/mu/v1/invites/"+id+"/claim", "", map[string]any{"claim": c.claim, "session": c.session}), 200, "claim")
	e.mustStatus(e.do("POST", "/mu/v1/invites/"+id+"/answer", "Mu-Session "+c.session, answerFor(c, name)), 200, "answer")
	return c
}
