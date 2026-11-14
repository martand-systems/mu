package main

// The security review of ff2c707 (docs/design/pairing.md §17): each test here was first an exploit that passed against
// that commit; each is named in must-fail.sh with the fault that puts the defect back.

import (
	"fmt"
	"os"
	"strings"
	"testing"
)

// Finding 1: nobody without the token can empty the global admin bucket the owner's requests need.
func TestAdminBucketNotStarved(t *testing.T) {
	e := newEnv(t, staticDefault())
	// requests with no Authorization header, from one address: refused, counted as failed logins, then held off
	for i := 0; i < 240; i++ {
		r := e.do("GET", "/mu/v1/clients", "", nil, "X-Forwarded-For", "198.51.100.66, 10.0.1.2")
		if i == 0 && (r.Status != 401 || errCode(r) != "admin-required") {
			t.Fatalf("no header: %s", r.Raw)
		}
	}
	if r := e.do("GET", "/mu/v1/clients", "", nil, "X-Forwarded-For", "198.51.100.66, 10.0.1.2"); r.Status != 429 {
		t.Fatalf("an address sending header-less admin requests is never held off: %s", r.Raw)
	}
	// wrong tokens spread over 25 IPv6 /64s
	for a := 0; a < 25; a++ {
		for i := 0; i < 10; i++ {
			e.do("GET", "/mu/v1/clients", "Bearer mra_nope", nil, "X-Forwarded-For", fmt.Sprintf("2001:db8:%x::1, 10.0.1.2", a))
		}
	}
	if r := e.do("GET", "/mu/v1/clients", admin(), nil, "X-Forwarded-For", "203.0.113.5, 10.0.1.2"); r.Status != 200 {
		t.Fatalf("the owner's token from another address is refused after a flood without the token: %s", r.Raw)
	}
	// the verified requests still have their own global limit
	n429 := 0
	for i := 0; i < 260; i++ {
		if r := e.do("GET", "/mu/v1/clients", admin(), nil, "X-Forwarded-For", "203.0.113.5, 10.0.1.2"); r.Status == 429 {
			n429++
		}
	}
	if n429 == 0 {
		t.Fatalf("260 verified admin requests in a minute, none limited")
	}
}

// Finding 2: a tombstoned static client never comes back when a dynamic client of its name goes again.
func TestTombstoneOutlivesDynamicClient(t *testing.T) {
	static := "static-pc-password-01234567890"
	removeStatic := func(e *env) {
		e.mustStatus(e.do("DELETE", "/mu/v1/clients/office-pc", admin(), nil), 200, "delete static")
		if _, pws := e.users(); pws[static] == "office-pc" {
			t.Fatal("setup: the tombstone did not take")
		}
	}
	check := func(e *env, how string) {
		if _, pws := e.users(); pws[static] == "office-pc" {
			t.Fatalf("the removed static office-pc is back in the auth file with its old password after %s", how)
		}
		r := e.do("GET", "/mu/v1/clients", admin(), nil)
		if !strings.Contains(r.Raw, `"name":"office-pc","removed"`) {
			t.Fatalf("the tombstone is gone after %s: %s", how, r.Raw)
		}
	}
	// a contest by whoever holds the code
	e := newEnv(t, staticDefault())
	removeStatic(e)
	c := e.pairNode("7K3M", 1, "office-pc", 7001) // no --replace needed: the tombstoned name is free
	if _, pws := e.users(); pws[static] == "office-pc" {
		t.Fatal("the static password is in the auth file while the dynamic office-pc stands")
	}
	body := map[string]any{"claim": c.claim, "contest": map[string]string{"id": "7K3M", "offer_digest": mac("d")}, "contest_mac": mac("contest")}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/contest", "", body), 200, "contest")
	check(e, "a contest")
	// the inviter's integrity abort
	e = newEnv(t, staticDefault())
	removeStatic(e)
	e.pairNode("7K3M", 1, "office-pc", 7001)
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/abort", admin(), map[string]any{"reason": "integrity"}), 200, "abort")
	check(e, "an integrity abort")
	// mu compute remove: DELETE of the dynamic client
	e = newEnv(t, staticDefault())
	removeStatic(e)
	e.pairNode("7K3M", 1, "office-pc", 7001)
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/accept", admin(), map[string]any{}), 200, "accept")
	e.mustStatus(e.do("DELETE", "/mu/v1/clients/office-pc", admin(), nil), 200, "delete dynamic")
	check(e, "DELETE of the dynamic client")
	// PUT (mu relay restore) of a dynamic client, then DELETE
	e = newEnv(t, staticDefault())
	removeStatic(e)
	e.mustStatus(e.do("PUT", "/mu/v1/clients/office-pc", admin(), map[string]any{"role": "compute",
		"password": "dyn-pc-password-0123456789abc", "ports": []int{7001}}), 201, "put")
	e.mustStatus(e.do("DELETE", "/mu/v1/clients/office-pc", admin(), nil), 200, "delete put client")
	check(e, "PUT then DELETE")
	// DELETE /mu/v1/tombstones/{name} still brings the static entry back, on purpose
	e.mustStatus(e.do("DELETE", "/mu/v1/tombstones/office-pc", admin(), nil), 200, "untombstone")
	if _, pws := e.users(); pws[static] != "office-pc" {
		t.Fatal("DELETE /mu/v1/tombstones/office-pc did not bring the static entry back")
	}
}

// Finding 8: a state.json that parses but makes an auth file the check refuses: .prev, else mode damaged with the
// static clients; never a failed start.
func TestParseableBadStateDoesNotStopTheRelay(t *testing.T) {
	e := newEnv(t, staticDefault())
	p := e.cfg.StateDir + "/state.json"
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	bad := strings.Replace(string(b), `"clients": {}`, `"clients": {"x": {"role":"compute","password":"short","ports":[7005],"created":1,"via":"put"}}`, 1)
	if bad == string(b) {
		t.Fatal("setup: could not plant the bad client")
	}
	if err := os.WriteFile(p, []byte(bad), 0o600); err != nil {
		t.Fatal(err)
	}
	os.Remove(p + ".prev")
	r, err := NewRelay(e.cfg, e.sup)
	if err != nil {
		t.Fatalf("NewRelay refuses to start (the static clients would be down too): %v", err)
	}
	if r.mode != modeDamaged {
		t.Fatalf("mode %q, want damaged", r.mode)
	}
	if _, pws := e.users(); pws["static-pc-password-01234567890"] != "office-pc" {
		t.Fatal("the static clients are not in the auth file of a damaged store")
	}
	// with a good .prev, that one is used
	if err := os.WriteFile(p+".prev", b, 0o600); err != nil {
		t.Fatal(err)
	}
	r, err = NewRelay(e.cfg, e.sup)
	if err != nil || r.mode != modeOK {
		t.Fatalf("a good .prev was not used: %v, mode %q", err, r.mode)
	}
}
