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

    def test_photo_flow(self):
        fakes.CALLS.clear()
        img = "A" * 2000
        body = json.dumps({"images": [img] * 5}).encode()
        req = urllib.request.Request(self.base + "/api/photo", data=body, method="POST",
                                     headers={"X-Key": "testkey", "Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req, timeout=10).read())
        self.assertEqual([i["headline"] for i in res["items"]],
                         ["Madonā atklāj jaunu bibliotēku", "Ziemā ceļi tiks kaisīti biežāk", "Sludinājumi"])
        first = fakes.CALLS[0]["messages"][0]["content"]
        self.assertEqual(sum(b["type"] == "image" for b in first), 5)
        self.assertEqual(first[4]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(fakes.CALLS[0]["model"], srv.CLAUDE_MODEL)

        t0 = time.time()
        lines = self.get(f"/api/photo/article?pid={res['pid']}&id=1").read().decode().splitlines()
        msgs = [json.loads(l) for l in lines if l.strip()]
        self.assertEqual(msgs[-1], {"done": True})
        sentences = [m["s"] for m in msgs if "s" in m]
        self.assertEqual(sentences[0], "Madonas novadā šonedēļ atklāja jauno bibliotēku.")
        self.assertIn("Madonā atklāj jaunu bibliotēku", fakes.CALLS[1]["messages"][0]["content"][-1]["text"])
        # second time: served from the finished job, no new Claude call
        n = len(fakes.CALLS)
        again = self.get(f"/api/photo/article?pid={res['pid']}&id=1").read().decode()
        self.assertEqual(len(fakes.CALLS), n)
        self.assertIn('"done": true', again)
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/photo/article?pid=nope&id=1")


if __name__ == "__main__":
    unittest.main()
