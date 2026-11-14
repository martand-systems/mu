// mu-relayd: the μ relay's pairing and control service (docs/design/pairing.md §6). It runs as the container's main
// process when RELAY_ADMIN_TOKEN is set: it writes chisel's auth file from the static clients (relay/entrypoint.sh's
// static.tsv) and the dynamic ones on the state volume, serves the API on 127.0.0.1:8081 behind chisel's --backend,
// and supervises chisel.
//
//	mu-relayd --config /run/relay/relayd.conf
//
// The config file (written by the entrypoint, 0600, removed after reading) holds key=value lines: key, admin_token,
// keepalive, revoked, and for tests chisel, run_dir, state_dir, listen, chisel_port, reload_ms.
package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var adminTokenRE = regexp.MustCompile(`^mra_[A-Za-z0-9_-]{43,128}$`)
var keepaliveRE = regexp.MustCompile(`^[0-9]+[sm]$`)

func readConfig(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	out := map[string]string{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, found := strings.Cut(line, "=")
		if !found {
			return nil, errors.New("a line without =")
		}
		out[k] = v
	}
	return out, sc.Err()
}

func get(c map[string]string, k, def string) string {
	if v, ok := c[k]; ok && v != "" {
		return v
	}
	return def
}

func main() {
	logger := log.New(os.Stderr, "mu-relayd: ", 0)
	logf := func(format string, a ...any) { logger.Printf(format, a...) }
	if len(os.Args) != 3 || os.Args[1] != "--config" {
		fmt.Fprintln(os.Stderr, "usage: mu-relayd --config FILE")
		os.Exit(64)
	}
	conf, err := readConfig(os.Args[2])
	if err != nil {
		logf("config: %v", err)
		os.Exit(64)
	}
	if get(conf, "keep_config", "") != "1" {
		os.Remove(os.Args[2]) // the key and the token are in it
	}
	key := conf["key"]
	token := conf["admin_token"]
	if !strings.HasPrefix(key, "ck-") {
		logf("config: no chisel key")
		os.Exit(64)
	}
	if !adminTokenRE.MatchString(token) {
		logf("config: the admin token must be mra_ and 43 to 128 characters of A-Za-z0-9_- (not printed)")
		os.Exit(64)
	}
	keepalive := get(conf, "keepalive", "25s")
	if !keepaliveRE.MatchString(keepalive) {
		logf("config: keepalive %q", keepalive)
		os.Exit(64)
	}
	revoked := map[string]bool{}
	for _, n := range strings.Split(conf["revoked"], ",") {
		if n = strings.TrimSpace(n); n != "" {
			if !validName(n) {
				logf("config: revoked name %q", n)
				os.Exit(64)
			}
			revoked[n] = true
		}
	}
	runDir := get(conf, "run_dir", "/run/relay")
	stateDir := get(conf, "state_dir", "/var/lib/mu-relay")
	listen := get(conf, "listen", "127.0.0.1:8081")
	chiselPath := get(conf, "chisel", "/usr/local/bin/chisel")
	chiselPort := get(conf, "chisel_port", "8080")
	reloadMS, _ := strconv.Atoi(get(conf, "reload_ms", "300"))
	if faultsBuilt {
		logf("built with fault hooks (tests only); active: %q", os.Getenv("MU_RELAY_FAULT"))
	}

	static, err := readStatic(filepath.Join(runDir, "static.tsv"))
	if err != nil {
		logf("static clients: %v", err)
		os.Exit(64)
	}
	os.Clearenv()

	sup := &Supervisor{Path: chiselPath, MinInterval: 10 * time.Second, StopWait: 3 * time.Second, Delay: time.Second, Logf: logf,
		Args: []string{"server", "--host", "0.0.0.0", "--port", chiselPort, "--reverse",
			"--authfile", filepath.Join(runDir, "users.json"), "--keepalive", keepalive,
			"--backend", "http://" + listen},
		Env: []string{"PATH=/usr/local/bin:/usr/bin:/bin", "CHISEL_KEY_FILE=" + key, "CHISEL_SHUTDOWN_GRACE=2s"}}
	relay, err := NewRelay(Config{StateDir: stateDir, RunDir: runDir, AdminToken: token, Revoked: revoked,
		Static: static, ReloadDelay: time.Duration(reloadMS) * time.Millisecond, Logf: logf}, sup)
	if err != nil {
		logf("%v", err)
		os.Exit(70)
	}
	n := len(relay.entries(relay.st))
	logf("store %s (%s), %d client(s) in the auth file, %d revoked; API on %s", relay.st.StoreID, relay.mode, n, len(revoked), listen)

	ln, err := net.Listen("tcp", listen)
	if err != nil {
		logf("listen %s: %v", listen, err)
		os.Exit(70)
	}
	srv := &http.Server{Handler: relay, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second,
		WriteTimeout: 40 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 8 << 10,
		ErrorLog: log.New(os.Stderr, "mu-relayd: http: ", 0)}
	go func() {
		if err := srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			logf("serve: %v", err)
			os.Exit(70)
		}
	}()

	exit := make(chan int, 1)
	sup.OnExit = func(err error) {
		logf("chisel exited by itself (%v): exiting so that Docker restarts the container", err)
		exit <- 70
	}
	if err := sup.Start(); err != nil {
		logf("chisel did not start: %v", err)
		os.Exit(70)
	}
	go func() {
		t := time.NewTicker(time.Second)
		for range t.C {
			relay.mu.Lock()
			relay.applyDeadlines()
			relay.mu.Unlock()
		}
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	code := 0
	select {
	case s := <-sig:
		logf("%v: stopping", s)
	case code = <-exit:
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	srv.Shutdown(ctx)
	cancel()
	sup.Stop()
	os.Exit(code)
}
