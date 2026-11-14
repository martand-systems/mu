// The token travels from `mu dashboard open` in the URL's fragment (#t=…), never in a query, a cookie or a log
// (docs/design/dashboard.md §4.6). On boot it moves into localStorage and leaves the address bar and the history.
const KEY = "mu.token";
const TOKEN = /^[0-9a-f]{64}$/;

export function takeTokenFromFragment(): boolean {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const t = params.get("t");
  if (t === null) return false;
  params.delete("t");
  const rest = params.toString();
  if (!__FAULT_KEEP_FRAGMENT__) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search + (rest ? "#" + rest : ""));
  }
  if (!TOKEN.test(t)) return false;
  try {
    window.localStorage.setItem(KEY, t);
  } catch {
    return false;
  }
  return true;
}

export function token(): string | null {
  try {
    const t = window.localStorage.getItem(KEY);
    return t !== null && TOKEN.test(t) ? t : null;
  } catch {
    return null;
  }
}
