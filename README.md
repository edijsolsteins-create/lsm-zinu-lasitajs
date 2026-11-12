# Ziņu lasītājs

Reads the news aloud for someone whose sight no longer allows reading a phone screen.
Everything works with **one button**: a short tap and a long hold.

* **Klausīties ziņas** – headlines from [LSM.lv](https://www.lsm.lv) are read one by one. A tap during the
  pause after a headline reads the whole article.
* **Lasīt avīzi** – take a photo of a printed newspaper page (for example *Stars*). The headlines are recognised
  and read in the same way, and a tap reads that article from the photo.

![Phone screens](docs/screens.png)

## How it is used

| | Phone | Laptop |
|---|---|---|
| The button | tap anywhere on the screen | spacebar (or a click) |
| During a headline or the pause after it (green screen) | tap = read the whole article | |
| While an article is read | tap = pause / continue · hold 1 s = back to headlines | |
| During headlines | hold 1 s = stop (a later tap continues where it stopped) | |

The first time, the app explains itself in Latvian (about 40 seconds; tap to skip). The **⚙ Iestatījumi**
strip at the bottom is for the helper: voice, speed, replay the explanation.

Helper keys on a laptop: `A` newspaper photo, `1`–`9` voice, `+`/`−` speed, `T` read the short teaser after
each LSM headline, `I` explanation again, `V` *Ziņas vieglajā valodā* (human-read recording), `R` reload, `Esc` stop.

## Run it

Needs Python 3.8 or newer ([python.org](https://www.python.org/downloads/); on Windows tick *Add Python to PATH*).
No other packages are needed.

```
python server.py          # Windows: double-click start-windows.bat
```

It opens the app in the browser and prints two links:

```
Šajā datorā:  http://127.0.0.1:8765/?k=…
Telefonā (tajā pašā Wi-Fi):  http://192.168.1.23:8765/?k=…
```

**On a laptop** use Microsoft Edge: it has free natural Latvian voices (Everita, Nils).

**On a phone** in the same Wi-Fi, open the second link in Chrome (Android) or Safari (iPhone), then
*Add to Home screen* so it opens like an app. Windows may ask to allow Python through the firewall: allow it
for private networks. The `?k=…` part is the access key; keep it in the link.

## Run everything on an Android phone (no computer)

1. Install **Termux** from [F-Droid](https://f-droid.org/packages/com.termux/) or its GitHub releases
   (not Google Play: that build is outdated).
2. In Termux:
   ```
   pkg update && pkg install python git
   git clone https://github.com/edijsolsteins-create/lsm-zinu-lasitajs
   chmod +x lsm-zinu-lasitajs/start-android.sh
   lsm-zinu-lasitajs/start-android.sh
   ```
   Chrome opens the app at `http://127.0.0.1:8765/?k=…`. In Chrome: ⋮ → *Add to Home screen*.
3. Optional keys: `cd lsm-zinu-lasitajs && cp .env.example .env && nano .env` (`pkg install nano` first).
4. Each later time: open Termux and run `lsm-zinu-lasitajs/start-android.sh`, then tap the home-screen icon.
   For a one-tap start, install **Termux:Widget** (from the same place as Termux) and copy the script to
   `~/.shortcuts/` as described inside it.

Termux has to keep running in the background: allow its notification, and in Android settings set Termux's
battery use to *Unrestricted*. On the phone itself the address is `127.0.0.1`, which browsers treat as secure,
so screen wake-lock and installing as an app work without HTTPS.

## Voices on phones

* **Android** has a Latvian voice in *Speech Recognition & Synthesis by Google*. If the app says there is no
  Latvian voice: Settings → *Text-to-speech output* → install Latvian voice data.
* **Natural voices for any phone**: add an Azure AI Speech key to `.env` (see below). The server then offers
  *Everita · Azure* and *Nils · Azure*, and the app picks them over a phone's built-in voice. Choose in ⚙ Iestatījumi.

## Settings (`.env`)

Copy `.env.example` to `.env` next to `server.py`. All keys are optional.

| Setting | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Turns on **Lasīt avīzi** (newspaper photo). Key from [platform.claude.com](https://platform.claude.com). |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` (default, most accurate) or `claude-haiku-5-5` (much cheaper). |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | Natural Latvian voices from the server for phones. |
| `ACCESS_KEY` | Secret in the link. Generated automatically if not set; **set it on a hosted server**. |
| `PHOTO_DAILY_LIMIT`, `TTS_DAILY_CHARS` | Daily caps so costs cannot run away. |
| `WAIT_SECONDS`, `LONG_PRESS_MS`, `DEFAULT_RATE`, `MAX_HEADLINES` | Timing and speed. |

## How the newspaper photo works

1. The phone shrinks the photo and sends it as the whole page plus four overlapping quarters, so small print
   stays readable.
2. Claude lists the headlines in reading order (adverts, captions and the masthead are skipped).
3. When an article is chosen, Claude transcribes it and the text is **streamed**: reading starts after a few
   seconds while the rest is still arriving. The page images are sent with prompt caching, so each article
   after the first costs little.

Photos stay in the server's memory for up to 3 hours and are not written to disk. They are sent to the
Anthropic API for recognition.

## Costs (estimates)

| | Sonnet 5.5 | Haiku 5.5 |
|---|---|---|
| Recognising one page (5 images, headlines) | ≈ $0.06 | ≈ $0.003 |
| Each article read from that page | ≈ $0.02 | ≈ $0.001 |
| A page with 3 articles read | ≈ $0.12 | < $0.01 |

Based on Claude API list prices ($2 / $10 per million input / output tokens for Sonnet 5.5, $0.10 / $0.50 for
Haiku 5.5, cache reads at 5–10 %) and about 24,000 image tokens per page. Azure voices: the free tier covers
0.5 million characters a month, roughly headlines plus 5 articles a day; sentences are cached, so repeats are free.

LSM news itself costs nothing.

## Hosting (so it works away from home Wi-Fi)

Any small server that runs Docker or Python works (a VPS, a Raspberry Pi at home behind a tunnel, …):

```
docker build -t zinu-lasitajs .
docker run -d -p 8765:8765 -e ACCESS_KEY=… -e ANTHROPIC_API_KEY=… -e AZURE_SPEECH_KEY=… zinu-lasitajs
```

Put HTTPS in front of it (for example Caddy or a Cloudflare Tunnel): phones keep the screen awake and allow
"install as app" only over HTTPS.

## Tests

```
python -m unittest discover -s tests          # offline: feeds, article extraction, photo + voice endpoints
python tests/e2e_phone.py                     # emulated phone run (needs: pip install playwright)
```

The LSM article extraction is tuned on sample pages; if an article comes out wrong (menu text, missing
paragraphs), save the page HTML and add it to `tests/fixtures.py`.

## Notes

News texts and recordings belong to LSM and the newspaper publishers; this tool only reads them aloud for
personal use.
