# Jarvis: personal voice assistant

Jarvis is a voice assistant for a Windows laptop, written in Python 3.13. It started as an AQI bot and still focuses on air quality. It gives live CPCB AQI, PM2.5, PM10, NO2 and red zones for any area or pincode in India. It also handles weather, apps, websites, YouTube, news, notes, reminders, laptop controls, Gmail, Google Calendar, Spotify, maths and general questions.

Each feature lives in its own **skill file**, so new integrations (WhatsApp, VS Code and so on) can be added without touching the rest.

## Setup (Windows)

```powershell
cd jarvis
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Then edit `.env`. Every setting has a default, so these are all optional:

| Setting | What it does |
|---|---|
| `AI_API_KEY` | Lets Jarvis answer any question, using Gemini's native API or any OpenAI-compatible API (free options below). |
| `AI_PROVIDER` | `gemini` or `openai`. If empty, it's `gemini` when `AI_BASE_URL` is a `generativelanguage.googleapis.com` URL, otherwise `openai`. |
| `WAQI_TOKEN` | A free token from <https://aqicn.org/data-platform/token/>. Red zones then come from real CPCB monitoring stations. |
| `DEFAULT_LOCATION` | Used until you tell Jarvis your area. Defaults to New Delhi. |
| `VOICE_NAME`, `VOICE_RATE` | Part of a Windows voice name (`zira`, `david`, `heera`...) and the speaking speed. |
| `WAKE_WORDS`, `FOLLOW_UP_SECONDS` | The wake phrases, and how long you can keep talking without one after a reply. |
| `WHATSAPP_SEND_DELAY` | Seconds to wait for WhatsApp to open a chat before pressing Enter (default 3). |
| `SPOTIFY_CLIENT_ID` | Lets Jarvis control Spotify (see [Connecting Google and Spotify](#connecting-google-and-spotify)). |
| `CALENDAR_REMINDERS`, `CALENDAR_REMINDER_MINUTES` | Spoken reminder and toast before each calendar event (on by default, 10 minutes before). |
| `PROJECT_ROOTS` | Folders to scan for code projects, separated by `;`. Defaults to `C:\Users\ADMIN\OneDrive\Desktop\ANJALI`. |

Free AI keys:

| Provider | Get a key | `AI_PROVIDER` | `AI_BASE_URL` | `AI_MODEL` |
|---|---|---|---|---|
| Gemini (Google AI Studio) | <https://aistudio.google.com/apikey> | `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `gemini-2.5-flash` |
| Groq (fastest) | <https://console.groq.com/keys> | `openai` | `https://api.groq.com/openai/v1` | `qwen/qwen3.8-27b` with `AI_REASONING_EFFORT=none` |

**Gemini** uses the native `generateContent` API with the key in the `x-goog-api-key` header. Google issues keys in two formats, the newer `AQ.…` auth keys and the older `AIza…` keys, and Jarvis sends both the same way. (`AQ.` keys don't work on Google's OpenAI-compatible `/v1beta/openai` endpoint, which is why Jarvis doesn't use it.) If the model has been retired, Jarvis switches to the newest Flash model for the session and logs the name to put in `AI_MODEL`.

**Groq** models change often: if Jarvis says the model wasn't found, check the current list at <https://console.groq.com/docs/models>. Reasoning models (`qwen/...`, `openai/gpt-oss-...`) answer faster with `AI_REASONING_EFFORT=none` (Qwen) or `low` (gpt-oss).

**Retired models.** Providers retire models without warning. When that happens, Jarvis:
- lists the provider's current models;
- picks the first of `AI_MODEL_FALLBACKS` that's still offered (otherwise the newest Gemini Flash, or the first chat model);
- asks your question again and says "I switched to a newer AI model" once;
- rewrites only the `AI_MODEL` line of `.env` (your key is never read out or changed).

It also checks in the background at startup, at most once a day, so the first question isn't the one that fails. Ask "which AI model are you using" to hear the provider and model.

**Natural language for actions.** You don't have to use exact phrasings. When no command pattern matches, Jarvis sends what you said to the AI with a list of actions (tool calling), generated from the skills that declare `params=`: volume, media, play music, open and close apps, reminders, timers, notes, weather, air quality, WhatsApp, email drafts, calendar add and cancel, shutdown and restart, git pull and running projects.
- If the AI picks an action, its arguments become a normal command that runs through the usual skill, so every existing check still applies. "kal subah 9 baje yaad dilana gym jaana hai" becomes `(understood as: remind me tomorrow at 9 am to gym jaana hai)`.
- The AI can only reach those registered skills, and anything that sends, deletes or shuts down always asks first.
- Otherwise it answers as normal chat.
- The last 3 turns are included, so follow-ups work: "what about Gurgaon?", "and tomorrow?", "make it 5 minutes instead", "send the same to Riya".

The AI is only asked when no pattern matches. The request times out after 4 seconds (`AI_ROUTER_TIMEOUT`), you can use a separate model (`AI_ROUTER_MODEL`), identical phrases are cached for the session, and `AI_ROUTING=false` turns it off. On Groq's free tier (8,000 tokens a minute), a routed command costs about 2,000 tokens, so about 4 natural-language commands a minute. Exact phrasings like "volume up" don't use the AI at all.

**Short, streamed answers.** The AI is asked for 1 or 2 short sentences (150 tokens) unless you say "explain in detail" (600 tokens). Replies are streamed: Jarvis starts speaking the first sentence while the rest is still being written. On this laptop, speaking is the slowest step, so long skill replies (help, news, calendar, email, notes, reminders, projects) speak 3 items and then ask "Want more?". Say yes to hear the next 3. The full list is always printed in the terminal.

If the key is rejected, the model name is wrong, or the free quota runs out, Jarvis says so instead of failing silently. If Google rejects a key with `ACCESS_TOKEN_TYPE_UNSUPPORTED` or `API_KEY_SERVICE_BLOCKED`, the key isn't allowed to use the Gemini API: create one in AI Studio, or in Cloud Console allow the **Generative Language API** under the key's API restrictions.

For WhatsApp, copy `contacts.example.json` to `data\contacts.json` and add your contacts (country code plus number, digits only), or add them by voice. For extra project names, copy `projects.example.json` to `data\projects.json`. Everything in `data\` is git-ignored.

If `pip install PyAudio` fails, update pip first (`python -m pip install -U pip`); PyAudio ships Windows wheels for recent Python versions.

## Running

```powershell
python main.py            # voice, say "Jarvis ..." first
python main.py --no-wake  # voice, every phrase is a command
python main.py --text     # type commands (wake word not needed)
python main.py --mute     # print replies instead of speaking
python main.py --timing   # print how long each step of every turn took
python -m jarvis.benchmark  # compare Google vs Whisper speech-to-text on your recorded samples
python -m pytest -q       # run the offline tests
```

With no microphone, Jarvis switches to text mode automatically. After it replies you have `FOLLOW_UP_SECONDS` (20 by default) to carry on without saying the wake word. Press Ctrl+C to quit at any time.

## How listening works (and why it's fast)

- **Offline wake word.** Jarvis listens for **"Hey Jarvis"** on the laptop itself, using [openWakeWord](https://github.com/dscripka/openWakeWord)'s pretrained model on a continuous 16 kHz stream. Nothing is sent anywhere until it hears you. Then it **beeps**, and you say the command. `WAKE_SENSITIVITY` (0 to 1, default 0.5) sets how sure it must be: lower it if it misses you, raise it if it wakes by itself. If openWakeWord can't load, Jarvis falls back to looking for "Jarvis" in the transcript as before.
- **One microphone stream, opened once.** The old code reopened the mic for every phrase (about 250 ms each time).
- **End of speech.** A command ends after **600 ms of silence** (`END_SILENCE_MS`). Silence is detected with webrtcvad plus an energy threshold of the calibrated room level × 1.3. Jarvis waits up to 5 seconds for you to start (`LISTEN_TIMEOUT`), and a command can be up to 7 seconds long (`PHRASE_LIMIT`). It re-measures the room quietly after 10 idle minutes, or when you say **"recalibrate mic"**.
- **Speech-to-text.** `STT_ENGINE=google` (default) or `whisper`. Whisper runs locally with faster-whisper (`base.en` for English, `small` for Hindi). It uses CUDA when an NVIDIA GPU is available, otherwise the CPU with int8. The model loads in the background, and Google is used until it's ready. To see which is faster on your laptop, say **"record samples"** (5 short phrases saved to `data\samples\`), then run `python -m jarvis.benchmark`.
- **Snappier replies.** The voice speaks at 185 words a minute (`VOICE_RATE`). If a skill takes more than 1.5 seconds (`ONE_SEC_AFTER`), Jarvis says "One sec" first. Network calls time out after 6 seconds, the AI after 15. Weather and AQI are cached on disk for 10 minutes (`CACHE_MINUTES`) and place lookups for 30 days, in `data\cache.json`.
- **Barge-in.** Pressing **Ctrl+Alt+J** while Jarvis is talking stops it immediately and starts listening.
- **Startup.** Heavy libraries (requests, pyautogui, pystray, Whisper, openWakeWord) are loaded only when needed or in the background. Run with `--timing` to see where each turn's time goes, for example `[timing] wake word at 0.00s | speech detected at 0.41s | end of phrase at 1.92s | speech-to-text 0.55s | routing + skill 0.02s | text-to-speech 1.80s`.

In the follow-up window after a reply (`FOLLOW_UP_SECONDS`), Jarvis listens without the wake word, so speech in that window is sent to the speech-to-text engine. Set `FOLLOW_UP_SECONDS=0` if you want nothing sent without "Hey Jarvis".

## Running in the background

```powershell
pythonw run_jarvis.pyw        # tray icon, no console window (or just double-click run_jarvis.pyw)
python install_startup.py     # start in the tray every time you log in
python uninstall_startup.py   # undo that
```

- **Tray icon:** an orange "J" with a status dot. Green means listening, grey sleeping, red mic muted; hover to see the status. The right-click menu has Wake up, Sleep, Mute mic / Unmute, Open COMMANDS.md, Open notes, Open logs, Restart Jarvis and Quit. Saying "goodbye" closes the tray too.
- **Hotkey:** **Ctrl+Alt+J** (set `HOTKEY` in `.env`, or leave it empty to turn it off) wakes Jarvis instantly. It beeps, and the next phrase doesn't need the wake word. It uses `pynput`, which doesn't need admin rights, and also works in `python main.py`.
- **Start on login:** `install_startup.py`, or saying "start with Windows" (asks first), puts a `Jarvis.lnk` shortcut in your Startup folder (`shell:startup`). It runs the repo's `.venv\Scripts\pythonw.exe` if a venv exists, otherwise the `pythonw.exe` next to your Python, on `run_jarvis.pyw`, in the repo folder. Say "don't start with Windows" or run `uninstall_startup.py` to remove it.
- **Notifications:** reminders and timers also show a Windows toast (`NOTIFY=true`), so you see them while you're muted or in a meeting.
- **One copy only:** a named mutex stops a second Jarvis from starting. Launching it again, from the tray launcher or from `main.py`, wakes the one already running instead.
- **Logs:** everything is written to `data\logs\jarvis.log`, rotating at 1 MB and keeping 5 old files. `main.py` also prints to the console; the tray app has no console, so check the log (tray menu > Open logs) if something seems wrong.

## Connecting Google and Spotify

Everything is stored in `data\` (git-ignored). Jarvis never sends email. It only saves drafts, and it asks before creating or deleting anything.

### Google (Gmail + Calendar)

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create a **new project** (for example "Jarvis").
2. **APIs & Services > Library:** enable the **Gmail API** and the **Google Calendar API**.
3. **APIs & Services > OAuth consent screen:** choose **External**, name it Jarvis, and use your own email for the contact fields. Under **Test users**, **add your own Gmail address**.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**, with application type **Desktop app**.
5. Download the JSON and save it as `data\google_credentials.json`.
6. Say **"connect google"**. A browser opens: pick your account, click *Continue* on the "Google hasn't verified this app" screen (it's your own app), and allow access. The token is saved to `data\google_token.json`.

The permissions are `gmail.readonly`, `gmail.compose` (drafts) and `calendar.events`. If you say "connect google" before the JSON is in place, Jarvis prints these steps in the terminal (and in the log).

### Spotify

1. Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and click **Create app**.
2. Set the redirect URI to exactly `http://127.0.0.1:8888/callback`, tick **Web API** and save.
3. Copy the app's **Client ID**. Jarvis uses the PKCE flow, so you don't need the client secret.
4. Put it in `.env` as `SPOTIFY_CLIENT_ID=...` and restart Jarvis.
5. Say **"connect spotify"** and log in. The token is cached in `data\spotify_token.json`.

Spotify only allows apps to control playback on **Premium** accounts. Without a connection, "play X on Spotify" opens a search in the Spotify app instead.

## What you can say

📄 **Printable cheat sheet: [COMMANDS.md](COMMANDS.md).** Say "Jarvis, help" for the categories, or "help with WhatsApp" (or laptop, VS Code...) for examples from one category.

**Wake and control**
- "Hey Jarvis", then wait for the beep (offline wake word). If openWakeWord isn't available: "Jarvis", "Hey Jarvis" or "OK Jarvis" at the start or end of a sentence
- Ctrl+Alt+J wakes it instantly, and also interrupts it while it's talking
- "Recalibrate mic" / "record samples"
- "Go to sleep" / "stop listening" (only the wake word wakes it again)
- "Goodbye", "exit", "bye Jarvis", "turn off Jarvis"
- "Help" / "what can you do"

**Air quality** (live CPCB AQI)
- "What's the AQI", "air quality in Dwarka", "pollution in 110085", "is there smog"
- "PM2.5", "PM 2.5 in Rohini sector 7", "PM2 point 5", "PM10 in Noida"
- "NO2", "N O 2", "nitrogen dioxide in Gurugram"
- "Is it safe to go out", "do I need a mask", "is it safe for a morning walk", "pollution safe"
- "Red zones", "most polluted areas"
- "My area is Rohini", "I live in Lajpat Nagar", or just say your pincode "110085". Jarvis remembers it for the session.

**Weather**
- "What's the weather", "weather in Mumbai tomorrow", "temperature"
- "Will it rain today", "do I need an umbrella"

**Apps and websites**
- "Open Chrome", "open VS Code and Notepad", "open WhatsApp", "open wifi settings"
- "Open GitHub", "open gmail", "open github.com", "open example dot com"
- Anything else is searched in the Start menu: "open Figma"
- "Close Chrome", "quit Spotify", "close this window", "close tab"

**WhatsApp** (WhatsApp Desktop, or WhatsApp Web if Desktop isn't installed)
- "Send a WhatsApp to mom saying I'll be late"
- "Message Dhruv on WhatsApp that the demo is at 5"
- "WhatsApp mom" (asks what to say)
- Jarvis always reads the message back and asks before sending: "Send 'I'll be late' to Mom?"
- "Call mom on WhatsApp" opens the chat; you tap the call button yourself
- "Add contact Riya 98765 43210" (asks first; 10-digit numbers get the 91 prefix)
- Contact names are matched loosely, so "Druv" finds Dhruv.

**VS Code and projects**
- "Open bogie admin in VS Code", "open jarvis project", "work on bogie backend"
- If several projects match, Jarvis lists up to 3 and asks which one ("the second one" or a name)
- "Open VS Code here" / "new VS Code window"
- "Open file router dot py in jarvis" (skips node_modules, .git, dist, build, .next and \_\_pycache\_\_)
- "List my projects", "rescan projects"
- "Git status of jarvis": branch, ahead/behind, and how many files changed (file names are never read out)
- "Pull jarvis": asks first, then runs `git pull --ff-only`. Jarvis never pushes, commits or resets by voice.
- "Run bogie backend" / "start the go api server": asks first, then opens a new terminal running `npm run dev`, `npm start`, `go run .` or `python main.py`

**Email** (after "connect google")
- "Check my email", "any new emails": unread count in the Primary inbox, plus the senders and subjects of the latest 5
- "Read the email from Dhruv": a short summary (bodies are only read when you ask)
- "Search email for invoice": the top 3 matches
- "Email Dhruv saying the demo is at 5": reads it back, asks, and saves a **draft** (never sends)

**Calendar** (after "connect google", times in IST)
- "What's on my calendar today / tomorrow / this week", "my next meeting"
- "Add meeting with Dhruv tomorrow at 5 pm for 30 minutes", "schedule dentist on Monday at 11" (asks first)
- "Cancel my 5 pm meeting" (asks first)
- A spoken reminder and a toast 10 minutes before each event

**Spotify** (after "connect spotify")
- "Play Believer on Spotify", "play my liked songs", "play playlist workout"
- "Pause Spotify", "next song on Spotify", "what's playing", "Spotify volume 50"

**Daily briefing**
- "Good morning Jarvis" / "morning briefing" / "brief me": one short briefing (under 40 seconds) with the date, today's weather, AQI and mask advice for your area, the first 3 calendar events, unread email with the top 2 senders, today's reminders and one headline. Sections are fetched in parallel (6 s each); anything that fails or isn't connected is simply left out. The full version is printed in the terminal.
- "Good night Jarvis": tomorrow's first event and weather, tomorrow's reminders, an offer to set a wake-up reminder, and the volume turned down to 20%.
- Settings in `.env`: `BRIEFING_SECTIONS` (which sections, in order), `BRIEFING_TIME` (for example `08:30`: brief you automatically once a day, with a toast, if Jarvis is running and you've been idle for 2 minutes), `BRIEFING_MUSIC` (for example `lofi beats`, played on Spotify afterwards).
- In Hindi mode, the briefing is spoken in Hinglish (Roman letters) if an AI key is set, otherwise in English.

**Web**
- "Play Arijit Singh songs", "play lofi beats on YouTube"
- "Search lofi music on YouTube"
- "Search best laptops under 60000", "google weather in Mumbai", "look up Python decorators"
- "News", "news about cricket", "tech news"

**Notes, reminders and timers**
- "Take a note buy milk", "remember that the meeting is at 5"
- "Read my notes", "clear my notes" (asks first)
- "Remind me in 10 minutes to drink water", "remind me at 6 pm to call mom", "remind me in an hour and 30 minutes to stretch"
- "Set a timer for 5 minutes", "what are my reminders", "cancel my reminders"

Reminders only fire while Jarvis is running.

**Laptop** (Windows)
- "Volume up / down", "volume up by 20", "mute"
- "Pause the music", "resume", "next song", "previous song"
- "Brightness 70", "brightness up", "dim the screen"
- "Take a screenshot" (saved to `Pictures\Jarvis Screenshots`)
- "Battery status", "system status", "CPU usage"
- "Lock the laptop"
- "Shut down the laptop", "restart in 10 minutes", "turn off my computer" (always asks first), "cancel shutdown"
- "Put the laptop to sleep" (asks first)

**Everything else**
- "What time is it", "what's the date", "who are you", "how are you", "thank you"
- "What is 25 times 4", "10 percent of 250", "square root of 144", "2 to the power of 10"
- "Tell me a joke"
- "Who is Sachin Tendulkar", "tell me about the Taj Mahal", "what is Python" (Wikipedia)
- Anything else goes to the AI (needs `AI_API_KEY`). "Forget our conversation" clears its memory.
- Speak Hindi or Hinglish to the AI and it replies in Hinglish written in Roman letters, which the Windows voice can read.
- "Switch to Hindi" makes speech recognition listen for Hindi (`hi-IN`), and "switch to English" switches back. This lasts for the session only. In Hindi mode Google writes what you say in Devanagari, and `jarvis/translit.py` rewrites commands into English before routing: "क्रोम खोलो" becomes "open chrome". Messages and notes keep your exact words, and anything that isn't a command goes to the AI unchanged. See the Hindi section of [COMMANDS.md](COMMANDS.md).

## Architecture

```
main.py                 console entry point: flags, single-instance check, runs the Assistant
run_jarvis.pyw          background entry point (no console): starts the tray app
install_startup.py      add / remove the Startup-folder shortcut
uninstall_startup.py
config.py               every setting, read from .env with defaults
jarvis/assistant.py     Assistant: the listening loop, wake word, wake()/sleep()/mute() controls
jarvis/tray.py          pystray tray icon + menu; owns the main thread, voice loop on a background thread
jarvis/hotkey.py        global hotkey (pynput)
jarvis/notify.py        Windows toasts (win11toast, tray balloon fallback)
jarvis/startup.py       Startup-folder shortcut logic
jarvis/instance.py      single instance (named mutex) + "wake the running copy" signal
jarvis/logs.py          rotating log file + console
jarvis/voice.py         Voice: interruptible TTS on a worker thread, listening (wake word -> record -> STT), text mode
jarvis/audio.py         always-open 16 kHz mic stream, webrtcvad end-of-speech, openWakeWord "hey jarvis"
jarvis/stt.py           speech-to-text engines: Google, or faster-whisper loaded in the background
jarvis/timing.py        per-turn latency log (--timing)
jarvis/cache.py         small on-disk cache (data/cache.json) for weather, AQI and places
jarvis/benchmark.py     python -m jarvis.benchmark: Google vs Whisper on data/samples/*.wav
jarvis/router.py        @skill / @fallback registry, Context (say/ask/confirm), handle()
jarvis/translit.py      Hindi (Devanagari) -> English command rewriting
jarvis/http.py          shared requests.Session with timeouts: get_json / get_text / post_json
jarvis/geo.py           area name / pincode -> Place(name, lat, lon), location_from_command()
jarvis/timeparse.py     spoken durations ("an hour and 30 minutes") and clock times ("at 6 pm")
jarvis/skills/
    __init__.py         imports skills IN ORDER: general, notes, gmail, calendar, spotify, system,
                        web, whatsapp, vscode, apps, weather, aqi, knowledge, ai
    general.py          exit, sleep, help, greetings, time, date, calculator, jokes
    notes.py            notes file, reminders, timers
    gmail.py            unread count, read/search email, drafts only; "connect google"
    calendar.py         today/week/next meeting, add/cancel events, reminders before events
    spotify.py          play songs/liked songs/playlists, pause/skip, now playing, volume
    briefing.py         good morning briefing (parallel sections, 40 s budget, BRIEFING_TIME), good night
    system.py           volume, media, brightness, screenshot, battery, status, lock, power
    web.py              YouTube, Google, Google News
    whatsapp.py         WhatsApp messages, calls, contacts (data/contacts.json)
    vscode.py           project registry, open in VS Code, git status/pull, run dev servers
    apps.py             open / close apps and websites
    weather.py          Open-Meteo forecast
    aqi.py              CPCB AQI, pollutants, safety advice, red zones
    knowledge.py        Wikipedia summaries
    ai.py               OpenAI-compatible fallback with short conversation memory
tests/                  offline tests: conftest.py blocks network, real apps, Google and Spotify;
                        test_jarvis.py, test_whatsapp.py, test_vscode.py, test_gmail.py,
                        test_calendar.py, test_spotify.py (fakes.py holds the fake APIs)
```

How a command is handled:

1. `main.py` hears a phrase, strips the wake word, and calls `router.handle(ctx, command)`.
2. The router tries each skill **in registration order**, testing its regex patterns.
3. When one matches, the handler runs. Returning a string speaks it; returning `""` or `True` means "handled"; returning `None` means "not mine", and the router moves on to the next skill. For example, the calculator returns `None` for "what is python", so Wikipedia answers instead.
4. If nothing handles it, the AI fallback does.
5. Network errors and crashes inside a skill are caught and turned into a short spoken apology. Jarvis never crashes.

## Keeping secrets out of git

API keys live only in `.env`, which is git-ignored. As a safety net, install the pre-commit hook once:

```powershell
python scripts/install_hooks.py
```

Every `git commit` then runs `scripts/check_secrets.py` on the staged files. It blocks the commit if it finds something that looks like an API key: Google `AIza…` and `AQ.…` keys, Groq `gsk_…`, `sk-…` keys, GitHub tokens, private keys, or long random tokens. It shows only the first few characters of anything it finds. Run `python scripts/check_secrets.py --all` to check every tracked file. For a false alarm, add the comment `secret-check: ignore` at the end of that line.

## Adding a skill

`whatsapp.py`, `gmail.py` and `spotify.py` are complete examples. A minimal skill, `jarvis/skills/coin.py`:

```python
import random

from jarvis.router import skill


@skill("coin flip", r"\b(?:flip|toss) a coin\b", category="general", help="flip a coin")
def coin_flip(ctx, command, match):
    return f"It's {random.choice(['heads', 'tails'])}."
```

To let the AI pick your skill from natural language, give it `params=` and `command=`:

```python
@skill("timer", r"\bset (?:a )?timer\b(.*)$", help="set a timer for 5 minutes",
       params={"duration": "how long, e.g. 5 minutes"}, command="set a timer for {duration}")
```

`params` maps each argument to a description, a list of allowed values, or `("integer", "description")`; a trailing `?` marks it optional. `command` is a template (`"weather[ in {place}][ {day}]"`: bracketed parts are dropped when their argument is missing) or a function of the arguments. It must produce a phrase your own patterns match. Mark anything that sends, deletes or shuts down with `risky=True` (add `confirms=True` if the skill already asks). Keep the tool list small: it's sent with every routed command, and free AI tiers count tokens.

Then add `coin` to the import list in `jarvis/skills/__init__.py`. Order matters, because the first matching skill wins: a skill whose phrases start with "play" must come **before** `web`, or YouTube takes them (that's why `spotify` does). Integrations normally go before `apps`, and always before `knowledge` and `ai`. Add its phrases to the `ROUTES` table in `tests/test_jarvis.py`, and replace side effects (launching, keypresses, subprocess calls) with small module functions that `tests/conftest.py` can stub out.

Some guidelines:
- Use `ctx.say`, `ctx.ask` and `ctx.confirm` for conversation, and `ctx.state` for memory within a session.
- Use `jarvis.http.get_json` / `get_text` for network calls (they have timeouts, and tests can fake them).
- Ask with `ctx.confirm` before anything destructive or outward-facing.
- Return `None` when a phrase matched your pattern but isn't really for you.

## Air quality data notes

- **AQI formula:** Indian CPCB National AQI, calculated from **24-hour averages** of PM2.5, PM10 and NO2. Each pollutant gets a sub-index by linear interpolation within the CPCB breakpoints. The AQI is the highest sub-index, and that pollutant is reported as the main one. The open-ended top band (PM2.5 250+, PM10 430+, NO2 400+) is capped at 500.
- **Categories:** Good ≤50, Satisfactory ≤100, Moderate ≤200, Poor ≤300, Very Poor ≤400, Severe above 400. **Zones:** green ≤100, yellow ≤200, red above 200.
- **Open-Meteo** (default, no key) uses the CAMS atmospheric model on a grid of roughly 10–40 km. It's good for city-level readings, but **nearby areas often get identical numbers**, so the red zones it gives are area-level estimates, not individual neighbourhoods.
- **WAQI** (set `WAQI_TOKEN`) returns **real CPCB/DPCC monitoring station readings** within about 35 km of your location, which is much better for red zones. Note that WAQI reports its own AQI scale (based on US EPA), so its numbers can differ from the CPCB numbers Jarvis calculates from Open-Meteo.
- Geocoding uses OpenStreetMap Nominatim (with Open-Meteo geocoding as a fallback), and results are cached for the session.

## Roadmap

- **WhatsApp:** read unread chats, send to groups
- **VS Code:** recent projects, run tests by voice
- **Email:** reply to a thread, read attachments
- **Calendar:** invite people to events, move an event to another time
- **Hindi:** more phrase coverage in `translit.py`, and Hindi replies for the built-in skills (not just the AI)
- A custom-trained "Jarvis" wake word (the pretrained model needs "Hey Jarvis")
- Reminders that persist across restarts
