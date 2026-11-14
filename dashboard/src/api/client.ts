// The only way the page talks to anything: same-origin GETs to /api with the X-Mu-Token header.
import { token } from "./token";

export class ServerDown extends Error {} // fetch itself failed: the dashboard server is not running
export class NoToken extends Error {} // 401: no token, or one from an earlier server start
export class ApiFailed extends Error {
  constructor(readonly status: number, readonly error: string) {
    super(error);
  }
}

export async function apiGet(path: string): Promise<unknown> {
  const t = token();
  if (t === null) throw new NoToken("no token");
  let r: Response;
  try {
    r = await fetch(path, { headers: { "X-Mu-Token": t }, cache: "no-store", credentials: "omit" });
  } catch {
    throw new ServerDown("the dashboard server is not running");
  }
  if (r.status === 401) throw new NoToken("stale token");
  let body: unknown = null;
  try {
    body = await r.json();
  } catch {
    throw new ApiFailed(r.status, "bad-json");
  }
  if (!r.ok) {
    const e = (body as { error?: unknown }).error;
    throw new ApiFailed(r.status, typeof e === "string" ? e.slice(0, 40) : "error");
  }
  return body;
}

// The one state-changing request of phase 1a: ask a refused node again (POST /api/nodes/<name>/retry). Same origin,
// so the browser adds Origin and Sec-Fetch-Site: same-origin, which the server requires besides the token.
export async function apiPost(path: string): Promise<void> {
  const t = token();
  if (t === null) throw new NoToken("no token");
  let r: Response;
  try {
    r = await fetch(path, { method: "POST", headers: { "X-Mu-Token": t }, cache: "no-store", credentials: "omit" });
  } catch {
    throw new ServerDown("the dashboard server is not running");
  }
  if (r.status === 401) throw new NoToken("stale token");
  if (!r.ok) throw new ApiFailed(r.status, "post");
}
