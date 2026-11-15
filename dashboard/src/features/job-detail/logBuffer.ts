// The page's copy of a job's log (docs/design/dashboard.md §5.3 LogView): the last 5,000 lines or 2 MB, whichever is
// smaller; one line is cut at 10 kB with "… N bytes cut". Text arrives in pieces by byte offset; a piece may end in
// the middle of a line, which the next piece continues. Pure data: tested in Vitest.
export const MAX_LINES = 5000;
export const MAX_CHARS = 2 * 1024 * 1024;
export const LINE_CAP = 10 * 1024;

export type Line = { text: string; cut: number };

export class LogBuffer {
  lines: Line[] = [];
  open = false; // the last line has no newline yet
  chars = 0;
  dropped = 0; // lines dropped from the front (the cap)

  append(text: string): void {
    if (!text) return;
    const parts = text.split("\n");
    for (let i = 0; i < parts.length; i++) {
      const p = parts[i]!;
      const last = i === parts.length - 1;
      if (i === 0 && this.open && this.lines.length > 0) {
        this.extend(this.lines[this.lines.length - 1]!, p);
      } else if (!(last && p === "")) {
        const l: Line = { text: "", cut: 0 };
        this.extend(l, p);
        this.lines.push(l);
      }
      if (!last) this.open = false;
    }
    this.open = !text.endsWith("\n");
    this.trim();
  }

  private extend(l: Line, more: string): void {
    const room = LINE_CAP - l.text.length;
    if (room >= more.length) {
      l.text += more;
      this.chars += more.length;
    } else {
      if (room > 0) {
        l.text += more.slice(0, room);
        this.chars += room;
      }
      l.cut += new TextEncoder().encode(more.slice(Math.max(room, 0))).length;
    }
  }

  private trim(): void {
    let drop = 0;
    while ((this.lines.length - drop > MAX_LINES || this.chars > MAX_CHARS) && drop < this.lines.length - 1) {
      this.chars -= this.lines[drop]!.text.length;
      drop++;
    }
    if (drop > 0) {
      this.lines.splice(0, drop);
      this.dropped += drop;
    }
  }

  /** The text to show, one string (a single text node: fast for 5,000 lines). */
  render(): string {
    let s = "";
    for (let i = 0; i < this.lines.length; i++) {
      const l = this.lines[i]!;
      s += l.text;
      if (l.cut > 0) s += ` … ${l.cut} bytes cut`;
      if (i < this.lines.length - 1) s += "\n";
    }
    return s;
  }
}
