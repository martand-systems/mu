#!/usr/bin/env python3
"""A TCP hop that closes any connection that has carried no bytes in either direction for IDLE seconds: the kind
of middlebox (NAT, carrier, corporate proxy) that keepalives exist for. Traefik is expected NOT to behave like
this; the hop is the negative control that shows the idle test can tell.

  idle_killer.py LISTEN_PORT TARGET_HOST TARGET_PORT IDLE_SECONDS
"""
import asyncio
import sys

listen_port, target_host, target_port, idle = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), float(sys.argv[4])


async def handle(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        up_reader, up_writer = await asyncio.open_connection(target_host, target_port)
    except OSError as e:
        print(f"{peer}: upstream failed: {e}", flush=True)
        writer.close()
        return
    loop = asyncio.get_running_loop()
    last = [loop.time()]

    async def pump(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                last[0] = loop.time()
                dst.write(data)
                await dst.drain()
        except (OSError, asyncio.CancelledError):
            pass

    tasks = [asyncio.create_task(pump(reader, up_writer)), asyncio.create_task(pump(up_reader, writer))]
    reason = "closed by a peer"
    while True:
        done, _ = await asyncio.wait(tasks, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        if done:
            break
        if loop.time() - last[0] > idle:
            reason = f"CUT after {idle:.0f} s idle"
            break
    for t in tasks:
        t.cancel()
    for w in (writer, up_writer):
        try:
            w.close()
        except OSError:
            pass
    print(f"{peer}: {reason}", flush=True)


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", listen_port)
    print(f"idle-killer on :{listen_port} -> {target_host}:{target_port}, idle limit {idle:.0f} s", flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
