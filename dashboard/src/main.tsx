// Boot: move #t= into localStorage (and out of the address bar), tell the server the redirect file can go, render.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { apiGet } from "./api/client";
import { takeTokenFromFragment } from "./api/token";
import "./styles/index.css";

if (takeTokenFromFragment()) {
  apiGet("/api/session?first=1").catch(() => undefined);
}
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
