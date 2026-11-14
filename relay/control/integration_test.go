package main

// mu-relayd with the real chisel 1.12.0, on loopback, no Docker: reloads without a restart, eviction, the rules of
// dynamic clients, and an auth file that never opens chisel up. Runs when MU_RELAYD_CHISEL names a chisel binary
// (CI's go job downloads the pinned release; tools/s4 repeats these through Traefik). Ports 7931-7934 must be free.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

type proc struct {
	cmd *exec.Cmd
	mu  sync.Mutex
	out bytes.Buffer
}

func (p *proc) Write(b []byte) (int, error) { p.mu.Lock(); defer p.mu.Unlock(); return p.out.Write(b) }
func (p *proc) text() string                { p.mu.Lock(); defer p.mu.Unlock(); return p.out.String() }
func (p *proc) count(s string) int          { return strings.Count(p.text(), s) }

func startProc(t *testing.T, env []string, name string, args ...string) *proc {
	t.Helper()
	p := &proc{}
	p.cmd = exec.Command(name, args...)
	p.cmd.Env = env
	p.cmd.Stdout, p.cmd.Stderr = p, p
	p.cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} // mu-relayd's chisel child goes with it
	if err := p.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { syscall.Kill(-p.cmd.Process.Pid, syscall.SIGKILL); p.cmd.Wait() })
	return p
}

func freePort(t *testing.T) int {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

func until(d time.Duration, f func() bool) bool {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		if f() {
			return true
		}
		time.Sleep(100 * time.Millisecond)
	}
	return f()
}

func httpGet(url string) string {
	c := http.Client{Timeout: 3 * time.Second}
	r, err := c.Get(url)
	if err != nil {
		return "error: " + err.Error()
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	return string(b)
}

func TestIntegrationWithChisel(t *testing.T) {
	chisel := os.Getenv("MU_RELAYD_CHISEL")
	if chisel == "" {
		t.Skip("MU_RELAYD_CHISEL is not set")
	}
	dir := t.TempDir()
	bin := filepath.Join(dir, "mu-relayd")
	args := []string{"build", "-o", bin}
	if faultsBuilt {
		args = append(args, "-tags", "faults")
	}
	if out, err := exec.Command("go", append(args, ".")...).CombinedOutput(); err != nil {
		t.Fatalf("build: %v\n%s", err, out)
	}
	key, err := exec.Command(chisel, "server", "--keygen", "-").Output()
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	run, state := filepath.Join(dir, "run"), filepath.Join(dir, "state")
	os.MkdirAll(run, 0o700)
	os.MkdirAll(state, 0o700)
	pwMac, pwPC := "static-mac-password-0123456789", "static-pc-password-01234567890"
	os.WriteFile(filepath.Join(run, "static.tsv"), []byte("workstation\tavi-mac\t"+pwMac+"\t7931\ncompute\toffice-pc\t"+pwPC+"\t7931\n"), 0o600)
	cport, aport := freePort(t), freePort(t)
	conf := fmt.Sprintf("key=%s\nadmin_token=%s\nkeepalive=25s\nrevoked=\nchisel=%s\nrun_dir=%s\nstate_dir=%s\nlisten=127.0.0.1:%d\nchisel_port=%d\nreload_ms=300\n",
		strings.TrimSpace(string(key)), testToken, chisel, run, state, aport, cport)
	os.WriteFile(filepath.Join(run, "relayd.conf"), []byte(conf), 0o600)
	env := []string{"PATH=" + os.Getenv("PATH")}
	if f := os.Getenv("MU_RELAY_FAULT"); f != "" {
		env = append(env, "MU_RELAY_FAULT="+f)
	}
	relayd := startProc(t, env, bin, "--config", filepath.Join(run, "relayd.conf"))
	base := fmt.Sprintf("http://127.0.0.1:%d", cport)
	if !until(10*time.Second, func() bool { return httpGet(base+"/health") == "OK\n" }) {
		t.Fatalf("no /health through chisel:\n%s", relayd.text())
	}
	if _, err := os.Stat(filepath.Join(run, "relayd.conf")); err == nil {
		t.Error("relayd.conf (the key and the token) was not removed after reading")
	}
	if s := httpGet(base + "/mu/v1/"); !strings.Contains(s, `"service":"mu-relay"`) {
		t.Fatalf("/mu/v1/ through chisel's --backend: %s", s)
	}
	api := func(method, path string, body any) (int, string) {
		var rd io.Reader
		if body != nil {
			b, _ := json.Marshal(body)
			rd = bytes.NewReader(b)
		}
		req, _ := http.NewRequest(method, base+path, rd)
		req.Header.Set("Authorization", "Bearer "+testToken)
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0, err.Error()
		}
		defer res.Body.Close()
		b, _ := io.ReadAll(res.Body)
		return res.StatusCode, string(b)
	}
	svc1 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "office-pc") }))
	defer svc1.Close()
	svc2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "lab-pc") }))
	defer svc2.Close()
	hostport := func(u string) string { return strings.TrimPrefix(u, "http://") }
	client := func(auth string, remote string, extra ...string) *proc {
		a := append([]string{"client", "--max-retry-interval", "1s"}, extra...)
		a = append(a, base, remote)
		return startProc(t, []string{"PATH=" + os.Getenv("PATH"), "AUTH=" + auth}, chisel, a...)
	}
	connected := func(p *proc) bool { return p.count("client: Connected") >= 1 }

	// static clients, as in legacy mode
	pc := client("office-pc:"+pwPC, "R:127.0.0.1:7931:"+hostport(svc1.URL))
	l1 := freePort(t)
	mac := client("avi-mac:"+pwMac, fmt.Sprintf("127.0.0.1:%d:127.0.0.1:7931", l1))
	if !until(10*time.Second, func() bool { return httpGet(fmt.Sprintf("http://127.0.0.1:%d/", l1)) == "office-pc" }) {
		t.Fatalf("static tunnel:\n%s\n%s", pc.text(), mac.text())
	}

	// a dynamic client is accepted after the auth file is rewritten, with no restart, existing tunnels untouched
	pwLab := strings.Repeat("L", 40)
	if st, b := api("PUT", "/mu/v1/clients/lab-pc", map[string]any{"role": "compute", "password": pwLab, "ports": []int{7932}}); st != 201 {
		t.Fatalf("PUT: %d %s", st, b)
	}
	lab := client("lab-pc:"+pwLab, "R:127.0.0.1:7932:"+hostport(svc2.URL), "--max-retry-count", "0")
	if !until(3*time.Second, func() bool { return connected(lab) }) {
		t.Fatalf("I3 the dynamic client was not accepted within 3 s:\n%s", lab.text())
	}
	if st, b := api("PUT", "/mu/v1/grants/avi-mac", map[string]any{"ports": []int{7932}}); st != 200 {
		t.Fatalf("grant: %d %s", st, b)
	}
	l2 := freePort(t)
	mac2 := client("avi-mac:"+pwMac, fmt.Sprintf("127.0.0.1:%d:127.0.0.1:7932", l2), "--max-retry-count", "0")
	if !until(5*time.Second, func() bool { return httpGet(fmt.Sprintf("http://127.0.0.1:%d/", l2)) == "lab-pc" }) {
		t.Fatalf("I4 the granted port:\n%s", mac2.text())
	}
	if strings.Contains(relayd.text(), "chisel restarted") || pc.count("client: Disconnected") != 0 || mac.count("client: Disconnected") != 0 {
		t.Fatalf("I3 adding clients restarted chisel or dropped a tunnel:\n%s", relayd.text())
	}
	if httpGet(fmt.Sprintf("http://127.0.0.1:%d/", l1)) != "office-pc" {
		t.Fatal("I3 the first tunnel stopped working")
	}

	// a dynamic workstation reaches its ports only
	pwW := strings.Repeat("W", 40)
	if st, b := api("PUT", "/mu/v1/clients/ws2", map[string]any{"role": "workstation", "password": pwW, "ports": []int{7932}}); st != 201 {
		t.Fatalf("PUT ws2: %d %s", st, b)
	}
	deny := client("ws2:"+pwW, fmt.Sprintf("127.0.0.1:%d:127.0.0.1:7931", freePort(t)), "--max-retry-count", "0")
	denyRev := client("ws2:"+pwW, "R:127.0.0.1:7933:127.0.0.1:1", "--max-retry-count", "0")
	labOther := client("lab-pc:"+pwLab, "R:127.0.0.1:7934:127.0.0.1:1", "--max-retry-count", "0")
	if !until(5*time.Second, func() bool {
		return strings.Contains(deny.text(), "denied") && strings.Contains(denyRev.text(), "denied") && strings.Contains(labOther.text(), "denied")
	}) || connected(deny) || connected(denyRev) || connected(labOther) {
		t.Fatalf("I5 a dynamic client got more than its rule:\n%s\n%s\n%s", deny.text(), denyRev.text(), labOther.text())
	}

	// removing a client cuts its established session (eviction) and refuses it afterwards
	if st, b := api("DELETE", "/mu/v1/clients/lab-pc", nil); st != 200 {
		t.Fatalf("DELETE: %d %s", st, b)
	}
	if !until(8*time.Second, func() bool { return lab.count("client: Disconnected") >= 1 }) {
		t.Fatalf("I6 the removed client's session was not cut:\n%s\n%s", lab.text(), relayd.text())
	}
	// the client sees the cut while the old chisel drains, before the new one listens: a single try in that gap is
	// refused by the network, not by the auth file (CI run 43571059503), so wait for the restart to finish first
	if !until(15*time.Second, func() bool {
		return strings.Contains(relayd.text(), "chisel restarted") && httpGet(base+"/health") == "OK\n"
	}) {
		t.Fatalf("I6 chisel did not come back after the eviction:\n%s", relayd.text())
	}
	again := client("lab-pc:"+pwLab, "R:127.0.0.1:7932:"+hostport(svc2.URL), "--max-retry-count", "0")
	if !until(5*time.Second, func() bool { return strings.Contains(strings.ToLower(again.text()), "authentication failed") }) || connected(again) {
		t.Fatalf("I6 the removed client connected again:\n%s", again.text())
	}
	if !until(20*time.Second, func() bool { return httpGet(fmt.Sprintf("http://127.0.0.1:%d/", l1)) == "office-pc" }) {
		t.Fatalf("I6 the other clients did not come back:\n%s", pc.text())
	}

	// the auth file never opens chisel up: removing every client is refused at the last one, and the sentinel stays
	for _, n := range []string{"ws2", "office-pc"} {
		if st, b := api("DELETE", "/mu/v1/clients/"+n, nil); st != 200 {
			t.Fatalf("DELETE %s: %d %s", n, st, b)
		}
	}
	st, b := api("DELETE", "/mu/v1/clients/avi-mac", nil)
	time.Sleep(500 * time.Millisecond)
	anyone := client("nobody:wrong-password-0000000000000000", fmt.Sprintf("127.0.0.1:%d:127.0.0.1:%d", freePort(t), aport), "--max-retry-count", "0")
	until(5*time.Second, func() bool {
		return connected(anyone) || strings.Contains(strings.ToLower(anyone.text()), "authentication failed")
	})
	if connected(anyone) {
		t.Errorf("I7 chisel let in a client with no valid password (an open proxy):\n%s", anyone.text())
	}
	if st != 409 {
		t.Fatalf("I7 removing the last client: %d %s", st, b)
	}
	for _, secret := range []string{testToken, pwLab, pwW, pwMac, pwPC, strings.TrimSpace(string(key))} {
		if strings.Contains(relayd.text(), secret) {
			t.Fatal("a secret in mu-relayd's or chisel's output")
		}
	}
}
