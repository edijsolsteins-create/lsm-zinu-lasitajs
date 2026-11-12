"""
ASGI adapter for Vercel. Each file in api/ re-exports `app` from here, so the
routes behave exactly like the local server: both call server.handle().
Standard library only, and streamed replies (newspaper articles) are sent
piece by piece as Claude produces them.
"""
import asyncio
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

MAX_BODY = 4_500_000          # Vercel's request body limit


async def _read_body(receive):
    body = bytearray()
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            break
        body += msg.get("body", b"")
        if len(body) > MAX_BODY:
            return None
        if not msg.get("more_body"):
            break
    return bytes(body)


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return

    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    query = urllib.parse.parse_qs(scope.get("query_string", b"").decode("latin-1"))
    body = await _read_body(receive)
    if body is None:
        reply = server.json_reply({"error": "too large"}, 413)
    else:
        reply = await asyncio.to_thread(server.handle, scope["method"], scope["path"], query, headers, body)

    out_headers = [(b"content-type", reply.ctype.encode())]
    out_headers += [(k.lower().encode(), str(v).encode()) for k, v in reply.headers.items()]
    if reply.stream is None:
        out_headers.append((b"content-length", str(len(reply.body)).encode()))
    await send({"type": "http.response.start", "status": reply.status, "headers": out_headers})

    if reply.stream is None:
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else reply.body})
        return

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def pump():                     # the Claude stream is blocking urllib: run it in a worker thread
        try:
            for chunk in reply.stream:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    worker = loop.run_in_executor(None, pump)
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})
    await worker
