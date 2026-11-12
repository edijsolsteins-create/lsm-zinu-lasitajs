#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ziņu lasītājs — reads news aloud for people who can no longer read a screen.

  * LSM.lv headlines -> tap during the pause after a headline -> whole article is read.
  * Photo of a printed newspaper page -> headlines are recognised (Claude vision)
    and read the same way; tapping reads that article.

Runs locally (this file) or on Vercel (api/*.py -> vercel_app.py -> handle() below).

Run:   python server.py            (opens the app in your browser)
Phone: open the "Wi-Fi" address it prints, on a phone in the same Wi-Fi.

Only the Python 3.8+ standard library is used. Settings come from environment
variables or a `.env` file next to this script (see .env.example).
"""

import base64
import email.utils
import hashlib
import html
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from collections import OrderedDict
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape as xml_escape

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "public")
VERSION = "0.2.0"


# ---------------------------------------------------------------- settings --
def _load_env_file(path):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_env_file(os.path.join(ROOT, ".env"))


def env(name, default=""):
    return os.environ.get(name, default).strip()


ON_VERCEL = bool(os.environ.get("VERCEL"))


def _access_key():
    key = env("ACCESS_KEY")
    if key or ON_VERCEL:          # on Vercel the key can only come from the project's environment variables
        return key
    path = os.path.join(ROOT, ".access_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if not key:
        key = secrets.token_urlsafe(12)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(key)
        except OSError:
            pass
    return key


HOST = env("HOST", "0.0.0.0")
PORT = int(env("PORT", "8765"))
ACCESS_KEY = _access_key()

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-sonnet-5-5")
PHOTO_DAILY_LIMIT = int(env("PHOTO_DAILY_LIMIT", "80"))        # Claude calls per day (0 = no limit)

AZURE_SPEECH_KEY = env("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = env("AZURE_SPEECH_REGION", "westeurope")
AZURE_VOICES = [v.strip() for v in env("AZURE_VOICES", "lv-LV-EveritaNeural,lv-LV-NilsNeural").split(",") if v.strip()]
TTS_DAILY_CHARS = int(env("TTS_DAILY_CHARS", "60000"))         # characters per day (0 = no limit)

MAX_HEADLINES = int(env("MAX_HEADLINES", "12"))
WAIT_SECONDS = float(env("WAIT_SECONDS", "6"))
LONG_PRESS_MS = int(env("LONG_PRESS_MS", "1000"))
DEFAULT_RATE = float(env("DEFAULT_RATE", "0.9"))


def photo_enabled():      # paid features only ever run behind an access key
    return bool(ANTHROPIC_API_KEY and ACCESS_KEY)


def azure_enabled():
    return bool(AZURE_SPEECH_KEY and ACCESS_KEY)

FEED_NEWS = "https://www.lsm.lv/rss/?lang=lv&catid=14"      # Visas ziņas
FEED_WEATHER = "https://www.lsm.lv/rss/?lang=lv&catid=19"   # Laika ziņas
FEED_EASY = "https://www.lsm.lv/rss/?lang=lv&catid=21710"   # Ziņas vieglajā valodā (MP3)

USER_AGENT = "Mozilla/5.0 (compatible; zinu-lasitajs/%s)" % VERSION
CACHE_SECONDS = 600


def log(msg):
    try:
        print(time.strftime("%H:%M:%S"), msg, flush=True)
    except Exception:
        pass


class DailyBudget:
    """Caps paid API use per day so a bug or a stranger cannot run up a bill."""

    def __init__(self, limit):
        self.limit, self.day, self.used = limit, None, 0
        self.lock = threading.Lock()

    def take(self, amount=1):
        with self.lock:
            today = time.strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.used = today, 0
            if self.limit and self.used + amount > self.limit:
                return False
            self.used += amount
            return True


PHOTO_BUDGET = DailyBudget(PHOTO_DAILY_LIMIT)
TTS_BUDGET = DailyBudget(TTS_DAILY_CHARS)


class LimitReached(Exception):
    pass


# ------------------------------------------------------------------ fetch --
_cache = {}
_cache_lock = threading.Lock()


def fetch(url):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "lv"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace").lstrip("﻿")
    with _cache_lock:
        _cache[url] = (now, text)
    return text


# ------------------------------------------------------------- text tools --
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(s):
    if not s:
        return ""
    s = html.unescape(html.unescape(s))          # feeds sometimes double-escape (&amp;nbsp;)
    s = _TAG.sub(" ", s).replace("\xa0", " ")
    return _WS.sub(" ", s).strip()


# Things a Latvian voice reads badly. Grammar agreement is not attempted.
_SAY_FIXES = [(re.compile(a), b) for a, b in [
    (r"\bu\.\s?c\.", "un citi"),
    (r"\bt\.\s?sk\.", "tai skaitā"),
    (r"\bu\.\s?tml\.", "un tamlīdzīgi"),
    (r"\butt\.", "un tā tālāk"),
    (r"\bplkst\.", "pulksten"),
    (r"\bmilj\.", "miljoni"),
    (r"\bmljrd\.", "miljardi"),
    (r"\btūkst\.", "tūkstoši"),
    (r"\bapm\.", "apmēram"),
    (r"\bNr\.", "numur"),
    (r"(\d) (\d{3})\b", r"\1\2"),                 # 1 000 -> 1000
    (r"(\d) (\d{3})\b", r"\1\2"),                 # twice for 1 000 000
    (r"(\d)\s?%", r"\1 procenti"),
    (r"€\s?(\d[\d.,]*)", r"\1 eiro"),
    (r"(\d)\s?€", r"\1 eiro"),
    (r"\bEUR\b", "eiro"),
    (r"°\s?C\b", " grādi"),
    (r"\bkm/h\b", "kilometri stundā"),
    (r"\bkm\b", "kilometri"),
    (r"(?<!\d)(?<!\d\.)([01]?\d|2[0-3])[.:]00(?!\d|\.\d)", r"\1"),              # 18.00 -> 18 (not dates)
    (r"(?<!\d)(?<!\d\.)([01]?\d|2[0-3])[.:]([0-5]\d)(?!\d|\.\d)", r"\1 \2"),     # 9.30 -> 9 30
    (r"[«»„“”]", '"'),
    (r"\s[–—]\s", ", "),
]]


def normalize(s):
    for rx, rep in _SAY_FIXES:
        s = rx.sub(rep, s)
    return _WS.sub(" ", s).strip()


_SENT_END = r"(?:(?<=[.!?…])|(?<=[.!?…][\"')»”]))\s+(?=[\"'(«„]?[A-ZĀČĒĢĪĶĻŅŠŪŽ0-9])"
_SENT_SPLIT = re.compile(_SENT_END)
# A full stop after these is not the end of a sentence ("t.sk. 500", "2026. g. maijā", "J. Bērziņš").
_ABBR_TAIL = re.compile(
    r"(?:\b(?:u\.\s?c|t\.\s?sk|u\.\s?tml|t\.\s?i|plkst|milj|mljrd|tūkst|apm|Nr|piem|lpp|gs|g|sk|"
    r"prof|doc|Dr|kv|iel|nov|pag)\.|(?:^|\s)[A-ZĀČĒĢĪĶĻŅŠŪŽ]\.)$")


def _good_boundaries(text, rx):
    for m in rx.finditer(text):
        if m.group(0).count("\n") >= 2 or not _ABBR_TAIL.search(text[:m.start()]):
            yield m


def split_sentences(text, max_len=260):
    """Short chunks: browsers cut off long utterances, and pause/resume works per chunk."""
    parts, start = [], 0
    for m in _good_boundaries(text, _SENT_SPLIT):
        parts.append(text[start:m.start()])
        start = m.end()
    parts.append(text[start:])
    out = []
    for part in parts:
        part = part.strip()
        while len(part) > max_len:
            cut = part.rfind(", ", 0, max_len)
            if cut < 80:
                cut = part.rfind(" ", 0, max_len)
            if cut <= 0:
                break
            out.append(part[:cut + 1].strip())
            part = part[cut + 1:].strip()
        if part:
            out.append(part)
    return out


class StreamSplitter:
    """Turns text arriving in pieces into speakable sentences as soon as each one is complete."""

    BOUNDARY = re.compile(r"\n\s*\n|" + _SENT_END)

    def __init__(self):
        self.buf = ""

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            m = next(_good_boundaries(self.buf, self.BOUNDARY), None)
            if not m:
                break
            chunk, self.buf = self.buf[:m.start()], self.buf[m.end():]
            out.extend(self._prep(chunk))
        return out

    def flush(self):
        chunk, self.buf = self.buf, ""
        return self._prep(chunk)

    @staticmethod
    def _prep(chunk):
        chunk = _WS.sub(" ", chunk).strip()
        return split_sentences(normalize(chunk)) if chunk else []


# -------------------------------------------------------------------- RSS --
_BAD_ENTITY = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")


def _iso_date(s):
    try:
        return email.utils.parsedate_to_datetime(s).isoformat()
    except Exception:
        return None


def parse_rss(xml_text):
    xml_text = _BAD_ENTITY.sub("&amp;", _XML_DECL.sub("", xml_text, count=1))
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        audio = None
        for enc in it.findall("enclosure"):
            u = enc.get("url") or ""
            if "audio" in (enc.get("type") or "") or u.lower().split("?")[0].endswith(".mp3"):
                audio = u
        items.append({
            "title": clean_text(it.findtext("title")),
            "desc": clean_text(it.findtext("description")),
            "link": (it.findtext("link") or "").strip().split("?")[0],
            "date": _iso_date(it.findtext("pubDate") or ""),
            "audio": audio,
        })
    return items


# Items with nothing to read aloud (video, quizzes, galleries, live streams, podcasts)
_SKIP_TITLE = re.compile(
    r"^\W*(video|foto|tests?|tiešraide|galerija|audio|infografika)\b"
    r"|raidieraksts|podkāsts|ziņas vieglajā valodā",
    re.IGNORECASE,
)


def get_headlines():
    out, errors = [], []

    try:
        weather = parse_rss(fetch(FEED_WEATHER))
        if weather:
            out.append({"type": "weather", **weather[0]})
    except Exception as e:
        errors.append(f"laika ziņas: {e}")

    try:
        for it in parse_rss(fetch(FEED_EASY)):
            if it["audio"] or it["link"]:
                out.append({"type": "easy", **it})
                break
    except Exception as e:
        errors.append(f"vieglā valoda: {e}")

    try:
        seen, count = set(), 0
        for it in parse_rss(fetch(FEED_NEWS)):
            if not it["title"] or not it["link"] or it["link"] in seen or _SKIP_TITLE.search(it["title"]):
                continue
            seen.add(it["link"])
            out.append({"type": "news", **it})
            count += 1
            if count >= MAX_HEADLINES:
                break
    except Exception as e:
        errors.append(f"ziņas: {e}")

    for it in out:
        it["say_title"] = normalize(it["title"])
        it["say_desc"] = normalize(it["desc"])
    for e in errors:
        log("KĻŪDA ielādējot " + e)
    return {"items": out, "errors": errors}


# --------------------------------------------------------- article body --
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_SKIP_TAGS = {"script", "style", "noscript", "figure", "figcaption", "aside", "nav", "footer",
              "form", "button", "svg", "iframe", "template", "select", "picture", "video", "audio"}
_TEXT_BLOCKS = {"p", "h2", "h3", "h4", "h5", "li", "blockquote"}
_CLOSES_P = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "blockquote",
             "section", "article", "figure", "header", "footer", "aside", "nav", "pre", "form", "hr"}
_JUNK_ATTR = re.compile(
    r"share|social|related|banner|advert|reklam|promo|comment|caption|sidebar|newsletter|"
    r"cookie|breadcrumb|gallery|recommend|popular|subscribe|modal|footer", re.IGNORECASE)
_JUNK_TEXT = re.compile(
    r"Valodas kļūda rakstā|Iezīmējiet tekstu|Ctrl\s*\+\s*Enter|Par faktu kļūdām|redakcija@lsm|"
    r"Saistītie raksti|^Lasi(et)? arī|^Seko(jiet)? |^Foto:|^Video:|^Avots:\s*\S*$", re.IGNORECASE)


class _Node:
    __slots__ = ("tag", "attrs", "parent", "children")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []


class _TreeBuilder(HTMLParser):
    """Forgiving mini-DOM: good enough to find where the article text lives."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        if tag in _CLOSES_P and self.cur.tag == "p":
            self.cur = self.cur.parent
        if tag == "li" and self.cur.tag == "li":
            self.cur = self.cur.parent
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(_Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def _is_junk(node):
    if node.tag in _SKIP_TAGS:
        return True
    a = node.attrs
    if "hidden" in a or a.get("aria-hidden") == "true":
        return True
    if "display:none" in a.get("style", "").replace(" ", ""):
        return True
    marker = (a.get("class", "") + " " + a.get("id", "")).strip()
    return bool(marker) and bool(_JUNK_ATTR.search(marker))


def _iter(node):
    for c in node.children:
        yield c
        if not isinstance(c, str):
            yield from _iter(c)


def _text(node):
    parts = []

    def walk(n):
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            elif c.tag == "br":
                parts.append(" ")
            elif not _is_junk(c):
                walk(c)
    walk(node)
    return _WS.sub(" ", "".join(parts).replace("\xa0", " ")).strip()


def _link_only(node):
    """True when all visible text sits inside <a> tags, e.g. 'Lasiet arī: <a>…</a>' lists."""
    outside = []

    def walk(n):
        for c in n.children:
            if isinstance(c, str):
                outside.append(c)
            elif c.tag != "a" and not _is_junk(c):
                walk(c)
    walk(node)
    rest = _WS.sub("", "".join(outside)).strip(":;-–—·|")
    return len(rest) <= 12 and any(not isinstance(c, str) and c.tag == "a" for c in _iter(node))


def _best_container(root):
    """The element whose paragraphs hold the most text (parent full credit, grandparent half)."""
    scores = {}

    def add(node, pts):
        if node is not None:
            prev = scores.get(id(node))
            scores[id(node)] = (node, (prev[1] if prev else 0) + pts)

    def walk(n):
        for c in n.children:
            if isinstance(c, str) or _is_junk(c):
                continue
            if c.tag == "p":
                length = len(_text(c))
                if length >= 25:
                    add(c.parent, length)
                    add(c.parent.parent if c.parent else None, length / 2)
            walk(c)
    walk(root)
    if not scores:
        return None
    return max(scores.values(), key=lambda t: t[1])[0]


def _collect(container):
    out = []

    def walk(n):
        for c in n.children:
            if isinstance(c, str) or _is_junk(c):
                continue
            if c.tag in _TEXT_BLOCKS:
                t = _text(c)
                if t and not _link_only(c):
                    out.append((c.tag, t))
            else:
                walk(c)
    walk(container)
    return out


def extract_article(page):
    builder = _TreeBuilder()
    builder.feed(page)
    builder.close()
    best = _best_container(builder.root)
    # The lead paragraph often sits next to the body container: widen one level up
    # when that adds only a little text (so a sidebar next door is not pulled in).
    if best is not None and best.parent is not None and best.parent.tag not in ("#root", "html", "body"):
        own = sum(len(t) for _, t in _collect(best))
        wider = sum(len(t) for _, t in _collect(best.parent))
        if own and wider - own <= 0.6 * own:
            best = best.parent
    sentences, seen = [], set()
    for tag, t in (_collect(best) if best is not None else []):
        if len(t) < 3 or t in seen or _JUNK_TEXT.search(t):
            continue
        seen.add(t)
        t = normalize(t)
        if tag.startswith("h") and not t.endswith((".", "!", "?", ":")):
            t += "."
        sentences.extend(split_sentences(t))
    m = re.search(r"https?://[^\"'\s<>]+?\.mp3", page.replace("\\/", "/"))
    return {"sentences": sentences, "audio": m.group(0) if m else None}


# ------------------------------------------------- newspaper photo (Claude) --
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

PHOTO_SYSTEM = (
    "You help a visually impaired Latvian reader listen to a printed newspaper. "
    "You receive photos of ONE newspaper page: the first image is the whole page; any further images "
    "are overlapping zoomed-in crops of the same page (top-left, top-right, bottom-left, bottom-right) "
    "for reading small print. Treat them as one page. The text is Latvian: keep Latvian letters "
    "(ā č ē ģ ī ķ ļ ņ š ū ž) exactly as printed. Never invent, summarise or translate text; "
    "if a part is unreadable, leave it out."
)

HEADLINES_TASK = """List the articles on this page in reading order (top to bottom, across columns from left to right).
For each article give its headline exactly as printed (join words split by hyphenation).
Include news items, articles, interviews and notice sections (for example "Sludinājumi" or "Līdzjūtības"); for a section without its own headline use a short Latvian description, e.g. "Sludinājumi".
Skip commercial advertisements, the newspaper name and date banner, page numbers, photo captions and crosswords.
Answer with JSON only, nothing else:
{"articles": [{"id": 1, "headline": "..."}]}
If the page has no readable text, answer {"articles": []}."""

ARTICLE_TASK = """Transcribe the complete text of article {id} on this page, the one with the headline: "{headline}".
Write it exactly as printed, in reading order, following it across columns. Do not summarise, shorten, translate or add anything.
Leave out the headline itself and photo captions. Join words split by hyphenation at line ends. Separate paragraphs with a blank line.
If the article continues on another page or is cut off at the edge of the photo, end with this sentence: Raksta turpinājums šajā foto nav redzams.
Output only the article text."""


def claude_request(payload):
    """POST to the Messages API. Returns the open response (caller reads/streams it)."""
    req = urllib.request.Request(
        ANTHROPIC_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Claude API {e.code}: {detail}") from None


def _photo_content(images, task):
    blocks = []
    for i, data in enumerate(images):
        block = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
        if i == len(images) - 1:
            block["cache_control"] = {"type": "ephemeral"}   # later article requests reuse the images cheaply
        blocks.append(block)
    blocks.append({"type": "text", "text": task})
    return blocks


def _json_from_reply(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group(0))


def recognise_headlines(images):
    if not PHOTO_BUDGET.take():
        raise LimitReached("photo limit")
    payload = {"model": CLAUDE_MODEL, "max_tokens": 2000, "system": PHOTO_SYSTEM,
               "messages": [{"role": "user", "content": _photo_content(images, HEADLINES_TASK)}]}
    with claude_request(payload) as resp:
        reply = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
    data = _json_from_reply(text)
    items = []
    for n, a in enumerate(data.get("articles", []), start=1):
        headline = clean_text(str(a.get("headline", "")))
        if headline:
            items.append({"id": n, "headline": headline, "say_headline": normalize(headline)})
    return items[:40]


def iter_sse_text(resp):
    """Yield text pieces from a streaming Messages API response."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            yield ev["delta"].get("text", "")
        elif kind == "error":
            raise RuntimeError(ev.get("error", {}).get("message", "Claude API error"))
        elif kind == "message_stop":
            return


def check_images(images):
    """Photos come from the phone as base64 JPEGs: the whole page plus up to 4 crops."""
    if not isinstance(images, list) or not 1 <= len(images) <= 6 or \
            not all(isinstance(i, str) and 100 < len(i) < 9_000_000 for i in images):
        raise ValueError("bad images")
    for i in images:
        base64.b64decode(i[:400] + "=" * (-len(i[:400]) % 4))   # cheap sanity check (raises on garbage)
    return images


def article_lines(images, aid, headline):
    """NDJSON lines for one article, sent as soon as each sentence is ready.

    Stateless on purpose: the phone sends the photos again with every article request,
    so this also works on serverless hosting. Prompt caching keeps the repeat cheap."""
    def line(obj):
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        if not PHOTO_BUDGET.take():
            raise LimitReached("photo limit")
        task = ARTICLE_TASK.format(id=aid, headline=str(headline).replace('"', "'")[:300])
        payload = {"model": CLAUDE_MODEL, "max_tokens": 8000, "stream": True, "system": PHOTO_SYSTEM,
                   "messages": [{"role": "user", "content": _photo_content(images, task)}]}
        splitter = StreamSplitter()
        with claude_request(payload) as resp:
            for piece in iter_sse_text(resp):
                for s in splitter.feed(piece):
                    yield line({"s": s})
        for s in splitter.flush():
            yield line({"s": s})
        yield line({"done": True})
    except Exception as e:
        log(f"KĻŪDA raksta atšifrēšanā: {e}")
        yield line({"error": "limit" if isinstance(e, LimitReached) else "failed"})


# ------------------------------------------------------------ Azure voice --
_TTS_CACHE = OrderedDict()
_TTS_INFLIGHT = {}
_TTS_LOCK = threading.Lock()


def _azure_synthesize(text, voice, rate):
    pct = int(round((rate - 1.0) * 100))
    ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="lv-LV">'
            f'<voice name="{voice}"><prosody rate="{pct:+d}%">{xml_escape(text)}</prosody></voice></speak>')
    req = urllib.request.Request(
        f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"), method="POST",
        headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
                 "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                 "User-Agent": "zinu-lasitajs"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Azure TTS {e.code}: {e.read()[:200]!r}") from None


def tts_audio(text, voice, rate):
    """MP3 bytes for one sentence; cached, and parallel requests for the same text share one call."""
    key = hashlib.sha1(f"{voice}|{rate:.2f}|{text}".encode("utf-8")).hexdigest()
    with _TTS_LOCK:
        if key in _TTS_CACHE:
            _TTS_CACHE.move_to_end(key)
            return _TTS_CACHE[key]
        waiter = _TTS_INFLIGHT.get(key)
        owner = waiter is None
        if owner:
            waiter = _TTS_INFLIGHT[key] = threading.Event()
    if not owner:
        waiter.wait(35)
        with _TTS_LOCK:
            if key in _TTS_CACHE:
                return _TTS_CACHE[key]
        raise RuntimeError("TTS failed")
    try:
        if not TTS_BUDGET.take(len(text)):
            raise LimitReached("tts limit")
        audio = _azure_synthesize(text, voice, rate)
        with _TTS_LOCK:
            _TTS_CACHE[key] = audio
            while len(_TTS_CACHE) > 500:
                _TTS_CACHE.popitem(last=False)
        return audio
    finally:
        with _TTS_LOCK:
            _TTS_INFLIGHT.pop(key, None)
        waiter.set()


# ------------------------------------------------------------ requests --
# One request handler shared by the local server (below) and the Vercel functions (vercel_app.py).
class Reply:
    def __init__(self, status=200, body=b"", ctype="application/json; charset=utf-8", headers=None, stream=None):
        self.status, self.ctype, self.headers, self.stream = status, ctype, dict(headers or {}), stream
        self.body = body.encode("utf-8") if isinstance(body, str) else body


def json_reply(obj, status=200):
    return Reply(status, json.dumps(obj, ensure_ascii=False), headers={"Cache-Control": "no-store"})


def media_reply(data, ctype, range_header, cache):
    """Byte-range aware, because iPhones refuse to play audio without it."""
    extra = {"Accept-Ranges": "bytes", "Cache-Control": cache}
    m = re.match(r"bytes=(\d*)-(\d*)$", (range_header or "").strip())
    if m and (m.group(1) or m.group(2)):
        size = len(data)
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        else:
            start, end = max(0, size - int(m.group(2))), size - 1
        if start >= size or start > end:
            return Reply(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
        extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Reply(206, data[start:end + 1], ctype, extra)
    return Reply(200, data, ctype, extra)


def authorized(headers, query):
    if not ACCESS_KEY:                       # no key configured: free LSM reading only (paid features are off)
        return True
    given = headers.get("x-key") or (query.get("k") or [""])[0]
    return secrets.compare_digest(given.encode(), ACCESS_KEY.encode())


def handle(method, path, query, headers, body=b""):
    """Route one /api request. `headers` has lower-case names. Returns a Reply."""
    try:
        if not authorized(headers, query):
            return json_reply({"error": "key"}, 401)
        q1 = lambda name, default="": (query.get(name) or [default])[0]

        if method in ("GET", "HEAD"):
            if path == "/api/config":
                return json_reply({
                    "version": VERSION, "photo": photo_enabled(), "azure": azure_enabled(),
                    "azureVoices": AZURE_VOICES if azure_enabled() else [], "keyRequired": bool(ACCESS_KEY),
                    "waitSeconds": WAIT_SECONDS, "longPressMs": LONG_PRESS_MS, "rate": DEFAULT_RATE})
            if path == "/api/headlines":
                return json_reply(get_headlines())
            if path == "/api/article":
                url = q1("u")
                if not url.startswith("https://www.lsm.lv/"):
                    return json_reply({"error": "only www.lsm.lv links"}, 400)
                return json_reply(extract_article(fetch(url)))
            if path == "/api/tts":
                if not azure_enabled():
                    return json_reply({"error": "azure disabled"}, 503)
                text, voice = q1("t").strip()[:600], q1("v", AZURE_VOICES[0])
                try:
                    rate = min(1.5, max(0.5, float(q1("r", "1"))))
                except ValueError:
                    rate = 1.0
                if not text or voice not in AZURE_VOICES:
                    return json_reply({"error": "bad request"}, 400)
                # The same sentence is the same file: let browsers and the CDN keep it.
                return media_reply(tts_audio(text, voice, rate), "audio/mpeg", headers.get("range"),
                                   "public, max-age=86400, s-maxage=2592000")

        if method == "POST" and path in ("/api/photo", "/api/photo/article"):
            if not photo_enabled():
                return json_reply({"error": "photo disabled"}, 503)
            data = json.loads(body.decode("utf-8") or "{}")
            images = check_images(data.get("images"))
            if path == "/api/photo":
                items = recognise_headlines(images)
                log(f"Foto: atrasti {len(items)} virsraksti")
                return json_reply({"items": items})
            try:
                aid = int(data.get("id"))
            except (TypeError, ValueError):
                return json_reply({"error": "bad id"}, 400)
            return Reply(200, ctype="application/x-ndjson; charset=utf-8",
                         headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                         stream=article_lines(images, aid, data.get("headline", "")))
        return json_reply({"error": "not found"}, 404)
    except LimitReached:
        return json_reply({"error": "limit"}, 429)
    except ValueError as e:
        return json_reply({"error": str(e)}, 400)
    except Exception as e:
        log(f"KĻŪDA: {e}")
        return json_reply({"error": str(e)}, 500)


# ------------------------------------------------------------ local server --
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".webmanifest": "application/manifest+json",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json"}
MAX_BODY = 40 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "ZinuLasitajs/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def _write(self, r):
        self.send_response(r.status)
        self.send_header("Content-Type", r.ctype)
        for k, v in r.headers.items():
            self.send_header(k, v)
        if r.stream is None:
            self.send_header("Content-Length", str(len(r.body)))
        self.end_headers()
        if self.command == "HEAD":
            return
        if r.stream is None:
            self.wfile.write(r.body)
            return
        for chunk in r.stream:              # HTTP/1.0: the connection closes when the stream ends
            self.wfile.write(chunk)
            self.wfile.flush()

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else urllib.parse.unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR + os.sep) or not os.path.isfile(full):
            return Reply(404, "not found", "text/plain; charset=utf-8")
        with open(full, "rb") as f:
            data = f.read()
        return Reply(200, data, _MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream"),
                     {"Cache-Control": "no-cache"})

    def _dispatch(self):
        u = urllib.parse.urlparse(self.path)
        try:
            if not u.path.startswith("/api/"):
                return self._write(self._static(u.path))
            body = b""
            if self.command == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > MAX_BODY:
                    return self._write(json_reply({"error": "bad size"}, 413))
                body = self.rfile.read(length)
            headers = {k.lower(): v for k, v in self.headers.items()}
            self._write(handle(self.command, u.path, urllib.parse.parse_qs(u.query), headers, body))
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_HEAD = do_POST = _dispatch


# -------------------------------------------------------------------- main --
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))     # no packet is sent; just picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def open_browser(url):
    system = platform.system()
    try:
        if shutil.which("termux-open-url"):          # Android phone running Termux
            subprocess.Popen(["termux-open-url", url])
            return
        if system == "Windows":
            os.startfile("microsoft-edge:" + url)
            return
        if system == "Darwin" and os.path.isdir("/Applications/Microsoft Edge.app"):
            subprocess.Popen(["open", "-a", "Microsoft Edge", url])
            return
        for exe in ("microsoft-edge", "microsoft-edge-stable"):
            if shutil.which(exe):
                subprocess.Popen([exe, url])
                return
    except Exception:
        pass
    webbrowser.open(url)


def main():
    server = None
    for port in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        log("Neizdevās atvērt nevienu portu.")
        sys.exit(1)
    server.daemon_threads = True
    port = server.server_address[1]
    local = f"http://127.0.0.1:{port}/?k={ACCESS_KEY}"
    log(f"Ziņu lasītājs {VERSION} darbojas.")
    log("Šajā datorā:  " + local)
    ip = lan_ip()
    if ip and HOST in ("0.0.0.0", "", "::"):
        log(f"Telefonā (tajā pašā Wi-Fi):  http://{ip}:{port}/?k={ACCESS_KEY}")
    log("Avīzes foto: " + (f"ieslēgts ({CLAUDE_MODEL})" if photo_enabled() else "izslēgts (nav ANTHROPIC_API_KEY)"))
    log("Azure balsis: " + (", ".join(AZURE_VOICES) if azure_enabled() else "izslēgtas (nav AZURE_SPEECH_KEY)"))
    log("Lai apturētu, aizveriet šo logu (vai Ctrl+C).")
    if "--no-browser" not in sys.argv and not env("NO_BROWSER"):
        threading.Timer(0.8, open_browser, args=(local,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
