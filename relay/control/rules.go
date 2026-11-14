package main

// Clients, their access rules, and chisel's auth file (docs/design/pairing.md §6.5).
//
// The rules are exactly relay/entrypoint.sh's:
//   compute, port P            ^R:127\.0\.0\.1:P$          one reverse listener on the relay's own loopback
//   workstation, ports P1+P2   ^127\.0\.0\.1:(P1|P2)$      forwards to those loopback ports only
// and the auth file always holds a sentinel user, so chisel's user list is never empty: with no users chisel 1.12.0
// turns authentication off altogether (server/server.go authUser).

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const (
	RoleCompute     = "compute"
	RoleWorkstation = "workstation"
	PortMin         = 7001
	PortMax         = 7999
	// A name no client can have: '_' is outside the client name alphabet.
	SentinelName = "_mu-sentinel"
	// No chisel address ("host:port", "R:host:port", "socks", "R:socks") can be "-".
	SentinelRule = "^-$"
)

var (
	nameRE         = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,31}$`)
	staticPassRE   = regexp.MustCompile(`^[A-Za-z0-9_-]{24,}$`)
	dynamicPassRE  = regexp.MustCompile(`^[A-Za-z0-9_-]{24,128}$`)
	computeRuleRE  = regexp.MustCompile(`^\^R:127\\\.0\\\.0\\\.1:([0-9]{4})\$$`)
	workRuleRE     = regexp.MustCompile(`^\^127\\\.0\\\.0\\\.1:\(([0-9]{4}(?:\|[0-9]{4})*)\)\$$`)
	sentinelPassRE = regexp.MustCompile(`^[A-Za-z0-9_-]{43}$`)
)

func validName(s string) bool       { return nameRE.MatchString(s) }
func validPort(p int) bool          { return p >= PortMin && p <= PortMax }
func validDynPass(s string) bool    { return dynamicPassRE.MatchString(s) }
func validRole(r string) bool       { return r == RoleCompute || r == RoleWorkstation }
func validStaticPass(s string) bool { return staticPassRE.MatchString(s) }

// Entry is one user of the auth file.
type Entry struct {
	Name     string
	Role     string
	Password string
	Ports    []int
	Source   string // "static" or "dynamic"
	Grants   []int  // static workstations: the ports added by grants (already in Ports)
	Override bool   // a dynamic client that took a static client's place
}

// Shadowed is a static entry left out of the auth file because a dynamic client holds its name or port.
type Shadowed struct {
	Name   string `json:"name"`
	Reason string `json:"reason"`
}

func ruleFor(e Entry) string {
	anchorL, anchorR := "^", "$"
	if fault("unanchored-rule") {
		anchorL, anchorR = "", ""
	}
	if e.Role == RoleCompute {
		return anchorL + `R:127\.0\.0\.1:` + strconv.Itoa(e.Ports[0]) + anchorR
	}
	if fault("wide-ports") {
		return `^127\.0\.0\.1:(7[0-9]{3})$`
	}
	ps := make([]string, len(e.Ports))
	for i, p := range e.Ports {
		ps[i] = strconv.Itoa(p)
	}
	return anchorL + `127\.0\.0\.1:(` + strings.Join(ps, "|") + `)` + anchorR
}

// parsePorts parses "7001" or "7001+7002" with the entrypoint's rules (no leading zero, 7001-7999).
func parsePorts(s string) ([]int, error) {
	var out []int
	for _, p := range strings.Split(s, "+") {
		if p == "" || len(p) > 5 || p[0] == '0' || strings.Trim(p, "0123456789") != "" {
			return nil, fmt.Errorf("port %q", p)
		}
		n, _ := strconv.Atoi(p)
		if !validPort(n) {
			return nil, fmt.Errorf("port %d outside %d-%d", n, PortMin, PortMax)
		}
		out = append(out, n)
	}
	return out, nil
}

func portsString(ps []int) string {
	s := make([]string, len(ps))
	for i, p := range ps {
		s[i] = strconv.Itoa(p)
	}
	return strings.Join(s, "+")
}

// readStatic reads /run/relay/static.tsv as relay/entrypoint.sh wrote it: role, name, password, ports per line.
func readStatic(path string) ([]Entry, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []Entry
	names := map[string]bool{}
	ports := map[int]string{}
	sc := bufio.NewScanner(f)
	n := 0
	for sc.Scan() {
		n++
		line := sc.Text()
		if line == "" {
			continue
		}
		f := strings.Split(line, "\t")
		if len(f) != 4 {
			return nil, fmt.Errorf("static.tsv line %d: expected 4 fields", n)
		}
		e := Entry{Role: f[0], Name: f[1], Password: f[2], Source: "static"}
		if !validRole(e.Role) || !validName(e.Name) || !validStaticPass(e.Password) {
			return nil, fmt.Errorf("static.tsv line %d: bad role, name or password", n)
		}
		if names[e.Name] {
			return nil, fmt.Errorf("static.tsv line %d: name %s twice", n, e.Name)
		}
		names[e.Name] = true
		if e.Ports, err = parsePorts(f[3]); err != nil {
			return nil, fmt.Errorf("static.tsv line %d: %v", n, err)
		}
		if e.Role == RoleCompute {
			if len(e.Ports) != 1 {
				return nil, fmt.Errorf("static.tsv line %d: a compute client has one port", n)
			}
			if o, ok := ports[e.Ports[0]]; ok {
				return nil, fmt.Errorf("static.tsv line %d: port %d belongs to %s", n, e.Ports[0], o)
			}
			ports[e.Ports[0]] = e.Name
		}
		out = append(out, e)
	}
	return out, sc.Err()
}

// merge builds the auth file's entries from the static list and the store (§6.5). Revoked names are never in it.
// A dynamic client wins over a static entry of the same name (same role: overrides_static; another role: the static
// entry is shadowed) and over a static compute client on its port.
func merge(static []Entry, st *State, revoked map[string]bool) ([]Entry, []Shadowed) {
	var out []Entry
	var shadowed []Shadowed
	dynPorts := map[int]string{}
	for name, c := range st.Clients {
		if revoked[name] && !fault("ignore-revoked") {
			continue
		}
		if c.Role == RoleCompute && len(c.Ports) == 1 {
			dynPorts[c.Ports[0]] = name
		}
	}
	for _, s := range static {
		if revoked[s.Name] && !fault("ignore-revoked") {
			continue
		}
		if _, ok := st.Tombstones[s.Name]; ok && !fault("ignore-tombstones") {
			continue
		}
		if d, ok := st.Clients[s.Name]; ok && !(revoked[s.Name] && !fault("ignore-revoked")) {
			if d.Role != s.Role {
				shadowed = append(shadowed, Shadowed{s.Name, "a dynamic client of another role has this name"})
			}
			continue // the dynamic client is added below
		}
		if s.Role == RoleCompute {
			if o, ok := dynPorts[s.Ports[0]]; ok {
				shadowed = append(shadowed, Shadowed{s.Name, "dynamic client " + o + " has port " + strconv.Itoa(s.Ports[0])})
				continue
			}
		}
		e := s
		e.Ports = append([]int(nil), s.Ports...)
		if s.Role == RoleWorkstation {
			for _, p := range st.Grants[s.Name] {
				if !containsInt(e.Ports, p) {
					e.Ports = append(e.Ports, p)
					e.Grants = append(e.Grants, p)
				}
			}
		}
		out = append(out, e)
	}
	staticNames := map[string]bool{}
	for _, s := range static {
		staticNames[s.Name] = true
	}
	for _, name := range sortedKeys(st.Clients) {
		if revoked[name] && !fault("ignore-revoked") {
			continue
		}
		c := st.Clients[name]
		out = append(out, Entry{Name: name, Role: c.Role, Password: c.Password, Ports: append([]int(nil), c.Ports...),
			Source: "dynamic", Override: staticNames[name]})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, shadowed
}

func containsInt(xs []int, x int) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

func sortedKeys[V any](m map[string]V) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func newSentinelPassword() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

// render writes the auth file's bytes: the entrypoint's layout, one user per line, plus the sentinel.
func render(entries []Entry, sentinelPass string) []byte {
	var b bytes.Buffer
	b.WriteString("{\n")
	lines := []string{}
	if !fault("allow-empty") {
		k, _ := json.Marshal(SentinelName + ":" + sentinelPass)
		r, _ := json.Marshal(SentinelRule)
		lines = append(lines, fmt.Sprintf("  %s: [%s]", k, r))
	}
	for _, e := range entries {
		k, _ := json.Marshal(e.Name + ":" + e.Password)
		r, _ := json.Marshal(ruleFor(e))
		lines = append(lines, fmt.Sprintf("  %s: [%s]", k, r))
	}
	b.WriteString(strings.Join(lines, ",\n"))
	b.WriteString("\n}\n")
	return b.Bytes()
}

// checkAuthFile is the last check before the auth file is replaced (review finding 1): it decodes the exact bytes
// again and refuses anything but the sentinel plus users with the two anchored loopback rules.
func checkAuthFile(data []byte) error {
	if fault("allow-empty") || fault("wide-ports") {
		return nil
	}
	if err := checkNoDuplicates(data); err != nil {
		return err
	}
	var raw map[string][]string
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	sentinel := false
	names := map[string]bool{}
	computePorts := map[int]bool{}
	for key, rules := range raw {
		i := strings.IndexByte(key, ':')
		if i < 0 {
			return errors.New("a user without a password")
		}
		name, pass := key[:i], key[i+1:]
		if names[name] {
			return fmt.Errorf("user %s twice", name)
		}
		names[name] = true
		if len(rules) != 1 {
			return fmt.Errorf("user %s: %d rules, expected 1", name, len(rules))
		}
		r := rules[0]
		if name == SentinelName {
			if r != SentinelRule || !sentinelPassRE.MatchString(pass) {
				return errors.New("the sentinel user is not as written")
			}
			sentinel = true
			continue
		}
		if !validName(name) || !validStaticPass(pass) {
			return fmt.Errorf("user %q: bad name or password", name)
		}
		if m := computeRuleRE.FindStringSubmatch(r); m != nil {
			p, _ := strconv.Atoi(m[1])
			if !validPort(p) || m[1][0] == '0' {
				return fmt.Errorf("user %s: port %s", name, m[1])
			}
			if computePorts[p] {
				return fmt.Errorf("compute port %d twice", p)
			}
			computePorts[p] = true
			continue
		}
		if m := workRuleRE.FindStringSubmatch(r); m != nil {
			for _, ps := range strings.Split(m[1], "|") {
				p, _ := strconv.Atoi(ps)
				if !validPort(p) || ps[0] == '0' {
					return fmt.Errorf("user %s: port %s", name, ps)
				}
			}
			continue
		}
		return fmt.Errorf("user %s: rule %q is not one of the two allowed shapes", name, r)
	}
	if !sentinel {
		return errors.New("no sentinel user: chisel would turn authentication off")
	}
	return nil
}
