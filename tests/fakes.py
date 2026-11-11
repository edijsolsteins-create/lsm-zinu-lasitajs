"""Fake Claude / Azure / LSM so tests run offline."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as srv
from fixtures import *

CALLS = []


class FakeResp:
    def __init__(self, body=b"", lines=(), delay=0.0):
        self.body, self.lines, self.delay = body, list(lines), delay
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.body
    def __iter__(self):
        for line in self.lines:
            time.sleep(self.delay)
            yield line


def sse(ev):
    return [f"event: {ev['type']}\n".encode(), f"data: {json.dumps(ev)}\n".encode(), b"\n"]


def fake_claude(payload):
    CALLS.append(payload)
    if not payload.get("stream"):
        return FakeResp(json.dumps({"content": [{"type": "text", "text": HEADLINES_REPLY}]}).encode())
    text = ARTICLE_STREAM_TEXT
    pieces = [text[i:i + 17] for i in range(0, len(text), 17)]
    lines = sse({"type": "message_start", "message": {}})
    for p in pieces:
        lines += sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}})
    lines += sse({"type": "message_stop"})
    return FakeResp(lines=lines, delay=0.004)


def install(silent_audio=b"ID3fake-mp3-bytes" * 50):
    FIX = {srv.FEED_NEWS: RSS_NEWS, srv.FEED_WEATHER: RSS_WEATHER, srv.FEED_EASY: RSS_EASY}
    srv.fetch = lambda url: FIX.get(url, ARTICLE)
    srv.claude_request = fake_claude
    srv._azure_synthesize = lambda text, voice, rate: silent_audio
    srv.ANTHROPIC_API_KEY = "test-key"
    srv.AZURE_SPEECH_KEY = "test-key"
    srv.ACCESS_KEY = "testkey"
    return srv
