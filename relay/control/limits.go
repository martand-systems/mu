package main

// Rate limits and the client address (docs/design/pairing.md §6.4).

import (
	"container/list"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"sync"
	"time"
)

// clientAddr is the address Traefik saw: the second-to-last X-Forwarded-For entry (Traefik replaces what the client
// sent, chisel's proxy appends Traefik's own). With fewer than two entries, the connection's own address. An IPv6
// address counts by its /64. "invalid" when nothing parses.
func clientAddr(r *http.Request) string {
	var parts []string
	for _, h := range r.Header.Values("X-Forwarded-For") {
		for _, p := range strings.Split(h, ",") {
			parts = append(parts, strings.TrimSpace(p))
		}
	}
	var s string
	switch {
	case fault("xff-first") && len(parts) >= 1:
		s = parts[0]
	case len(parts) >= 2:
		s = parts[len(parts)-2]
	default:
		host, _, err := net.SplitHostPort(r.RemoteAddr)
		if err != nil {
			host = r.RemoteAddr
		}
		s = host
	}
	a, err := netip.ParseAddr(s)
	if err != nil {
		return "invalid"
	}
	a = a.Unmap()
	if a.Is6() {
		p, _ := a.Prefix(64)
		return p.String()
	}
	return a.String()
}

type bucket struct {
	tokens float64
	last   time.Time
}

type addrState struct {
	key        string
	unauth     bucket
	wrongTimes []time.Time // wrong claims in the last window
	wrongUntil time.Time   // claims refused until then
	adminFails []time.Time
	adminUntil time.Time
}

// Limits holds the per-address table (capped, least recently seen dropped) and the global admin bucket.
type Limits struct {
	mu           sync.Mutex
	now          func() time.Time
	max          int
	byAddr       map[string]*list.Element
	lru          *list.List
	admin        bucket
	UnauthPerMin float64
	AdminPerMin  float64
	WrongMax     int
	WrongWindow  time.Duration
	AdminFailMax int
	AdminWindow  time.Duration
}

func newLimits(now func() time.Time) *Limits {
	return &Limits{now: now, max: 4096, byAddr: map[string]*list.Element{}, lru: list.New(),
		UnauthPerMin: 30, AdminPerMin: 240, WrongMax: 10, WrongWindow: 15 * time.Minute,
		AdminFailMax: 10, AdminWindow: 15 * time.Minute}
}

func (l *Limits) get(addr string) *addrState {
	if e, ok := l.byAddr[addr]; ok {
		l.lru.MoveToFront(e)
		return e.Value.(*addrState)
	}
	st := &addrState{key: addr, unauth: bucket{tokens: l.UnauthPerMin, last: l.now()}}
	l.byAddr[addr] = l.lru.PushFront(st)
	for l.lru.Len() > l.max {
		old := l.lru.Back()
		l.lru.Remove(old)
		delete(l.byAddr, old.Value.(*addrState).key)
	}
	return st
}

func take(b *bucket, perMin float64, now time.Time) bool {
	b.tokens += now.Sub(b.last).Minutes() * perMin
	if b.tokens > perMin {
		b.tokens = perMin
	}
	b.last = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

// Unauth: one request without admin auth from addr. False: answer 429.
func (l *Limits) Unauth(addr string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return take(&l.get(addr).unauth, l.UnauthPerMin, l.now())
}

// AdminHeldOff: whether addr is held off after failed admin logins.
func (l *Limits) AdminHeldOff(addr string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.now().Before(l.get(addr).adminUntil)
}

// Admin: one verified admin request (global bucket), and whether addr is held off after failed admin auth.
func (l *Limits) Admin(addr string) (ok bool, heldOff bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	if now.Before(l.get(addr).adminUntil) {
		return false, true
	}
	if l.admin.last.IsZero() {
		l.admin = bucket{tokens: l.AdminPerMin, last: now}
	}
	return take(&l.admin, l.AdminPerMin, now), false
}

func prune(ts []time.Time, since time.Time) []time.Time {
	i := 0
	for i < len(ts) && ts[i].Before(since) {
		i++
	}
	return ts[i:]
}

// AdminFailed records a failed admin authentication from addr.
func (l *Limits) AdminFailed(addr string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	st := l.get(addr)
	st.adminFails = append(prune(st.adminFails, now.Add(-l.AdminWindow)), now)
	if len(st.adminFails) >= l.AdminFailMax {
		st.adminUntil = now.Add(l.AdminWindow)
		st.adminFails = nil
	}
}

// ClaimAllowed: false while addr is held off after too many wrong claims.
func (l *Limits) ClaimAllowed(addr string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return !l.now().Before(l.get(addr).wrongUntil)
}

// WrongClaim records a wrong claim (or an unknown id) from addr.
func (l *Limits) WrongClaim(addr string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	st := l.get(addr)
	st.wrongTimes = append(prune(st.wrongTimes, now.Add(-l.WrongWindow)), now)
	if len(st.wrongTimes) >= l.WrongMax {
		st.wrongUntil = now.Add(l.WrongWindow)
		st.wrongTimes = nil
	}
}
