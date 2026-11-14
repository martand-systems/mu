package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestHealthAndRoot(t *testing.T) {
	e := newEnv(t, staticDefault())
	r := e.do("GET", "/health", "", nil)
	if r.Status != 200 || r.Raw != "OK\n" {
		t.Fatalf("/health: %d %q", r.Status, r.Raw)
	}
	r = e.do("GET", "/mu/v1/", "", nil)
	if r.Status != 200 || r.Body["service"] != "mu-relay" || r.Body["protocol"] != float64(1) || r.Body["pairing"] != true {
		t.Fatalf("/mu/v1/: %s", r.Raw)
	}
	if r.Header.Get("Cache-Control") != "no-store" {
		t.Fatalf("no Cache-Control: no-store")
	}
	if r := e.do("GET", "/version", "", nil); r.Status != 404 {
		t.Fatalf("/version: %d", r.Status)
	}
}

func TestAdminAuth(t *testing.T) {
	e := newEnv(t, staticDefault())
	if r := e.do("GET", "/mu/v1/clients", "", nil); r.Status != 401 || errCode(r) != "admin-required" {
		t.Fatalf("no token: %s", r.Raw)
	}
	// same first 8 characters, different token
	wrong := "Bearer " + testToken[:8] + strings.Repeat("x", len(testToken)-8)
	if r := e.do("GET", "/mu/v1/clients", wrong, nil); r.Status != 403 || errCode(r) != "admin-refused" {
		t.Fatalf("wrong token: %s", r.Raw)
	}
	if r := e.do("GET", "/mu/v1/clients", admin(), nil); r.Status != 200 {
		t.Fatalf("right token: %s", r.Raw)
	}
	for i := 0; i < 9; i++ {
		e.do("GET", "/mu/v1/clients", "Bearer mra_nope", nil)
	}
	if r := e.do("GET", "/mu/v1/clients", admin(), nil); r.Status != 429 {
		t.Fatalf("after 10 failures from one address the right token too must wait: %s", r.Raw)
	}
	if r := e.do("GET", "/mu/v1/clients", admin(), nil, "X-Forwarded-For", "203.0.113.9, 10.0.1.2"); r.Status != 200 {
		t.Fatalf("another address is not held off: %s", r.Raw)
	}
	e.clock.Add(16 * time.Minute)
	if r := e.do("GET", "/mu/v1/clients", admin(), nil); r.Status != 200 {
		t.Fatalf("after 15 minutes: %s", r.Raw)
	}
}

func TestInviteNodeEndToEnd(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]}) // avi-mac only: office-pc is new
	c := newCode("7K3M", 1)
	r := e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001))
	e.mustStatus(r, 201, "create")
	if inviteField(r, "state") != "created" || inviteField(r, "client") != "none" {
		t.Fatalf("create: %s", r.Raw)
	}
	r = e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session})
	e.mustStatus(r, 200, "claim")
	offer := r.Body["offer"].(map[string]any)
	if offer["inviter_key"] != testKey || r.Body["offer_mac"] != mac("offer 7K3M") || r.Body["resumed"] != false {
		t.Fatalf("claim: %s", r.Raw)
	}
	if _, leaks := r.Body["invite"].(map[string]any)["claims"]; leaks {
		t.Fatalf("the claimer sees the admin view")
	}
	r = e.do("GET", "/mu/v1/invites/7K3M/offer", "Mu-Session "+c.session, nil)
	e.mustStatus(r, 200, "offer")
	if r.Body["offer"].(map[string]any)["inviter_key"] != testKey {
		t.Fatalf("offer re-read: %s", r.Raw)
	}
	if _, pws := e.users(); pws[pwB] != "" {
		t.Fatalf("a client before the answer")
	}
	r = e.do("POST", "/mu/v1/invites/7K3M/answer", "Mu-Session "+c.session, answerFor(c, "office-pc"))
	e.mustStatus(r, 200, "answer")
	if inviteField(r, "state") != "answered" || inviteField(r, "client") != "created" {
		t.Fatalf("answer: %s", r.Raw)
	}
	rules, pws := e.users()
	if pws[pwB] != "office-pc" || rules["office-pc"] != `^R:127\.0\.0\.1:7001$` || rules["avi-mac"] != `^127\.0\.0\.1:(7001)$` {
		t.Fatalf("auth file after the answer: %v", rules)
	}
	// a retry of the same answer is fine; another answer is not
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/answer", "Mu-Session "+c.session, answerFor(c, "office-pc")), 200, "retry")
	a2 := answerFor(c, "office-pc")
	a2["answer_mac"] = mac("other")
	if r := e.do("POST", "/mu/v1/invites/7K3M/answer", "Mu-Session "+c.session, a2); r.Status != 409 {
		t.Fatalf("second answer: %s", r.Raw)
	}
	r = e.do("GET", "/mu/v1/invites/7K3M", admin(), nil)
	ans := inviteField(r, "answer").(map[string]any)
	if ans["host_key"] != testKey || inviteField(r, "answer_mac") != mac("answer 7K3M") {
		t.Fatalf("the admin sees the answer unchanged: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/accept", admin(), map[string]any{}), 200, "accept")
	conf := map[string]any{"confirm": map[string]string{"id": "7K3M", "offer_digest": mac("d"), "answer_digest": mac("a"), "outcome": "ok"}, "confirm_mac": mac("confirm")}
	r = e.do("POST", "/mu/v1/invites/7K3M/finish", admin(), conf)
	e.mustStatus(r, 200, "finish")
	r = e.do("GET", "/mu/v1/invites/7K3M/result", "Mu-Session "+c.session, nil)
	if inviteField(r, "state") != "finished" || r.Body["confirm_mac"] != mac("confirm") {
		t.Fatalf("result: %s", r.Raw)
	}
	r = e.do("GET", "/mu/v1/clients/office-pc?secret=1", admin(), nil)
	cl := r.Body["client"].(map[string]any)
	if cl["password"] != pwB || cl["source"] != "dynamic" || cl["via"] != "invite 7K3M" {
		t.Fatalf("client: %s", r.Raw)
	}
	if e.sup.count() != 0 {
		t.Fatalf("a new client must not restart chisel: %v", e.sup.evicted)
	}
	if r := e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": newCode("X", 9).session}); r.Status != 409 || errCode(r) != "invite-used" {
		t.Fatalf("claim after finish: %s", r.Raw)
	}
}

func TestOfferAndAnswerUnchanged(t *testing.T) {
	// The relay stores and returns the messages byte for byte (tamper-offer / tamper-answer play the attacker).
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := e.pairNode("7K3M", 1, "office-pc", 7001)
	r := e.do("GET", "/mu/v1/invites/7K3M/offer", "Mu-Session "+c.session, nil)
	sent := nodeInvite(newCode("7K3M", 1), "office-pc", 7001)["offer"].(map[string]string)
	got := r.Body["offer"].(map[string]any)
	if len(got) != len(sent) {
		t.Fatalf("the offer changed on the way: %v", got)
	}
	for k, v := range sent { // every member, byte for byte (the key, the relay URL and fingerprint included)
		if got[k] != v {
			t.Fatalf("the offer changed on the way: %s is %v, was %v", k, got[k], v)
		}
	}
	r = e.do("GET", "/mu/v1/invites/7K3M", admin(), nil)
	if inviteField(r, "answer").(map[string]any)["host_key"] != testKey {
		t.Fatalf("the answer changed on the way: %v", inviteField(r, "answer"))
	}
}

func TestCreateChecks(t *testing.T) {
	e := newEnv(t, staticDefault())
	c := newCode("7K3M", 1)
	bad := func(what string, mod func(m map[string]any), status int, code string) {
		t.Helper()
		m := nodeInvite(c, "lab-pc", 7002)
		mod(m)
		r := e.do("POST", "/mu/v1/invites", admin(), m)
		if r.Status != status || errCode(r) != code {
			t.Errorf("%s: %d %s, want %d %s", what, r.Status, errCode(r), status, code)
		}
	}
	bad("lower-case id", func(m map[string]any) { m["id"] = "7k3m" }, 400, "bad-request")
	bad("id with U", func(m map[string]any) { m["id"] = "7U3M" }, 400, "bad-request")
	bad("port 8080", func(m map[string]any) { m["port"] = 8080 }, 400, "bad-request")
	bad("upper-case name", func(m map[string]any) { m["name"] = "Lab" }, 400, "bad-request")
	bad("offer names another node", func(m map[string]any) { m["offer"].(map[string]string)["node_name"] = "x" }, 400, "bad-request")
	bad("offer names another port", func(m map[string]any) { m["offer"].(map[string]string)["port"] = "7003" }, 400, "bad-request")
	bad("offer with a control character", func(m map[string]any) { m["offer"].(map[string]string)["mu_version"] = "v\x1b[2J" }, 400, "bad-request")
	bad("unknown inviter", func(m map[string]any) {
		m["inviter"] = "bob"
		m["offer"].(map[string]string)["inviter_name"] = "bob"
	}, 409, "inviter-unknown")
	bad("a compute client as inviter", func(m map[string]any) {
		m["inviter"] = "office-pc"
		m["offer"].(map[string]string)["inviter_name"] = "office-pc"
	}, 409, "inviter-unknown")
	bad("ttl over an hour", func(m map[string]any) { m["ttl_s"] = 7200 }, 400, "bad-request")
	bad("an unknown member", func(m map[string]any) { m["extra"] = 1 }, 400, "bad-request")
	// port of a static compute client
	m := nodeInvite(c, "lab-pc", 7001)
	if r := e.do("POST", "/mu/v1/invites", admin(), m); r.Status != 409 || errCode(r) != "port-taken" {
		t.Errorf("port of office-pc: %s", r.Raw)
	}
	// existing name without replace
	m = nodeInvite(c, "office-pc", 7001)
	if r := e.do("POST", "/mu/v1/invites", admin(), m); r.Status != 409 || errCode(r) != "client-exists" {
		t.Errorf("existing name: %s", r.Raw)
	}
	// existing name, other role
	m = nodeInvite(c, "avi-mac", 7005)
	m["replace"] = true
	m["inviter"] = "avi-mac"
	if r := e.do("POST", "/mu/v1/invites", admin(), m); r.Status != 400 && errCode(r) != "role-mismatch" {
		t.Errorf("other role: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "lab-pc", 7002)), 201, "good")
	if r := e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "lab-pc2", 7003)); r.Status != 409 || errCode(r) != "id-taken" {
		t.Errorf("id twice: %s", r.Raw)
	}
	if r := e.do("POST", "/mu/v1/invites", admin(), nodeInvite(newCode("7K3N", 2), "lab-pc3", 7002)); r.Status != 409 || errCode(r) != "port-taken" {
		t.Errorf("port of an open invite: %s", r.Raw)
	}
}

func TestRoleMismatch(t *testing.T) {
	e := newEnv(t, staticDefault())
	m := nodeInvite(newCode("7K3M", 1), "avi-mac", 7005)
	m["replace"] = true
	r := e.do("POST", "/mu/v1/invites", admin(), m)
	if r.Status != 400 { // the inviter is the node: refused first
		t.Fatalf("%s", r.Raw)
	}
	r = e.do("PUT", "/mu/v1/clients/office-pc", admin(), map[string]any{"role": "workstation", "password": pwA, "ports": []int{7001}})
	if r.Status != 409 || errCode(r) != "role-mismatch" {
		t.Fatalf("a dynamic client must not take a static name with another role: %s", r.Raw)
	}
}

func TestWrongCodeSameAsUnknownAndLockout(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := newCode("7K3M", 1)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001)), 201, "create")
	other := newCode("7K3M", 2)
	unknown := e.do("POST", "/mu/v1/invites/ZZZZ/claim", "", map[string]any{"claim": c.claim, "session": c.session}, "X-Forwarded-For", "192.0.2.1, 10.0.0.1")
	wrong := e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": other.claim, "session": c.session}, "X-Forwarded-For", "192.0.2.1, 10.0.0.1")
	if unknown.Status != 403 || unknown.Raw != wrong.Raw {
		t.Fatalf("an unknown id and a wrong code must look the same:\n%s\n%s", unknown.Raw, wrong.Raw)
	}
	for i := 2; i <= lockAfter; i++ {
		ip := fmt.Sprintf("192.0.2.%d, 10.0.0.1", 10+i) // other addresses, so the per-address hold does not hide it
		e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": other.claim, "session": c.session}, "X-Forwarded-For", ip)
	}
	r := e.do("GET", "/mu/v1/invites/7K3M", admin(), nil)
	if inviteField(r, "state") != "locked" || inviteField(r, "wrong_claims") != float64(lockAfter) {
		t.Fatalf("after %d wrong claims: %s", lockAfter, r.Raw)
	}
	r = e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session}, "X-Forwarded-For", "198.51.100.200, 10.0.0.1")
	if r.Status != 410 || errCode(r) != "invite-locked" {
		t.Fatalf("right code on a locked invite: %s", r.Raw)
	}
}

func TestWrongClaimsDoNotLockAClaimedInvite(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := newCode("7K3M", 1)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001)), 201, "create")
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session}), 200, "claim")
	other := newCode("7K3M", 2)
	for i := 0; i < lockAfter+2; i++ {
		e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": other.claim, "session": other.session}, "X-Forwarded-For", fmt.Sprintf("192.0.2.%d, 10.0.0.1", i+1))
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/answer", "Mu-Session "+c.session, answerFor(c, "office-pc")), 200, "answer after wrong claims")
}

func TestPerAddressHoldOff(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := newCode("7K3M", 1)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001)), 201, "create")
	for i := 0; i < 10; i++ {
		e.do("POST", "/mu/v1/invites/QQQQ/claim", "", map[string]any{"claim": c.claim, "session": c.session})
	}
	r := e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session})
	if r.Status != 429 {
		t.Fatalf("an address with 10 wrong claims is held off: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session}, "X-Forwarded-For", "203.0.113.50, 10.0.0.1"), 200, "another address")
}

func TestSessionContinuityAndContested(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := newCode("7K3M", 1)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001)), 201, "create")
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session}), 200, "claim")
	// a retry with the same session (its answer was lost) resumes, from any address
	r := e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session}, "X-Forwarded-For", "203.0.113.77, 10.0.0.1")
	if r.Status != 200 || r.Body["resumed"] != true {
		t.Fatalf("resume: %s", r.Raw)
	}
	if v := e.do("GET", "/mu/v1/invites/7K3M", admin(), nil); inviteField(v, "contested") != false {
		t.Fatalf("a resume must not contest: %s", v.Raw)
	}
	// another session with the right code, from the SAME address: refused, contested
	thief := newCode("7K3M", 7)
	r = e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": thief.session})
	if r.Status != 409 || errCode(r) != "invite-used" {
		t.Fatalf("second claimer: %s", r.Raw)
	}
	if o, _ := r.Body["offer"].(map[string]any); o["inviter_key"] != testKey || r.Body["offer_mac"] != mac("offer 7K3M") {
		t.Fatalf("the refused claimer needs the offer for its contest: %s", r.Raw)
	}
	if r := e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": newCode("7K3M", 8).claim, "session": thief.session}); strings.Contains(r.Raw, "offer") {
		t.Fatalf("a wrong code got the offer: %s", r.Raw)
	}
	v := e.do("GET", "/mu/v1/invites/7K3M", admin(), nil)
	if inviteField(v, "contested") != true {
		t.Fatalf("not contested: %s", v.Raw)
	}
	// the first session still works; the second does not
	if r := e.do("GET", "/mu/v1/invites/7K3M/offer", "Mu-Session "+thief.session, nil); r.Status != 403 {
		t.Fatalf("the second claimer got the session: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/answer", "Mu-Session "+c.session, answerFor(c, "office-pc")), 200, "first claimer answers")
}

func TestContestRemovesClient(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := e.pairNode("7K3M", 1, "office-pc", 7001)
	if _, pws := e.users(); pws[pwB] != "office-pc" {
		t.Fatal("no client after the answer")
	}
	body := map[string]any{"claim": c.claim, "contest": map[string]string{"id": "7K3M", "offer_digest": mac("d")}, "contest_mac": mac("contest")}
	if r := e.do("POST", "/mu/v1/invites/7K3M/contest", "", map[string]any{"claim": newCode("7K3M", 3).claim, "contest": body["contest"], "contest_mac": mac("c")}); r.Status != 403 {
		t.Fatalf("a contest with a wrong code: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/contest", "", body), 200, "contest")
	if _, pws := e.users(); pws[pwB] != "" {
		t.Fatal("the contested invite's client is still in the auth file")
	}
	r := e.do("GET", "/mu/v1/invites/7K3M", admin(), nil)
	if inviteField(r, "contested") != true || inviteField(r, "contest_mac") != mac("contest") || inviteField(r, "client") != "removed" {
		t.Fatalf("after the contest: %s", r.Raw)
	}
	if e.sup.count() != 1 {
		t.Fatalf("the removed client's sessions must be cut: %d evictions", e.sup.count())
	}
}

func TestReplaceSwapsAtAccept(t *testing.T) {
	e := newEnv(t, staticDefault()) // static office-pc on 7001
	c := newCode("7K3M", 1)
	m := nodeInvite(c, "office-pc", 7001)
	m["replace"] = true
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), m), 201, "create")
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session}), 200, "claim")
	r := e.do("POST", "/mu/v1/invites/7K3M/answer", "Mu-Session "+c.session, answerFor(c, "office-pc"))
	e.mustStatus(r, 200, "answer")
	if _, pws := e.users(); pws[pwB] != "" || pws["static-pc-password-01234567890"] != "office-pc" {
		t.Fatalf("an unverified answer replaced the working client")
	}
	if e.sup.count() != 0 {
		t.Fatal("restart before accept")
	}
	conf := map[string]any{"confirm": map[string]string{"id": "7K3M", "outcome": "ok"}, "confirm_mac": mac("c")}
	if r := e.do("POST", "/mu/v1/invites/7K3M/finish", admin(), conf); r.Status != 409 || errCode(r) != "not-accepted" {
		t.Fatalf("finish before accept: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/accept", admin(), map[string]any{}), 200, "accept")
	if _, pws := e.users(); pws[pwB] != "office-pc" || pws["static-pc-password-01234567890"] != "" {
		t.Fatalf("accept did not swap the client")
	}
	if e.sup.count() != 1 {
		t.Fatalf("the swap must cut the old credential's sessions: %d", e.sup.count())
	}
	r = e.do("GET", "/mu/v1/clients", admin(), nil)
	if !strings.Contains(r.Raw, `"overrides_static":true`) {
		t.Fatalf("clients: %s", r.Raw)
	}
	// abort after accept: the static client comes back
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/abort", admin(), map[string]any{"reason": "integrity"}), 200, "abort")
	if _, pws := e.users(); pws[pwB] != "" || pws["static-pc-password-01234567890"] != "office-pc" {
		t.Fatalf("abort did not restore the static client")
	}
}

func TestDeadlines(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := newCode("7K3M", 1)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001)), 201, "create")
	e.clock.Add(1801 * time.Second)
	r := e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session})
	if r.Status != 410 || errCode(r) != "invite-expired" {
		t.Fatalf("unclaimed after 30 min: %s", r.Raw)
	}
	// claimed but not answered within the hour
	c2 := newCode("7K3N", 2)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c2, "lab-pc", 7002)), 201, "create 2")
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3N/claim", "", map[string]any{"claim": c2.claim, "session": c2.session}), 200, "claim 2")
	e.clock.Add(3601 * time.Second)
	if r := e.do("POST", "/mu/v1/invites/7K3N/answer", "Mu-Session "+c2.session, answerFor(c2, "lab-pc")); r.Status != 410 {
		t.Fatalf("answer after the hour: %s", r.Raw)
	}
	// answered, never confirmed: unconfirmed after 24 h, the client stays (review finding 3)
	c3 := e.pairNode("7K3P", 3, "gpu-pc", 7003)
	e.clock.Add(86401 * time.Second)
	r = e.do("GET", "/mu/v1/invites/7K3P/result", "Mu-Session "+c3.session, nil)
	if inviteField(r, "state") != "unconfirmed" {
		t.Fatalf("after 24 h: %s", r.Raw)
	}
	if _, pws := e.users(); pws[pwB] != "gpu-pc" {
		t.Fatal("an unconfirmed pairing lost its client")
	}
	// records go 24 h after they ended
	e.clock.Add(86401 * time.Second)
	if r := e.do("GET", "/mu/v1/invites/7K3P", admin(), nil); r.Status != 404 {
		t.Fatalf("not purged: %s", r.Raw)
	}
}

func TestAbort(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := e.pairNode("7K3M", 1, "office-pc", 7001)
	if r := e.do("POST", "/mu/v1/invites/7K3M/abort", "Mu-Session "+c.session, map[string]any{"reason": "integrity"}); r.Status != 409 {
		t.Fatalf("the claimer may not abort after its answer: %s", r.Raw)
	}
	if r := e.do("POST", "/mu/v1/invites/7K3M/abort", admin(), map[string]any{"reason": "because"}); r.Status != 400 {
		t.Fatalf("free-text reason: %s", r.Raw)
	}
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3M/abort", admin(), map[string]any{"reason": "integrity"}), 200, "abort")
	if _, pws := e.users(); pws[pwB] != "" {
		t.Fatal("the aborted invite's client stayed")
	}
	if e.sup.count() != 1 {
		t.Fatalf("evictions: %d", e.sup.count())
	}
	// session abort before the answer
	c2 := newCode("7K3N", 2)
	e.mustStatus(e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c2, "lab-pc", 7002)), 201, "create")
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3N/claim", "", map[string]any{"claim": c2.claim, "session": c2.session}), 200, "claim")
	e.mustStatus(e.do("POST", "/mu/v1/invites/7K3N/abort", "Mu-Session "+c2.session, map[string]any{"reason": "integrity"}), 200, "session abort")
}

func TestStrictRequests(t *testing.T) {
	e := newEnv(t, staticDefault())
	c := newCode("7K3M", 1)
	dup := `{"id":"7K3M","id":"7K3N"}`
	if r := e.do("POST", "/mu/v1/invites", admin(), dup); r.Status != 400 {
		t.Fatalf("duplicate member: %s", r.Raw)
	}
	m := nodeInvite(c, "lab-pc", 7002)
	m["surprise"] = true
	if r := e.do("POST", "/mu/v1/invites", admin(), m); r.Status != 400 {
		t.Fatalf("unknown member: %s", r.Raw)
	}
	if r := e.do("POST", "/mu/v1/invites", admin(), strings.Repeat(" ", maxBody+1)); r.Status != 413 {
		t.Fatalf("16 KiB + 1: %d", r.Status)
	}
	if r := e.do("POST", "/mu/v1/invites/7K3M/claim", "", `{"claim":"`+c.claim+`","session":"`+c.session+`"} {}`); r.Status != 400 {
		t.Fatalf("trailing data: %s", r.Raw)
	}
}

func TestUpgradeRefused(t *testing.T) {
	e := newEnv(t, staticDefault())
	r := e.do("GET", "/mu/v1/", "", nil, "Upgrade", "websocket", "Connection", "Upgrade")
	if r.Status != 400 || errCode(r) != "upgrade-refused" {
		t.Fatalf("an upgrade reached the API: %d %s", r.Status, r.Raw)
	}
}

func TestRateLimitAndAddress(t *testing.T) {
	e := newEnv(t, staticDefault())
	// Traefik's view: the client's address second to last, Traefik's own last. A client-supplied first entry is not used.
	for i := 0; i < 30; i++ {
		if r := e.do("GET", "/mu/v1/", "", nil, "X-Forwarded-For", fmt.Sprintf("10.9.9.%d, 198.51.100.23, 10.0.1.2", i)); r.Status != 200 {
			t.Fatalf("request %d: %d", i+1, r.Status)
		}
	}
	if r := e.do("GET", "/mu/v1/", "", nil, "X-Forwarded-For", "10.9.9.99, 198.51.100.23, 10.0.1.2"); r.Status != 429 || r.Header.Get("Retry-After") == "" {
		t.Fatalf("31st request from one address: %d", r.Status)
	}
	if r := e.do("GET", "/mu/v1/", "", nil, "X-Forwarded-For", "203.0.113.4, 10.0.1.2"); r.Status != 200 {
		t.Fatalf("another address: %d", r.Status)
	}
	e.clock.Add(time.Minute)
	if r := e.do("GET", "/mu/v1/", "", nil); r.Status != 200 {
		t.Fatalf("a minute later: %d", r.Status)
	}
}

func TestAddressTableCapped(t *testing.T) {
	l := newLimits(time.Now)
	l.max = 100
	for i := 0; i < 1000; i++ {
		l.Unauth(fmt.Sprintf("10.0.%d.%d", i/256, i%256))
	}
	if len(l.byAddr) != 100 || l.lru.Len() != 100 {
		t.Fatalf("table size %d", len(l.byAddr))
	}
}

func TestDeleteAndLastClient(t *testing.T) {
	e := newEnv(t, staticDefault())
	e.mustStatus(e.do("PUT", "/mu/v1/clients/lab-pc", admin(), map[string]any{"role": "compute", "password": pwA, "ports": []int{7002}}), 201, "put")
	if e.sup.count() != 0 {
		t.Fatal("an added client restarted chisel")
	}
	r := e.do("DELETE", "/mu/v1/clients/lab-pc", admin(), nil)
	if r.Status != 200 || r.Body["removed"] != "dynamic" || e.sup.count() != 1 {
		t.Fatalf("delete dynamic: %s (%d evictions)", r.Raw, e.sup.count())
	}
	r = e.do("DELETE", "/mu/v1/clients/office-pc", admin(), nil)
	if r.Status != 200 || r.Body["removed"] != "tombstone" {
		t.Fatalf("delete static: %s", r.Raw)
	}
	if rules, _ := e.users(); rules["office-pc"] != "" {
		t.Fatal("a tombstoned client is still in the auth file")
	}
	r = e.do("DELETE", "/mu/v1/clients/avi-mac", admin(), nil)
	if r.Status != 409 || errCode(r) != "last-client" {
		t.Fatalf("the last client: %s", r.Raw)
	}
	rules, _ := e.users()
	if rules[SentinelName] != SentinelRule || rules["avi-mac"] == "" {
		t.Fatalf("auth file: %v", rules)
	}
}

func TestAuthFileNeverEmpty(t *testing.T) {
	// Every static client revoked: the sentinel alone, so chisel's authentication stays on.
	e := newEnv(t, staticDefault(), "avi-mac", "office-pc")
	rules, _ := e.users()
	if len(rules) != 1 || rules[SentinelName] != SentinelRule {
		t.Fatalf("auth file with every client revoked: %v", rules)
	}
	if err := checkAuthFile([]byte("{}\n")); err == nil {
		t.Fatal("an empty auth file passed the check")
	}
}

func TestPortStrippedWhenComputeDeleted(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	e.pairNode("7K3M", 1, "lab-pc", 7002)
	if rules, _ := e.users(); rules["avi-mac"] != `^127\.0\.0\.1:(7001|7002)$` {
		t.Fatalf("grant: %v", rules["avi-mac"])
	}
	e.mustStatus(e.do("DELETE", "/mu/v1/clients/lab-pc", admin(), nil), 200, "delete")
	if rules, _ := e.users(); rules["avi-mac"] != `^127\.0\.0\.1:(7001)$` {
		t.Fatalf("the deleted node's port stayed granted: %v", rules["avi-mac"])
	}
}

func TestGrantsNeverCopyPasswords(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	e.pairNode("7K3M", 1, "lab-pc", 7002)
	b, _ := os.ReadFile(filepath.Join(e.cfg.StateDir, "state.json"))
	if strings.Contains(string(b), "static-mac-password") {
		t.Fatal("the static workstation's password was copied into the store")
	}
	_, pws := e.users()
	if pws["static-mac-password-0123456789"] != "avi-mac" {
		t.Fatal("avi-mac's password is not its static one")
	}
}

func TestRevokedAtStart(t *testing.T) {
	e := newEnv(t, staticDefault())
	e.mustStatus(e.do("PUT", "/mu/v1/clients/old-laptop", admin(), map[string]any{"role": "workstation", "password": pwC, "ports": []int{7001}}), 201, "put")
	e.mustStatus(e.do("PUT", "/mu/v1/grants/avi-mac", admin(), map[string]any{"ports": []int{7002}}), 200, "grant")
	// the owner adds both to RELAY_REVOKED and deploys
	e.cfg.Revoked = map[string]bool{"old-laptop": true, "avi-mac": true}
	e.cfg.Static = staticDefault()
	e.start()
	rules, _ := e.users()
	if rules["old-laptop"] != "" || rules["avi-mac"] != "" {
		t.Fatalf("revoked clients in the auth file: %v", rules)
	}
	b, _ := os.ReadFile(filepath.Join(e.cfg.StateDir, "state.json"))
	if strings.Contains(string(b), "old-laptop") || strings.Contains(string(b), `"avi-mac"`) {
		t.Fatalf("revoked names left in the store:\n%s", b)
	}
	if r := e.do("PUT", "/mu/v1/clients/old-laptop", admin(), map[string]any{"role": "workstation", "password": pwC, "ports": []int{7001}}); r.Status != 409 || errCode(r) != "revoked" {
		t.Fatalf("PUT of a revoked name: %s", r.Raw)
	}
}

func TestShadowedStatic(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	e.mustStatus(e.do("PUT", "/mu/v1/clients/lab-pc", admin(), map[string]any{"role": "compute", "password": pwA, "ports": []int{7002}}), 201, "put")
	// a later Dokploy edit adds a static compute client on the same port
	e.cfg.Static = append(staticDefault()[:1], Entry{Name: "other-pc", Role: RoleCompute, Password: "static-other-password-0123456", Ports: []int{7002}, Source: "static"})
	e.start()
	rules, _ := e.users()
	if rules["other-pc"] != "" || rules["lab-pc"] == "" {
		t.Fatalf("the dynamic client must win the port: %v", rules)
	}
	r := e.do("GET", "/mu/v1/clients", admin(), nil)
	if !strings.Contains(r.Raw, `"shadowed":[{"name":"other-pc"`) {
		t.Fatalf("shadowed not listed: %s", r.Raw)
	}
}

func TestLongPoll(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := newCode("7K3M", 1)
	r := e.do("POST", "/mu/v1/invites", admin(), nodeInvite(c, "office-pc", 7001))
	v := int64(inviteField(r, "version").(float64))
	done := make(chan resp, 1)
	go func() { done <- e.do("GET", fmt.Sprintf("/mu/v1/invites/7K3M?wait=10&since=%d", v), admin(), nil) }()
	time.Sleep(200 * time.Millisecond)
	start := time.Now()
	e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": c.session})
	r = <-done
	if inviteField(r, "state") != "claimed" || time.Since(start) > 3*time.Second {
		t.Fatalf("long poll: %s after %v", r.Raw, time.Since(start))
	}
}

func TestNoSecretsInLogs(t *testing.T) {
	e := newEnv(t, []Entry{staticDefault()[0]})
	c := e.pairNode("7K3M", 1, "office-pc", 7001)
	e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": c.claim, "session": newCode("7K3M", 5).session})
	e.do("POST", "/mu/v1/invites/7K3M/claim", "", map[string]any{"claim": newCode("7K3M", 6).claim, "session": c.session})
	e.do("GET", "/mu/v1/clients", "Bearer mra_wrongwrongwrongwrongwrongwrongwrongwrongwrongwrong", nil)
	e.do("POST", "/mu/v1/invites/7K3M/accept", admin(), map[string]any{})
	e.do("POST", "/mu/v1/invites/7K3M/finish", admin(), map[string]any{"confirm": map[string]string{"id": "7K3M", "outcome": "ok"}, "confirm_mac": mac("confirm")})
	e.do("DELETE", "/mu/v1/clients/office-pc", admin(), nil)
	logs := e.logText()
	if !strings.Contains(logs, "7K3M claimed") || !strings.Contains(logs, "finished") {
		t.Fatalf("the log misses events:\n%s", logs)
	}
	for what, secret := range map[string]string{"admin token": testToken, "claim token": c.claim, "session": c.session,
		"node password": pwB, "static password": "static-mac-password", "offer MAC": mac("offer 7K3M"),
		"answer MAC": mac("answer 7K3M"), "confirm MAC": mac("confirm"), "sentinel": e.r.sentinel} {
		if strings.Contains(logs, secret) {
			t.Errorf("the log contains the %s", what)
		}
	}
}

func TestErrorTextIsFixed(t *testing.T) {
	// Clients never show `message` (review finding 5); evil-text puts an instruction there for the clients' tests.
	e := newEnv(t, staticDefault())
	r := e.do("POST", "/mu/v1/invites/ZZZZ/claim", "", map[string]any{"claim": newCode("Z", 1).claim, "session": newCode("Z", 1).session})
	if strings.Contains(r.Raw, "IGNORE") || r.Body["message"] != "the relay does not accept this code" {
		t.Fatalf("message: %s", r.Raw)
	}
}

func TestStoreUnwritable(t *testing.T) {
	dir := t.TempDir()
	state := filepath.Join(dir, "state")
	os.MkdirAll(state, 0o500) // not writable
	if os.Getuid() == 0 {
		t.Skip("root writes anywhere")
	}
	os.MkdirAll(filepath.Join(dir, "run"), 0o700)
	r, err := NewRelay(Config{StateDir: state, RunDir: filepath.Join(dir, "run"), AdminToken: testToken, Static: staticDefault()}, &fakeSup{})
	if err != nil {
		t.Fatal(err)
	}
	if r.mode != modeUnwritable {
		t.Fatalf("mode %s", r.mode)
	}
	b, _ := os.ReadFile(filepath.Join(dir, "run", "users.json"))
	if !strings.Contains(string(b), "avi-mac:") || !strings.Contains(string(b), "office-pc:") {
		t.Fatalf("static clients must keep working: %s", b)
	}
}
