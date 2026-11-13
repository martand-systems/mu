// rawchan: a relay client that is NOT chisel's. chisel's own client only asks for the tunnels it declared when it
// connected, so the access rows made with it (X6-X8) show that the relay checks the declaration. This one logs in
// with real credentials, declares what the client is allowed, and then asks for SSH channels to other places. The
// relay has to refuse every such channel by itself (chisel >= 1.11.5 checks the rules on each channel).
//
//	AUTH=user:password rawchan URL CAFILE FINGERPRINT DECLARED TARGET...
//
//	URL          https://relay.martand.systems
//	CAFILE       the CA that signed the relay's TLS certificate
//	FINGERPRINT  the relay key's fingerprint; anything else is refused, as the chisel client does
//	DECLARED     the remote sent in the handshake, e.g. 127.0.0.1:7001:127.0.0.1:7001; "-" declares none
//	TARGET       host:port asked for in a "chisel" channel; on an opened channel it sends GET /ping
//
// Prints one line per step: "config accepted", "config refused: ...", "channel T OPENED <first body line>",
// "channel T REJECTED <reason>". Exit 0 if the log-in worked, whatever happened to the channels.
package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gorilla/websocket"
	"golang.org/x/crypto/ssh"
)

// The same field names as chisel's settings.Remote and settings.Config (share/settings at v1.12.0).
type remote struct {
	LocalHost, LocalPort, LocalProto    string
	RemoteHost, RemotePort, RemoteProto string
	Socks, Reverse, Stdio               bool
}
type config struct {
	Version string
	Remotes []*remote
}

// wsConn makes a websocket a net.Conn for the SSH library: one binary message per write (chisel's share/cnet).
type wsConn struct {
	*websocket.Conn
	buf []byte
}

func (c *wsConn) Read(p []byte) (int, error) {
	if len(c.buf) == 0 {
		_, msg, err := c.Conn.ReadMessage()
		if err != nil {
			return 0, err
		}
		c.buf = msg
	}
	n := copy(p, c.buf)
	c.buf = c.buf[n:]
	return n, nil
}
func (c *wsConn) Write(p []byte) (int, error) {
	if err := c.Conn.WriteMessage(websocket.BinaryMessage, p); err != nil {
		return 0, err
	}
	return len(p), nil
}
func (c *wsConn) SetDeadline(t time.Time) error {
	if err := c.Conn.SetReadDeadline(t); err != nil {
		return err
	}
	return c.Conn.SetWriteDeadline(t)
}

func die(format string, a ...any) {
	fmt.Printf(format+"\n", a...)
	os.Exit(2)
}

func parseDeclared(s string) []*remote {
	if s == "-" {
		return []*remote{}
	}
	r := &remote{LocalProto: "tcp", RemoteProto: "tcp"}
	if strings.HasPrefix(s, "R:") {
		r.Reverse = true
		s = s[2:]
	}
	p := strings.Split(s, ":")
	if len(p) != 4 {
		die("DECLARED must be [R:]host:port:host:port or -")
	}
	r.LocalHost, r.LocalPort, r.RemoteHost, r.RemotePort = p[0], p[1], p[2], p[3]
	return []*remote{r}
}

func main() {
	if len(os.Args) < 5 {
		die("usage: AUTH=user:password rawchan URL CAFILE FINGERPRINT DECLARED TARGET...")
	}
	url, cafile, fingerprint, declared, targets := os.Args[1], os.Args[2], os.Args[3], os.Args[4], os.Args[5:]
	user, pass, _ := strings.Cut(os.Getenv("AUTH"), ":")

	pem, err := os.ReadFile(cafile)
	if err != nil {
		die("ca: %v", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		die("ca: no certificate in %s", cafile)
	}
	d := websocket.Dialer{
		Subprotocols:     []string{"chisel-v3"},
		HandshakeTimeout: 10 * time.Second,
		TLSClientConfig:  &tls.Config{RootCAs: pool},
	}
	if to := os.Getenv("RAWCHAN_DIAL"); to != "" { // host:port to connect to instead of the URL's (the name stays)
		d.NetDialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, network, to)
		}
	}
	ws, _, err := d.Dial(strings.Replace(url, "http", "ws", 1), http.Header{})
	if err != nil {
		die("websocket: %v", err)
	}
	sc, chans, reqs, err := ssh.NewClientConn(&wsConn{Conn: ws}, "", &ssh.ClientConfig{
		User:          user,
		Auth:          []ssh.AuthMethod{ssh.Password(pass)},
		ClientVersion: "SSH-chisel-v3-client",
		Timeout:       10 * time.Second,
		HostKeyCallback: func(_ string, _ net.Addr, key ssh.PublicKey) error {
			sum := sha256.Sum256(key.Marshal())
			if got := base64.StdEncoding.EncodeToString(sum[:]); got != fingerprint {
				return fmt.Errorf("invalid fingerprint (%s)", got)
			}
			return nil
		},
	})
	if err != nil {
		die("ssh: %v", err)
	}
	defer sc.Close()
	go ssh.DiscardRequests(reqs)
	go func() {
		for ch := range chans {
			ch.Reject(ssh.Prohibited, "not served")
		}
	}()

	payload, _ := json.Marshal(config{Version: "1.12.0", Remotes: parseDeclared(declared)})
	_, reply, err := sc.SendRequest("config", true, payload)
	if err != nil {
		die("config: %v", err)
	}
	if len(reply) > 0 {
		fmt.Printf("config refused: %s\n", reply)
		return
	}
	fmt.Println("config accepted")

	for _, target := range targets {
		type opened struct {
			ch   ssh.Channel
			reqs <-chan *ssh.Request
			err  error
		}
		got := make(chan opened, 1)
		go func() {
			ch, r, err := sc.OpenChannel("chisel", []byte(target))
			got <- opened{ch, r, err}
		}()
		var o opened
		select {
		case o = <-got:
		case <-time.After(40 * time.Second): // longer than the relay's 30 s dial timeout
			fmt.Printf("channel %s NOANSWER\n", target)
			continue
		}
		if o.err != nil {
			fmt.Printf("channel %s REJECTED %s\n", target, strings.ReplaceAll(o.err.Error(), "\n", " "))
			continue
		}
		go ssh.DiscardRequests(o.reqs)
		fmt.Fprintf(o.ch, "GET /ping HTTP/1.0\r\nHost: %s\r\n\r\n", target)
		answer := make(chan string, 1)
		go func() {
			body, _ := io.ReadAll(io.LimitReader(o.ch, 4096))
			head, rest, _ := strings.Cut(string(body), "\r\n\r\n")
			status, _, _ := strings.Cut(head, "\r\n")
			line, _ := bufio.NewReader(strings.NewReader(rest)).ReadString('\n')
			answer <- strings.TrimSpace(status) + " | " + strings.TrimSpace(line)
		}()
		select {
		case a := <-answer:
			fmt.Printf("channel %s OPENED %s\n", target, a)
		case <-time.After(5 * time.Second):
			fmt.Printf("channel %s OPENED (no answer in 5 s)\n", target)
		}
		o.ch.Close()
	}
}
