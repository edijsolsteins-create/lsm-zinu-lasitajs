"""Offline tests:  python -m unittest discover tests"""
import json, threading, time, unittest, urllib.request, urllib.error
import fakes
from fixtures import *

srv = fakes.install()
from http.server import ThreadingHTTPServer


class TextTests(unittest.TestCase):
    def test_feeds(self):
        h = srv.get_headlines()
        types = [i["type"] for i in h["items"]]
        self.assertEqual(types, ["weather", "easy", "news", "news", "news"])     # VIDEO + duplicate + easy-in-news skipped
        easy = h["items"][1]
        self.assertTrue(easy["audio"].endswith(".mp3"))
        news = h["items"][2]
        self.assertEqual(news["link"], "https://www.lsm.lv/raksts/zinas/latvija/04.11.2026-budzets.a2/")
        self.assertEqual(news["say_desc"], "Saeima otrdien pieņēma budžetu 2027. gadam.")
        self.assertIn("pulksten 10", h["items"][4]["say_title"])

    def test_article(self):
        a = srv.extract_article(ARTICLE)
        s = a["sentences"]
        self.assertTrue(s[0].startswith("Saeima otrdien galīgajā lasījumā"))            # lead kept
        self.assertIn("16000 miljoni eiro", s[1])
        joined = " ".join(s)
        for junk in ("Reklāma", "Valodas kļūda", "sānjoslā", "Lasiet arī", "Saistītā ziņa", "Foto:"):
            self.assertNotIn(junk, joined)
        self.assertIn('Finanšu ministrs sacīja: "Šis ir atbildīgs budžets."', s)
        self.assertIn("Viņš piebilda, ka deficīts nepārsniegs trīs procentus.", s)
        self.assertEqual(srv.split_sentences("Pieņemts 2026. g. maijā. J. Bērziņš piekrita."),
                         ["Pieņemts 2026. g. maijā.", "J. Bērziņš piekrita."])
        self.assertEqual(a["audio"], "https://static.lsm.lv/media/2026/11/e.mp3")

    def test_stream_splitter(self):
        sp = srv.StreamSplitter()
        out = []
        for i in range(0, len(ARTICLE_STREAM_TEXT), 5):
            out += sp.feed(ARTICLE_STREAM_TEXT[i:i + 5])
        out += sp.flush()
        self.assertEqual(out[0], "Madonas novadā šonedēļ atklāja jauno bibliotēku.")
        self.assertIn("12000 grāmatu, tai skaitā 500", out[1])
        self.assertIn('Bibliotēkas vadītāja sacīja: "Gaidām visus!"', out)
        self.assertIn("no pulksten 9 līdz 18.", " ".join(out))
        self.assertEqual(srv.normalize("Sēde 12.10.2026. sākas 9.30, beidzas 18.00."),
                         "Sēde 12.10.2026. sākas 9 30, beidzas 18.")
        self.assertEqual(out[-1], "Raksta turpinājums šajā foto nav redzams.")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path, key="testkey", headers=None):
        req = urllib.request.Request(self.base + path, headers=dict({"X-Key": key}, **(headers or {})))
        return urllib.request.urlopen(req, timeout=10)

    def test_static_and_key(self):
        self.assertEqual(self.get("/", key="").status, 200)
        self.assertEqual(self.get("/manifest.webmanifest", key="").status, 200)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/api/config", key="wrong")
        self.assertEqual(e.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/../server.py", key="")
        cfg = json.loads(self.get("/api/config").read())
        self.assertTrue(cfg["photo"] and cfg["azure"])

    def test_tts_range(self):
        r = self.get("/api/tts?v=lv-LV-EveritaNeural&r=0.9&t=Labdien", headers={"Range": "bytes=0-1"})
        self.assertEqual(r.status, 206)
        self.assertEqual(r.read(), b"ID")
        self.assertTrue(r.headers["Content-Range"].startswith("bytes 0-1/"))
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/tts?v=en-US-Evil&t=x")

    def post(self, path, obj, key="testkey"):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode(), method="POST",
                                     headers={"X-Key": key, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=10)

    def test_photo_flow(self):
        fakes.CALLS.clear()
        images = ["A" * 2000] * 5
        res = json.loads(self.post("/api/photo", {"images": images}).read())
        self.assertEqual([i["headline"] for i in res["items"]],
                         ["Madonā atklāj jaunu bibliotēku", "Ziemā ceļi tiks kaisīti biežāk", "Sludinājumi"])
        first = fakes.CALLS[0]["messages"][0]["content"]
        self.assertEqual(sum(b["type"] == "image" for b in first), 5)
        self.assertEqual(first[4]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(fakes.CALLS[0]["model"], srv.CLAUDE_MODEL)

        # stateless: the phone sends the photo again with the article request
        r = self.post("/api/photo/article", {"images": images, "id": 1, "headline": res["items"][0]["headline"]})
        msgs = [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]
        self.assertEqual(msgs[-1], {"done": True})
        sentences = [m["s"] for m in msgs if "s" in m]
        self.assertEqual(sentences[0], "Madonas novadā šonedēļ atklāja jauno bibliotēku.")
        second = fakes.CALLS[1]["messages"][0]["content"]
        self.assertEqual(second[:5], first[:5])                      # identical prefix -> prompt cache hit
        self.assertIn("Madonā atklāj jaunu bibliotēku", second[-1]["text"])
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/photo/article", {"images": "nope", "id": 1})
        self.assertEqual(e.exception.code, 400)


class KeylessTests(unittest.TestCase):
    """A fresh Vercel deployment without ACCESS_KEY: LSM works for anyone, paid features stay off."""

    def setUp(self):
        self.saved = srv.ACCESS_KEY
        srv.ACCESS_KEY = ""

    def tearDown(self):
        srv.ACCESS_KEY = self.saved

    def test_keyless(self):
        cfg = json.loads(srv.handle("GET", "/api/config", {}, {}).body)
        self.assertFalse(cfg["photo"] or cfg["azure"] or cfg["keyRequired"])
        self.assertEqual(srv.handle("GET", "/api/headlines", {}, {}).status, 200)
        self.assertEqual(srv.handle("POST", "/api/photo", {}, {}, b'{"images": []}').status, 503)
        self.assertEqual(srv.handle("GET", "/api/tts", {"t": ["x"]}, {}).status, 503)


class VercelAdapterTests(unittest.TestCase):
    def run_app(self, method, path, query=b"", body=b"", headers=None):
        import asyncio, vercel_app
        sent = []
        scope = {"type": "http", "method": method, "path": path, "query_string": query,
                 "headers": [(k.encode(), v.encode()) for k, v in (headers or {"x-key": "testkey"}).items()]}
        chunks = [body[i:i + 70000] for i in range(0, len(body), 70000)] or [b""]
        msgs = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

        async def receive():
            return msgs.pop(0) if msgs else {"type": "http.disconnect"}

        async def send(m):
            sent.append(m)
        asyncio.run(vercel_app.app(scope, receive, send))
        return sent

    def test_json_route(self):
        sent = self.run_app("GET", "/api/config")
        self.assertEqual(sent[0]["status"], 200)
        self.assertTrue(json.loads(sent[1]["body"])["photo"])
        self.assertEqual(self.run_app("GET", "/api/config", headers={"x-key": "bad"})[0]["status"], 401)

    def test_streaming_route(self):
        body = json.dumps({"images": ["A" * 2000] * 5, "id": 2, "headline": "Ziemā ceļi"}).encode()
        sent = self.run_app("POST", "/api/photo/article", body=body)
        self.assertEqual(sent[0]["status"], 200)
        parts = [m for m in sent[1:] if m.get("body")]
        self.assertGreater(len(parts), 3)                              # arrived in pieces, not one blob
        lines = b"".join(m["body"] for m in parts).decode().splitlines()
        self.assertEqual(json.loads(lines[-1]), {"done": True})
        self.assertFalse(sent[-1]["more_body"])

    def test_tts_range_and_cache_header(self):
        sent = self.run_app("GET", "/api/tts", b"v=lv-LV-NilsNeural&t=Sveiki",
                            headers={"x-key": "testkey", "range": "bytes=0-1"})
        self.assertEqual(sent[0]["status"], 206)
        hdrs = dict(sent[0]["headers"])
        self.assertIn(b"s-maxage", hdrs[b"cache-control"])


if __name__ == "__main__":
    unittest.main()
