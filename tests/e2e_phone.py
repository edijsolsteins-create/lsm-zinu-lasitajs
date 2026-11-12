"""End-to-end run on an emulated phone (needs: pip install playwright && playwright install chromium).
   python tests/e2e_phone.py   -> screenshots in tests/out/"""
import io, json, os, struct, sys, threading, time, wave
import fakes
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(__file__), "out")
os.makedirs(OUT, exist_ok=True)


def wav(seconds=0.25):
    buf = io.BytesIO()
    w = wave.open(buf, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(struct.pack("<h", 0) * int(8000 * seconds)); w.close()
    return buf.getvalue()


srv = fakes.install(silent_audio=wav())
srv.WAIT_SECONDS, srv.LONG_PRESS_MS = 1.2, 600
TTS_HITS = []
_orig = srv._azure_synthesize
srv._azure_synthesize = lambda t, v, r: (TTS_HITS.append(t), _orig(t, v, r))[1]
httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{httpd.server_address[1]}/?k=testkey"

PAGE_JPG = os.environ.get("E2E_PAGE") or os.path.join(OUT, "_page.jpg")
from PIL import Image, ImageDraw
im = Image.new("RGB", (3000, 4000), "white")
ImageDraw.Draw(im).rectangle([200, 200, 2800, 600], fill="black")
if not os.environ.get("E2E_PAGE"):
    im.save(PAGE_JPG, quality=80)

def mock(with_lv_voice):
    voices = ["{name:'Google US English', lang:'en-US'}"]
    if with_lv_voice:
        voices.append("{name:'Google latviešu', lang:'lv-LV'}")
    return r"""(() => {
  const voices = [%s];
  window.__spoken = []; let cur = null, timer = null, queue = [];
  function next(){ if (cur || !queue.length) return; cur = queue.shift(); if (cur.text.trim()) window.__spoken.push(cur.text);
    timer = setTimeout(() => { const u = cur; cur = null; u.onend && u.onend({}); next(); }, 15 + cur.text.length * 2); }
  const synth = { getVoices: () => voices, speak(u){ queue.push(u); next(); },
    cancel(){ clearTimeout(timer); const u = cur; cur = null; const q = queue; queue = [];
      if (u && u.onerror) u.onerror({error:'interrupted'}); q.forEach(x => x.onerror && x.onerror({error:'canceled'})); },
    pause(){}, resume(){}, onvoiceschanged: null };
  Object.defineProperty(window, 'speechSynthesis', {value: synth, configurable: true});
  window.SpeechSynthesisUtterance = function(t){ this.text = t; };
})();""" % ",".join(voices)


def wait_for(page, cond, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        if page.evaluate(cond):
            return
        time.sleep(0.04)
    raise AssertionError(f"timeout: {cond} | mode={page.evaluate('S.mode')} idx={page.evaluate('S.idx')} "
                         f"view={page.evaluate('S.view')}")


def spoken(page):
    return page.evaluate("__spoken") + TTS_HITS


def hold(page, ms=900):
    box = page.locator("#stage").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down(); time.sleep(ms / 1000); page.mouse.up()


errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    device = p.devices["Pixel 7"]

    # ---------- A: phone with its own Latvian voice ----------
    ctx = browser.new_context(**device)
    ctx.add_init_script(mock(True))
    page = ctx.new_page()
    page.on("console", lambda m: m.type in ("error", "warning") and errors.append("A " + m.text))
    page.on("pageerror", lambda e: errors.append("A " + str(e)))
    page.goto(URL)
    time.sleep(0.5)
    print("touch:", page.evaluate("TOUCH"), "| voices:", page.evaluate("S.voices.map(v=>v.label)"),
          "| photo button:", page.is_visible("#btnPhoto"))
    page.screenshot(path=f"{OUT}/01_home.png")

    page.tap("#btnLsm")
    wait_for(page, "S.mode==='intro'"); time.sleep(0.3)
    page.screenshot(path=f"{OUT}/02_intro.png")
    wait_for(page, "S.mode==='wait' && S.idx===0")
    page.screenshot(path=f"{OUT}/03_wait.png")
    print("intro (touch wording):", [s for s in spoken(page) if "ekrān" in s][:2])

    wait_for(page, "S.mode==='headline' && S.idx===2")
    page.tap("#stage"); wait_for(page, "S.mode==='article'")
    time.sleep(0.1); page.screenshot(path=f"{OUT}/04_article.png")
    page.tap("#stage"); wait_for(page, "S.mode==='paused'")
    page.tap("#stage"); wait_for(page, "S.mode==='article'")
    hold(page); wait_for(page, "S.idx===3 && ['headline','wait'].includes(S.mode)")
    hold(page); wait_for(page, "S.view==='home'")
    print("LSM tap/pause/hold flow OK; resumable:", page.evaluate("S.lsm.resumable"))

    # newspaper photo
    TTS_HITS.clear()
    fakes.CALLS.clear()
    with page.expect_file_chooser() as fc:
        page.tap("#btnPhoto")
    fc.value.set_files(PAGE_JPG)
    wait_for(page, "S.mode==='photoBusy'"); time.sleep(0.2)
    page.screenshot(path=f"{OUT}/05_photo_busy.png")
    wait_for(page, "S.session==='photo' && S.mode==='wait' && S.idx===0")
    page.screenshot(path=f"{OUT}/06_photo_headline.png")
    imgs = [b for b in fakes.CALLS[0]["messages"][0]["content"] if b["type"] == "image"]
    total = sum(len(b["source"]["data"]) for b in imgs)
    print("photo sent as", len(imgs), "images,", round(total / 1e6, 2), "MB base64 (limit 3.8); headlines:",
          page.evaluate("S.items.map(i=>i.title)"))
    assert total <= 3_800_000
    page.tap("#stage")
    wait_for(page, "S.mode==='article' || S.mode==='waitText'")
    wait_for(page, "S.mode==='article' && S.pos>=1"); page.screenshot(path=f"{OUT}/07_photo_article.png")
    wait_for(page, "S.idx===1", timeout=30)
    sp = spoken(page)
    print("photo article read:", [s for s in sp if "bibliotēk" in s.lower()][:3])
    print("found-words line:", [s for s in sp if s.startswith("Atradu")])
    art_call = fakes.CALLS[1]["messages"][0]["content"]
    print("article request resent the same photo:", art_call[:5] == fakes.CALLS[0]["messages"][0]["content"][:5])
    wait_for(page, "S.view==='home'", timeout=30)
    print("photo session ended ->", spoken(page)[-2:])

    # settings sheet
    page.tap("#gear"); time.sleep(0.2)
    page.screenshot(path=f"{OUT}/08_settings.png")
    page.tap("#faster"); page.tap("#closeSettings")
    print("rate after +:", page.evaluate("S.rate"))
    ctx.close()

    # ---------- B: phone without a Latvian voice -> Azure voice from the server ----------
    ctx = browser.new_context(**device)
    ctx.add_init_script(mock(False))
    page = ctx.new_page()
    page.on("console", lambda m: m.type in ("error", "warning") and errors.append("B " + m.text))
    page.on("pageerror", lambda e: errors.append("B " + str(e)))
    page.goto(URL); time.sleep(0.5)
    print("B voices:", page.evaluate("S.voices.map(v=>v.label)"), "| chosen:", page.evaluate("S.voice && S.voice.kind"))
    page.tap("#btnLsm")
    wait_for(page, "S.mode==='wait' && S.idx===0", timeout=40)
    print("B Azure sentences synthesised:", len(TTS_HITS), "| first:", TTS_HITS[:2])
    ctx.close()

    TTS_HITS.clear()
    # ---------- C: laptop (keyboard) quick check ----------
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(mock(True))
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append("C " + str(e)))
    page.goto(URL); time.sleep(0.4)
    page.screenshot(path=f"{OUT}/09_laptop_home.png")
    page.keyboard.down("Space"); time.sleep(0.05); page.keyboard.up("Space")
    wait_for(page, "S.mode==='wait' && S.idx===0")
    print("C laptop intro wording:", [s for s in spoken(page) if "taustiņš" in s])
    ctx.close()
    browser.close()

print("console errors/warnings:", errors)
