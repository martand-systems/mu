"""Expand a compressed Apple disk image (UDIF .dmg) into a raw disk image, using every CPU core.

    python dmg2raw.py IN.dmg OUT.img

hdiutil decompresses a mounted image on a single thread: FreeCAD's 650 MB bzip2 DMG takes ~140 s to copy out of.
UDIF stores its data as independent compressed chunks, so they can be expanded in parallel; the raw result is
then attached with hdiutil (no decompression) and copied out in seconds. Each chunk's codec verifies its own
data (bzip2 CRC, zlib Adler-32); the DMG itself was SHA-256-checked before this runs.

Exit status 3 means the image uses a chunk type this script doesn't handle: the caller falls back to hdiutil.
"""
import base64, bz2, os, plistlib, struct, sys, zlib
from concurrent.futures import ProcessPoolExecutor

SECTOR = 512
ZERO_TYPES = {0x00000000, 0x00000002}       # zero-fill / ignored ("free") runs
RAW, ZLIB, BZIP2 = 0x00000001, 0x80000005, 0x80000006
SKIP_TYPES = {0x7FFFFFFE, 0xFFFFFFFF}       # comment, terminator

def chunks(path):
    """Yield (type, out_offset, out_length, in_offset, in_length) for every chunk of every partition."""
    with open(path, "rb") as f:
        f.seek(-512, os.SEEK_END)
        koly = f.read(512)
        if koly[:4] != b"koly":
            sys.exit("not a UDIF disk image (no koly trailer)")
        data_fork_offset = struct.unpack(">Q", koly[24:32])[0]
        xml_offset, xml_length = struct.unpack(">QQ", koly[216:232])
        f.seek(xml_offset)
        plist = plistlib.loads(f.read(xml_length))
    for part in plist["resource-fork"]["blkx"]:
        mish = part["Data"]
        if mish[:4] != b"mish":
            continue
        first_sector = struct.unpack(">Q", mish[8:16])[0]
        data_offset = struct.unpack(">Q", mish[24:32])[0]   # chunk data offsets are relative to this (0 in Apple's images)
        n = struct.unpack(">I", mish[200:204])[0]
        for i in range(n):
            e = mish[204 + 40 * i: 244 + 40 * i]
            typ, _, sec, nsec, coff, clen = struct.unpack(">IIQQQQ", e)
            yield typ, (first_sector + sec) * SECTOR, nsec * SECTOR, data_fork_offset + data_offset + coff, clen

def expand(job):
    src, dst, typ, out_off, out_len, in_off, in_len = job
    with open(src, "rb") as f:
        f.seek(in_off)
        data = f.read(in_len)
    if typ == BZIP2:
        data = bz2.decompress(data)
    elif typ == ZLIB:
        data = zlib.decompress(data)
    if len(data) != out_len:
        raise ValueError(f"chunk at {out_off}: expanded to {len(data)} bytes, expected {out_len}")
    fd = os.open(dst, os.O_WRONLY)
    try:
        view, off = memoryview(data), out_off
        while view:                      # pwrite may write less than asked (e.g. disk full): never leave a gap
            n = os.pwrite(fd, view, off)
            if n <= 0:
                raise OSError(f"short write at {off}")
            view, off = view[n:], off + n
    finally:
        os.close(fd)
    return out_len

def main():
    src, dst = sys.argv[1], sys.argv[2]
    jobs, size = [], 0
    for typ, out_off, out_len, in_off, in_len in chunks(src):
        size = max(size, out_off + out_len)
        if typ in ZERO_TYPES or typ in SKIP_TYPES or out_len == 0:
            continue
        if typ not in (RAW, ZLIB, BZIP2):
            print(f"unsupported chunk type {typ:#x}", file=sys.stderr)
            sys.exit(3)
        jobs.append((src, dst, typ, out_off, out_len, in_off, in_len))
    with open(dst, "wb") as f:
        f.truncate(size)           # sparse: zero runs cost nothing
    jobs.sort(key=lambda j: -j[6])  # biggest first keeps every core busy to the end
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        total = sum(ex.map(expand, jobs, chunksize=4))
    print(f"{src}: {len(jobs)} chunks, {total / 1e9:.2f} GB expanded")

if __name__ == "__main__":
    main()
