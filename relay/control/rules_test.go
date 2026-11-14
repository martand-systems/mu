package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// The vectors of tools/s4/test-config.sh: what the entrypoint accepts.
var entrypointVectors = []string{
	"compute:office-pc:0123456789abcdef0123456789abcdef0123456789abcdef:7001,workstation:avi-mac:fedcba9876543210fedcba9876543210fedcba9876543210:7001+7002",
	"workstation:a:0123456789abcdef01234567:7001",
	"compute:a:0123456789abcdef01234567:7001,compute:b:0123456789abcdef01234567:7999,workstation:c-1:ABCDEFGHIJKLMNOPQRSTUVWX_-:7001+7999+7500",
}

// runEntrypoint runs relay/entrypoint.sh with a stand-in for chisel (legacy) or for mu-relayd (admin mode).
func runEntrypoint(t *testing.T, clients, token, revoked string) string {
	t.Helper()
	dir := t.TempDir()
	run := filepath.Join(dir, "run")
	os.MkdirAll(run, 0o700)
	stub := filepath.Join(dir, "stub")
	os.WriteFile(stub, []byte("#!/bin/sh\nexit 0\n"), 0o755)
	cmd := exec.Command("sh", "../entrypoint.sh")
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "RELAY_RUN_DIR=" + run, "RELAY_CHISEL=" + stub, "RELAY_RELAYD=" + stub,
		"RELAY_KEY=ck-abc", "RELAY_CLIENTS=" + clients, "RELAY_ADMIN_TOKEN=" + token, "RELAY_REVOKED=" + revoked}
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("entrypoint: %v\n%s", err, out)
	}
	return run
}

func TestRulesEqualEntrypoint(t *testing.T) {
	for _, v := range entrypointVectors {
		legacy := runEntrypoint(t, v, "", "")
		var want map[string][]string
		b, err := os.ReadFile(filepath.Join(legacy, "users.json"))
		if err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(b, &want); err != nil {
			t.Fatal(err)
		}
		admin := runEntrypoint(t, v, testToken, "")
		static, err := readStatic(filepath.Join(admin, "static.tsv"))
		if err != nil {
			t.Fatal(err)
		}
		entries, _ := merge(static, newState(0), nil)
		var got map[string][]string
		rendered := render(entries, newSentinelPassword())
		if err := checkAuthFile(rendered); err != nil {
			t.Fatalf("%s: %v", v, err)
		}
		json.Unmarshal(rendered, &got)
		for k := range got {
			if strings.HasPrefix(k, SentinelName+":") {
				delete(got, k)
			}
		}
		gb, _ := json.Marshal(got)
		wb, _ := json.Marshal(want)
		if string(gb) != string(wb) {
			t.Errorf("%s:\n mu-relayd  %s\n entrypoint %s", v, gb, wb)
		}
		conf, _ := os.ReadFile(filepath.Join(admin, "relayd.conf"))
		if !strings.Contains(string(conf), "admin_token="+testToken+"\n") || !strings.Contains(string(conf), "key=ck-abc\n") {
			t.Errorf("relayd.conf: %q", conf)
		}
		if _, err := os.Stat(filepath.Join(admin, "users.json")); err == nil {
			t.Errorf("admin mode: the entrypoint wrote users.json itself")
		}
	}
}

func TestEntrypointRevoked(t *testing.T) {
	run := runEntrypoint(t, entrypointVectors[0], testToken, "office-pc")
	static, _ := readStatic(filepath.Join(run, "static.tsv"))
	if len(static) != 1 || static[0].Name != "avi-mac" {
		t.Fatalf("static.tsv with office-pc revoked: %+v", static)
	}
	run = runEntrypoint(t, entrypointVectors[0], "", "office-pc")
	b, _ := os.ReadFile(filepath.Join(run, "users.json"))
	if strings.Contains(string(b), "office-pc") || !strings.Contains(string(b), "avi-mac") {
		t.Fatalf("legacy users.json with office-pc revoked: %s", b)
	}
}

// chisel's check: any rule that matches the address (share/settings/user.go HasAccess uses MatchString).
func allowed(rule, addr string) bool { return regexp.MustCompile(rule).MatchString(addr) }

func TestRuleSemantics(t *testing.T) {
	c := ruleFor(Entry{Role: RoleCompute, Ports: []int{7001}})
	w := ruleFor(Entry{Role: RoleWorkstation, Ports: []int{7001, 7002}})
	if c != `^R:127\.0\.0\.1:7001$` || w != `^127\.0\.0\.1:(7001|7002)$` {
		t.Fatalf("rules: %s %s", c, w)
	}
	yes := [][2]string{{c, "R:127.0.0.1:7001"}, {w, "127.0.0.1:7001"}, {w, "127.0.0.1:7002"}}
	no := [][2]string{{c, "R:127.0.0.1:7002"}, {c, "R:0.0.0.0:7001"}, {c, "R:127.0.0.1:70010"}, {c, "127.0.0.1:7001"},
		{c, "xR:127.0.0.1:7001"}, {c, "R:127x0.0.1:7001"}, {c, "socks"}, {c, "R:socks"}, {w, "R:127.0.0.1:7001"},
		{w, "127.0.0.1:7003"}, {w, "127.0.0.1:70011"}, {w, "127.0.0.1:8080"}, {w, "127.0.0.1:8081"},
		{w, "dokploy-traefik:8080"}, {w, "10.0.0.1:7001"}, {w, "1127.0.0.1:7001"}, {w, "127a0b0c1:7001"},
		{w, "socks"}, {w, "evil.example:443"}, {w, "127.0.0.1:7999"}}
	for _, y := range yes {
		if !allowed(y[0], y[1]) {
			t.Errorf("%s should allow %s", y[0], y[1])
		}
	}
	for _, n := range no {
		if allowed(n[0], n[1]) {
			t.Errorf("%s allows %s", n[0], n[1])
		}
	}
	if allowed(SentinelRule, "-") != true || allowed(SentinelRule, "127.0.0.1:7001") || allowed(SentinelRule, "R:127.0.0.1:7001") {
		t.Error("the sentinel rule")
	}
}

func TestCheckAuthFileRefuses(t *testing.T) {
	s := `"` + SentinelName + `:` + newSentinelPassword() + `": ["^-$"]`
	good := `{` + s + `, "a:0123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7001$"]}`
	if err := checkAuthFile([]byte(good)); err != nil {
		t.Fatalf("good file refused: %v", err)
	}
	bad := map[string]string{
		"empty":           `{}`,
		"no sentinel":     `{"a:0123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7001$"]}`,
		"allow all":       `{` + s + `, "a:0123456789abcdef01234567": [""]}`,
		"star":            `{` + s + `, "a:0123456789abcdef01234567": ["*"]}`,
		"unanchored":      `{` + s + `, "a:0123456789abcdef01234567": ["R:127\\.0\\.0\\.1:7001"]}`,
		"other address":   `{` + s + `, "a:0123456789abcdef01234567": ["^R:0\\.0\\.0\\.0:7001$"]}`,
		"port 8080":       `{` + s + `, "a:0123456789abcdef01234567": ["^127\\.0\\.0\\.1:(8080)$"]}`,
		"two rules":       `{` + s + `, "a:0123456789abcdef01234567": ["^127\\.0\\.0\\.1:(7001)$", "^127\\.0\\.0\\.1:(7002)$"]}`,
		"no rule":         `{` + s + `, "a:0123456789abcdef01234567": []}`,
		"duplicate key":   `{` + s + `, "a:0123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7001$"], "a:0123456789abcdef01234567": [""]}`,
		"same name twice": `{` + s + `, "a:0123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7001$"], "a:x123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7002$"]}`,
		"port twice":      `{` + s + `, "a:0123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7001$"], "b:0123456789abcdef01234567": ["^R:127\\.0\\.0\\.1:7001$"]}`,
		"short password":  `{` + s + `, "a:short": ["^R:127\\.0\\.0\\.1:7001$"]}`,
		"wide ports":      `{` + s + `, "a:0123456789abcdef01234567": ["^127\\.0\\.0\\.1:(7[0-9]{3})$"]}`,
		"sentinel opened": `{"` + SentinelName + `:` + newSentinelPassword() + `": [""]}`,
	}
	for what, f := range bad {
		if err := checkAuthFile([]byte(f)); err == nil {
			t.Errorf("%s: accepted", what)
		}
	}
}

func TestDynamicClientChecks(t *testing.T) {
	e := newEnv(t, staticDefault())
	for what, body := range map[string]map[string]any{
		"short password":        {"role": "compute", "password": "short", "ports": []int{7002}},
		"long password":         {"role": "compute", "password": strings.Repeat("a", 129), "ports": []int{7002}},
		"password with a colon": {"role": "compute", "password": "aaaaaaaaaaaaaaaaaaaaaaaa:b", "ports": []int{7002}},
		"two compute ports":     {"role": "compute", "password": pwA, "ports": []int{7002, 7003}},
		"port 8081":             {"role": "workstation", "password": pwA, "ports": []int{8081}},
		"unknown role":          {"role": "admin", "password": pwA, "ports": []int{7002}},
	} {
		if r := e.do("PUT", "/mu/v1/clients/lab-pc", admin(), body); r.Status != 400 {
			t.Errorf("%s: %d %s", what, r.Status, r.Raw)
		}
	}
	if r := e.do("PUT", "/mu/v1/clients/lab-pc", admin(), map[string]any{"role": "compute", "password": pwA, "ports": []int{7001}}); r.Status != 409 || errCode(r) != "port-taken" {
		t.Errorf("a port of a static compute client: %s", r.Raw)
	}
	if r := e.do("PUT", "/mu/v1/clients/Lab", admin(), map[string]any{"role": "compute", "password": pwA, "ports": []int{7002}}); r.Status != 400 {
		t.Errorf("an upper-case name: %s", r.Raw)
	}
	e.mustStatus(e.do("PUT", "/mu/v1/clients/lab-ws", admin(), map[string]any{"role": "workstation", "password": pwA, "ports": []int{7001, 7005}}), 201, "workstation")
	if rules, _ := e.users(); rules["lab-ws"] != `^127\.0\.0\.1:(7001|7005)$` {
		t.Errorf("dynamic workstation rule: %s", rules["lab-ws"])
	}
}
