# Jarvis: personal voice assistant

Jarvis is a voice assistant for a Windows laptop, written in Python 3.13. It started as an AQI bot and still focuses on air quality. It gives live CPCB AQI, PM2.5, PM10, NO2 and red zones for any area or pincode in India. It also handles weather, apps, websites, YouTube, news, notes, reminders, laptop controls, maths and general questions.

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
| `AI_API_KEY` | Lets Jarvis answer any question. Works with any OpenAI-compatible API; set `AI_BASE_URL` and `AI_MODEL` for other providers. |
| `WAQI_TOKEN` | A free token from <https://aqicn.org/data-platform/token/>. Red zones then come from real CPCB monitoring stations. |
| `DEFAULT_LOCATION` | Used until you tell Jarvis your area. Defaults to New Delhi. |
| `VOICE_NAME`, `VOICE_RATE` | Part of a Windows voice name (`zira`, `david`, `heera`...) and the speaking speed. |
| `WAKE_WORDS`, `FOLLOW_UP_SECONDS` | The wake phrases, and how long you can keep talking without one after a reply. |
| `WHATSAPP_SEND_DELAY` | Seconds to wait for WhatsApp to open a chat before pressing Enter (default 3). |
| `PROJECT_ROOTS` | Folders to scan for code projects, separated by `;`. Defaults to `C:\Users\ADMIN\OneDrive\Desktop\ANJALI`. |

For WhatsApp, copy `contacts.example.json` to `data\contacts.json` and add your contacts (country code plus number, digits only), or add them by voice. For extra project names, copy `projects.example.json` to `data\projects.json`. Everything in `data\` is git-ignored.

If `pip install PyAudio` fails, update pip first (`python -m pip install -U pip`); PyAudio ships Windows wheels for recent Python versions.

## Running

```powershell
python main.py            # voice, say "Jarvis ..." first
python main.py --no-wake  # voice, every phrase is a command
python main.py --text     # type commands (wake word not needed)
python main.py --mute     # print replies instead of speaking
python -m pytest -q       # run the offline tests
```

With no microphone, Jarvis switches to text mode automatically. After it replies you have `FOLLOW_UP_SECONDS` (20 by default) to carry on without saying the wake word. Press Ctrl+C to quit at any time.

## What you can say

**Wake and control**
- "Jarvis", "Hey Jarvis", or "OK Jarvis", at the start or end of a sentence
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

## Architecture

```
main.py                 entry point: flags, greeting, wake-word loop, strip_wake_word()
config.py               every setting, read from .env with defaults
jarvis/voice.py         Voice: TTS on a worker thread (thread-safe speak), Google STT, text mode
jarvis/router.py        @skill / @fallback registry, Context (say/ask/confirm), handle()
jarvis/http.py          shared requests.Session with timeouts: get_json / get_text / post_json
jarvis/geo.py           area name / pincode -> Place(name, lat, lon), location_from_command()
jarvis/timeparse.py     spoken durations ("an hour and 30 minutes") and clock times ("at 6 pm")
jarvis/skills/
    __init__.py         imports skills IN ORDER: general, notes, system, web, whatsapp, vscode,
                        apps, weather, aqi, knowledge, ai
    general.py          exit, sleep, help, greetings, time, date, calculator, jokes
    notes.py            notes file, reminders, timers
    system.py           volume, media, brightness, screenshot, battery, status, lock, power
    web.py              YouTube, Google, Google News
    whatsapp.py         WhatsApp messages, calls, contacts (data/contacts.json)
    vscode.py           project registry, open in VS Code, git status/pull, run dev servers
    apps.py             open / close apps and websites
    weather.py          Open-Meteo forecast
    aqi.py              CPCB AQI, pollutants, safety advice, red zones
    knowledge.py        Wikipedia summaries
    ai.py               OpenAI-compatible fallback with short conversation memory
tests/                  offline tests: conftest.py blocks network and real apps;
                        test_jarvis.py, test_whatsapp.py, test_vscode.py
```

How a command is handled:

1. `main.py` hears a phrase, strips the wake word, and calls `router.handle(ctx, command)`.
2. The router tries each skill **in registration order**, testing its regex patterns.
3. When one matches, the handler runs. Returning a string speaks it; returning `""` or `True` means "handled"; returning `None` means "not mine", and the router moves on to the next skill. For example, the calculator returns `None` for "what is python", so Wikipedia answers instead.
4. If nothing handles it, the AI fallback does.
5. Network errors and crashes inside a skill are caught and turned into a short spoken apology. Jarvis never crashes.

## Adding a skill

`whatsapp.py` and `vscode.py` are complete examples. A minimal skill, `jarvis/skills/spotify.py`:

```python
from jarvis.router import skill
from jarvis.skills.apps import launch


@skill("spotify playlist", r"\bplay (?:my )?(.+?) playlist on spotify$", help="play my workout playlist on spotify")
def spotify_playlist(ctx, command, match):
    name = match.group(1)
    launch(f"spotify:search:{name}")
    return f"Searching Spotify for your {name} playlist."
```

Then add `spotify` to the import list in `jarvis/skills/__init__.py`. Order matters, because the first matching skill wins: put it **before** `web` here, or "play X on spotify" goes to YouTube. Integrations normally go before `apps`, and always before `knowledge` and `ai`. Add its phrases to the `ROUTES` table in `tests/test_jarvis.py`, and replace side effects (launching, keypresses, subprocess calls) with small module functions that `tests/conftest.py` can stub out.

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
- **Spotify:** play playlists and liked songs through the Spotify API
- **Calendar and email:** today's meetings, read and draft emails
- **Hindi commands:** `SPEECH_LANGUAGE=hi-IN`, Hindi patterns and replies
- Offline wake word (Porcupine / openWakeWord) and offline speech recognition
- Reminders that persist across restarts
