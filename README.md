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
| `PERSONALITY`, `NICKNAME` | `friend` (default: talks like a close Delhi friend, in casual Hinglish) or `professional`, and what Jarvis calls you. See [Personality and voice](#personality-and-voice). |
| `TTS_ENGINE`, `TTS_VOICE`, `TTS_RATE` | `edge` (default: Microsoft's free neural voices, needs internet) or `pyttsx3` (offline Windows voice); the neural voice (default `en-IN-PrabhatNeural`, male) and its speed. |
| `USER_GENDER` | `female` (default) or `male`: the Hindi verb forms Jarvis uses for you ("tu kaisi hai?" / "tu kaisa hai?"). |
| `VOICE_NAME`, `VOICE_RATE` | The offline fallback voice: part of a Windows voice name (`zira`, `david`, `heera`...) and its speed. |
| `WAKE_WORDS`, `FOLLOW_UP_SECONDS` | The wake phrases, and how long you can keep talking without one after a reply. |
| `WHATSAPP_SEND_DELAY` | Seconds to wait for WhatsApp to open a chat before pressing Enter (default 3). |
| `SPOTIFY_CLIENT_ID` | Lets Jarvis control Spotify (see [Connecting Google and Spotify](#connecting-google-and-spotify)). |
| `CALENDAR_REMINDERS`, `CALENDAR_REMINDER_MINUTES` | Spoken reminder and toast before each calendar event (on by default, 10 minutes before). |
| `PROJECT_ROOTS` | Folders to scan for code projects, separated by `;`. Defaults to `C:\Users\ADMIN\OneDrive\Desktop\ANJALI`. |

Free AI keys:

| Provider | Get a key | `AI_PROVIDER` | `AI_BASE_URL` | `AI_MODEL` |
|---|---|---|---|---|
| Groq (fastest, recommended) | <https://console.groq.com/keys> | `openai` | `https://api.groq.com/openai/v1` | `qwen/qwen3.8-27b` with `AI_REASONING_EFFORT=none` |
| Gemini (Google AI Studio) | <https://aistudio.google.com/apikey> | `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `gemini-3.5-flash-lite` |

**Gemini as a backup.** Keep Groq as the main AI and also set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`, default `gemini-3.5-flash-lite`). Then, if Groq returns a rate limit (429), a server error (5xx), times out, or rejects its key or model, the same request (chat or picking an action) is retried once on Gemini, and Jarvis says "Switching to my backup brain" once per switch.
- Picking an action on Gemini waits up to 8 seconds (`GEMINI_ROUTER_TIMEOUT`) and says "One sec" after 1.5 seconds.
- After Groq's *daily* limit, Gemini goes first until midnight IST. This is saved in `data/cache.json`, so it survives a restart, and Jarvis goes back to Groq by itself after midnight.
- If both fail, Jarvis says so in one sentence.
- "Which AI model are you using" says whether the backup is in use, and why.

Gemini is noticeably slower here (3 to 20 seconds, sometimes "high demand" errors), which is why it's the backup rather than the main AI.

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
- **Snappier replies.** Common short replies ("Ho gaya!", "Haan, bolo?") are cached as audio and start in about 30 ms. If a skill takes more than 1.5 seconds (`ONE_SEC_AFTER`), Jarvis says "One sec" first. Network calls time out after 6 seconds, the AI after 15. Weather and AQI are cached on disk for 10 minutes (`CACHE_MINUTES`) and place lookups for 30 days, in `data\cache.json`.
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

## Telegram phone remote

Control Jarvis on the laptop from your phone, by text or voice note. It uses long polling, so there's no server or webhook. It runs on a background thread in both the tray app and `main.py`.

**Setting it up (about 2 minutes):**
1. On your phone, open Telegram and search for **@BotFather** (the official one, with the blue tick).
2. Send `/newbot`. Give it a display name (for example "My Jarvis"), then a username that ends in `bot` (for example `anjali_jarvis_bot`).
3. BotFather replies with a token like `123456789:AAH...`. Treat it like a password. Put it in `.env` as `TELEGRAM_BOT_TOKEN=123456789:AAH...` (`.env` is git-ignored and checked by the secret scan).
4. Optional: in BotFather, send `/setprivacy` and choose your bot, then `/setjoingroups` and **Disable**, so nobody can add it to a group.
5. Restart Jarvis. The terminal says `[telegram] phone remote connected as @your_bot`.
6. Open your bot on your phone and send `/start`. Jarvis shows a **6-digit pairing code** in the laptop terminal and as a notification. Send that code back to the bot. It's valid for 5 minutes and gives you 3 tries.

Your chat id is saved in `data/telegram.json` (git-ignored). From then on, that one chat is the only one Jarvis listens to. Anyone else gets no reply at all, and is logged in `data/logs/actions.log`. `/unpair` removes the pairing.

**What you can do:** text anything you'd say out loud. It goes through the same router as voice: natural language, memory, web search and the friend personality. Jarvis replies in text and stays silent on the laptop, unless you add "laptop pe bol" or "say it out loud". Voice notes are transcribed with the same speech engine, decoded with ffmpeg if you have it, or otherwise with PyAV (already installed with faster-whisper) or pydub. `/status` gives battery, charging, CPU, RAM, disk and uptime. `/screenshot` or "screenshot bhej" sends a screenshot, after asking first. Reminders set from the phone arrive on the phone.

**Safety:**
- Only the normal Jarvis skills run. There are no shell commands and no code.
- Messages sent while the laptop was off are dropped, so a "shut down" can't run hours later.
- Every question a skill asks becomes **[Yes] [No]** buttons, and typed answers work too. The buttons expire after 60 s (`TELEGRAM_CONFIRM_SECONDS`), and no answer counts as no.
- Risky actions always ask from the phone, even ones that don't ask on the laptop: shutdown and restart, deleting or moving files, closing apps or windows, WhatsApp messages and email drafts, calendar changes, git pull, running a project, locking the laptop.
- Typing into windows, key presses and the clipboard are off from the phone unless you set `TELEGRAM_ALLOW_KEYBOARD=true`.
- Turning Wi-Fi off from the phone warns you first that it cuts the chat off.
- `/lock` (or "lock the laptop") locks at once, with no question, because locking is safe. "Close this window" still asks.
- **Files:** "send me my resume" or "resume bhej do" finds the file with the file index. When several files match, Jarvis asks which one. It then asks with buttons before sending the document. Files over 50 MB (`TELEGRAM_MAX_FILE_MB`, Telegram's limit for bots) are refused. So are folders, anything in system folders or Jarvis's own folder, and files that look like keys, tokens or passwords (`.env`, `.pem`, `.key`, `id_rsa`, password vaults).

**Notifications** (`TELEGRAM_NOTIFY=true`): reminders, timers, calendar alerts and memory date reminders set on the laptop also go to your phone. So do "battery below 15% and not charging" (once per drop; it re-arms after you charge or the battery is back above 20%) and "Jarvis started / stopped". Pairing codes and other laptop toasts never go to the phone.

**Status:** `/status` shows battery and charging, CPU, RAM, disk, the Wi-Fi network, what's playing, whether Jarvis is awake or asleep, the last action, and uptime.

**Reliability:** if the network drops or Telegram is down, Jarvis logs `[telegram] can't reach Telegram (...). Retrying in 5 s.` and reconnects, backing off from 5 s to 60 s. Jarvis itself keeps working, and reminders are never held up by a failed send. A rejected token is logged once, and the remote turns off. "typing..." shows in the chat while Jarvis works on a command.

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

## Personality and voice

**Friend mode** (`PERSONALITY=friend`, the default) makes Jarvis talk like a close friend from Delhi: casual Hinglish in Roman letters ("arre", "yaar", "chal", "haan bata", "done!"), warm and a bit witty, short. It's still honest: it tells you when you're wrong, says when it doesn't know, never makes up facts, and risky actions still need your yes ("Pakka shut down kar doon? 15 seconds mein"). `PERSONALITY=professional` keeps the polite assistant. Say **"talk professionally"** / **"formal mode"** or **"be my friend again"** / **"friend mode"** to switch for the session. `NICKNAME` is what it calls you.

- Fixed replies live in `jarvis/persona.py`, with 3 to 5 variants each per personality (picked at random, never the same one twice in a row): greetings by time of day ("Good morning Anjali! Chai pi li?"), done ("Ho gaya!", "Lo, kar diya"), didn't catch ("Kya bola? Phir se bol na"), reminders ("Oye Anjali! Yaad hai na, pani peena hai"), AQI ("Bahar mat ja yaar, AQI 312 hai. Mask pehen ke hi nikalna"), goodbyes, confirmations, errors and more. Other skill replies get a friend's opener ("Okay, ..." becomes "Chal, ...", "Done." becomes "Ho gaya!"). The AI chat and web answers get the same style in their instructions.
- Tell it how you feel ("I'm tired", "bore ho rahi hoon", "I'm stressed", "I'm so happy") and it answers like a friend: short and caring, maybe a break, music or a joke. After 1 am it sometimes (not every time, at most every 45 minutes) suggests sleeping.
- Hindi grammar follows both genders. Jarvis's own verbs follow the voice (male by default: "main kar dunga", "aa gaya", "samajh gaya"), and the way it talks to you follows `USER_GENDER` ("tu kaisi hai?", "thak gayi kya?", "aa gayi!"). Phrases in `persona.py` never hard-code a gendered verb (placeholders `{ta}`/`{ga}`/`{a}` for Jarvis, `{u_a}`/`{u_ta}` for you), and a test scans every variant for wrong forms. The AI's own text gets a grammar pass before it's spoken (`jarvis/grammar.py`), which fixes verbs addressed to you ("tu kaisa hai" -> "tu kaisi hai", "chai piyega?" -> "chai piyegi?") and Jarvis's first person ("main kar dungi" -> "main kar dunga"). It leaves dative and ergative forms alone ("tujhe pata hai", "tune khana khaya"), as well as impersonal ones ("kaam ho gaya") and English.

**It answers everything.** Anything that isn't a command goes to the AI, which picks one of five things, so there are no dead ends:
1. An action a skill does: it runs it.
2. Current or real-world facts (news, prices, people, places, "is X still...", anything after the model's training data): a web search, answered from the sources.
3. Knowledge, explanations, advice, opinions, maths, coding, life questions, jokes, chat: a friendly answer, 1 to 3 sentences, longer when you say "explain karo" / "detail mein bata". Opinions come with reasons; health, money and legal answers end with a short "doctor/CA se confirm kar lena" when it matters.
4. Something it can't do yet ("order me a pizza"): it says so casually and offers the closest thing it can do ("main Swiggy khol sakta hoon?"). Say "haan" and it does it. Only harmless alternatives (open a site or app, search, play music) are offered this way.
5. Too unclear: ONE short question, like a friend ("Kaunsa match? India wala?").

It answers in your language (English, Hinglish, or Hinglish in Roman letters when you speak Hindi), says "Pakka nahi pata yaar" instead of guessing, and never makes up facts. "Aur?", "kyun?", "uska kya?" go straight to the chat AI, which remembers the last 10 exchanges (separate from the 3 turns sent for picking actions); after a web answer, "aur?" reads more from the same pages. If the AI is down or rate-limited, the Gemini backup is tried once, and if that fails too Jarvis says so in friend style ("Net thoda slow hai yaar, ek minute mein try karte hain"). With no AI key at all, it looks the question up on the web instead.

**A good friend gives safe advice.** The chat prompt has a "good friend" section: for sleep, no chai or coffee at night (phone away, dim lights, haldi doodh, slow breathing, a calm playlist); nothing risky for health, and a casual "doctor ko dikha de" for pain, fever, chest or breathing problems, or anything lasting days; for money and legal questions, a useful answer plus "CA/lawyer se confirm kar lena". If you sound really low it answers with care (no jokes), asks how you're doing, suggests talking to someone you trust, and shares the free Tele-MANAS helpline (14416) if it sounds serious. If you ever say you want to die or hurt yourself, Jarvis always answers with care, Tele-MANAS (14416) and 112, straight away, without waiting for the AI.

**Lean routing (Groq's free tier allows 7,000 input tokens a minute).**
- Obvious chat (opinions, advice, "explain...", "tu kaisa hai", sleep or health worries, low mood, "aur?", "kyun?") skips the routing call and goes straight to the chat AI: one call instead of two.
- Everything else is routed in two stages. A local pre-filter (keywords, Hinglish synonyms like awaaz -> volume and yaad dila -> reminder, and fuzzy matching against each skill's name, examples and parameters) picks at most 3 likely tools; only those go to the AI, with `web_search`, the previous turn's tool (for "make it 5 minutes instead") and one small `reply` tool that also covers "can't do" offers and clarifying questions.
- Chat keeps the last 10 exchanges, but sends them within about 600 tokens: older ones are folded into one "Earlier in this chat..." line.
- `python main.py --timing` shows the input tokens of each request (`routing input 812 tokens`, `chat input ~690 tokens`; `~` = estimated).

Measured with Groq's own token counts: a routed question went from about 2,500 input tokens to 800-990. Ten questions fired 3 seconds apart: before, 5 of 10 hit Groq's limit and went to Gemini (one failed on both); after, 0 of 10, with 6 of them not needing a routing call at all.

**Natural voice.** `TTS_ENGINE=edge` uses [edge-tts](https://github.com/rany2/edge-tts) (Microsoft's free neural voices) and plays the audio with pygame's mixer. Each sentence starts downloading as soon as it's queued, so the next sentence is ready when the current one ends. The common short phrases are cached as mp3 in `data\tts_cache\` (filled in the background at startup) and play instantly. If you're offline or edge-tts fails, that sentence and the next minute's are spoken by the Windows voice (pyttsx3) instead. Ctrl+Alt+J still cuts it off mid-sentence.

Measured on this laptop: an uncached sentence starts playing about 1.1 to 1.3 s after it's ready (edge-tts sends its first audio after 1.1 to 1.8 s, so streaming inside a sentence wouldn't gain much); cached phrases start in 0.02 to 0.12 s. Importing edge-tts takes about 8 s here, so it loads in the background: Jarvis starts in about a second with the Windows voice and switches to the neural voice when it's ready. Choosing the voice: candidates were compared by synthesising 10 Hinglish and 5 English lines, running Google speech recognition on the audio, and comparing the words. Of the male voices, `en-IN-PrabhatNeural` came back clearest (80% of words on average) and produced audio for every line, while `hi-IN-MadhurNeural` failed to produce any audio for 3 of the 15 lines ("No audio was received", even on retry). Prabhat is the default. Female options: `hi-IN-SwaraNeural` (73% on mixed Hinglish in an earlier run) and `en-IN-NeerjaNeural` (67%); set one in `TTS_VOICE` and Jarvis's own verbs switch to the female forms. Keep `VOICE_NAME` (the offline fallback) the same gender: `david` or `zira`.

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
- "Open best laptops under 60000 in browser", "show me in Chrome": Google results in the browser

**Web search, answered by voice** (no browser window)
- "Search the web for the latest iPhone", "look up petrol price in Delhi today", "google Python decorators", "sensex internet pe dekho"
- Obvious web questions skip the AI routing call (a regex in `jarvis/skills/web_questions.py` catches words like latest, today, aaj, price, rate, bhav, score, who won, current): "who won the match yesterday", "petrol price in Delhi today", "latest iPhone", "sone ka bhav aaj kya hai". Other current-events questions still reach the web through AI routing. Questions about you ("my", "I", "you") and phrases other skills own ("weather today", "AQI today", "news") are left alone.
- Follow-ups: "tell me more", "which website said that" / "source?", "open the first source" / "open that in Chrome", "search more about its battery life"

How it works (`jarvis/skills/browse.py`):
1. Jarvis says "Checking online" (in the background) and searches with the first available provider in `SEARCH_PROVIDER` (default `auto`). Hinglish questions are searched in English words and "today"/"yesterday" become dates ("who won the cricket match yesterday" -> "cricket match result 24 November 2026").
   - **Tavily**, if `TAVILY_API_KEY` is set (free tier at <https://app.tavily.com>): its own answer plus the top 5 results.
   - **Groq's web-search models** (`groq/compound-mini`, `groq/compound`), if your Groq key has them. Jarvis checks the model list once a day; the current free key doesn't.
   - **Free, no key** (the `ddgs` package): the top 5 results with snippets; Yahoo and ddgs's "auto" are asked in parallel and the first to answer wins.
2. **Quick answer first:** the AI answers from the search results alone (Tavily's answer rephrased, or the snippets) in 1 or 2 spoken sentences, with the date for time-sensitive facts, streamed sentence by sentence. If the question leaves something out ("which match?"), it names the one it found.
3. Only if the results don't answer it (or for "tell me more") are the top 3 pages read in parallel (4 s each; PDFs and paywalled sites are skipped), their main text extracted with trafilatura, trimmed to about 1,500 words, and the AI asked again with the rule "answer ONLY from these". It says "I couldn't find a reliable answer" only when the pages don't answer either. Hindi or Hinglish questions get Hinglish answers in Roman letters.
4. The answer and the numbered source titles and URLs are printed in the terminal.

The search has an 8-second budget (`SEARCH_BUDGET`). Results are cached for 30 minutes (`SEARCH_CACHE_MINUTES`) in `data/cache.json`. The answer goes through the same AI as chat, so the Gemini backup covers Groq's rate limits. `python main.py --timing` adds `web search`, `page reading`, `AI to first sentence` and `first answer sentence ready` to each turn's line.

Speed on this laptop (free ddgs, no Tavily key), first answer sentence: about 2 to 5 s typically (it was 9 to 13 s before the quick-answer path). It's slower when Yahoo drops the connection (ddgs "auto" then takes 2 to 5 s) or when Groq's Qwen model has one of its occasional 2 to 4 s first-token pauses (`openai/gpt-oss-20b` measured a steadier 0.2 to 0.6 s), and about 4 s more when the pages have to be read.

Limits: sites that draw their numbers with JavaScript (live scores, stock tickers) often give no readable text, and then Jarvis says it couldn't find a reliable answer. It can only be as current as the pages the search returns.
- "News", "news about cricket", "tech news"

**Memory** (`skills/memory.py`, kept in `data/memory.json`, which is git-ignored)
- "Remember that Dhruv's birthday is 12 Feb", "remember I like cold coffee", "yaad rakhna ki meri bike ki service March mein hai". Each memory gets a category (personal, people, dates, preferences, work, other). A date in it sets a 9 am spoken reminder and a toast on that day. Birthdays and anniversaries remind you yearly, plus the day before.
- "Dhruv ka birthday kab hai?", "what did I tell you about my bike?", "what do you know about me?" Recall uses keyword and fuzzy matching. With an AI key, the AI picks the best of the matches and answers like a friend, using only what you told it.
- The AI chat gets the 5 most relevant memories with each question, plus same-topic ones (food, music, films...). It's told to use them only when they help ("Cold coffee bana le, tujhe toh pasand hai na").
- "Forget that" / "bhool ja ki..." (reads it back, asks first), "update: Dhruv's birthday is 14 Feb", "list my memories", "export my memories" (a readable file in Documents). "Undo that" also works.
- Passwords, OTPs, PINs, card and bank numbers, Aadhaar and PAN are refused. Health and sad memories are marked sensitive: Jarvis never mentions them unless you bring the topic up first.

**Notes, reminders and timers**
- "Take a note buy milk", "note down the meeting is at 5"
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
- "What's playing" (any app: Spotify, YouTube in Chrome, VLC...), "pause", "play", "next", "previous"

**Full laptop control** (Windows; see [Laptop control](#laptop-control) for the safety rules)
- Apps: "open Figma" (any installed app, fuzzy: "what's app", "vs code"), "close Notepad", "is Chrome open?", "list open apps", "restart Spotify", "close all apps", "refresh apps"
- Windows: "switch to Chrome", "minimize / maximize / restore Notepad", "minimize everything", "snap Chrome left", "close this window", "full screen", "move this to the other screen", "new desktop", "next desktop", "what's on my screen right now"
- Typing: "type hello comma see you at 5", "type in terminal git status", "press enter", "select all", "copy", "paste", "undo", "save", "new tab", "reopen tab", "go back", "refresh", "zoom in", "scroll down", "read my clipboard", "copy that"
- Files: "open my resume", "find the bogie pitch deck", "open the latest download", "show files I downloaded today", "open my Downloads folder", "create folder Receipts on desktop", "rename notes.txt to ideas.txt", "move resume to Documents", "zip the jarvis folder", "delete old notes.txt" (Recycle Bin), "undo that"
- Settings: "turn off wifi", "wifi status", "connect to Home WiFi", "Bluetooth on", "is airplane mode on?", "dark mode on", "night light", "do not disturb on", "battery saver on", "best performance mode", "volume 30", "Spotify volume 30", "mute Chrome", "mute my mic", "switch to headphones", "empty the recycle bin", "clear temp files", "open display / sound / bluetooth / update / privacy settings"
- Routines: "start work mode", "movie mode", "break time", "create a routine called study time", "list my routines", "delete routine study time", "every weekday at 9:30 start work mode"
- "What did you just do", "undo that"
- Hinglish works too: "yaar wifi band kar de", "chrome pe switch kar", "meri resume khol", "dark mode laga do", "laptop band kar do"

**Everything else**
- "What time is it", "what's the date", "who are you", "how are you", "thank you"
- "I'm tired" / "I'm bored" / "bore ho rahi hoon" / "I'm stressed" (a friend's reply)
- "Talk professionally" / "formal mode", "be my friend again" / "friend mode"
- "What is 25 times 4", "10 percent of 250", "square root of 144", "2 to the power of 10"
- "Tell me a joke"
- "Who is Sachin Tendulkar", "tell me about the Taj Mahal", "what is Python" (Wikipedia)
- Facts are verified, never answered from memory (`skills/facts.py`): "who wrote / founded / invented ...", "capital of ...", "when was ...", "how many ...", and the Hinglish forms ("godaan kisne likha", "australia ki rajdhani kya hai"). Jarvis does a quick web search and answers only from the results. If they don't confirm it, it says "Pakka nahi hoon yaar" and offers a Google search.
- Anything else goes to the AI (needs `AI_API_KEY`; without one, Jarvis searches the web): questions, advice, opinions ("tu kya sochta hai, mujhe MBA karna chahiye?"), explanations ("explain recursion simply"), and follow-ups ("aur?", "kyun?"). Things it can't do get an alternative ("order me a pizza" -> "Swiggy khol doon?"). "Forget our conversation" clears its memory.
- Speak Hindi or Hinglish to the AI and it replies in Hinglish written in Roman letters, which every voice can read.
- "Switch to Hindi" makes speech recognition listen for Hindi (`hi-IN`), and "switch to English" switches back. This lasts for the session only. In Hindi mode Google writes what you say in Devanagari, and `jarvis/translit.py` rewrites commands into English before routing: "क्रोम खोलो" becomes "open chrome". Messages and notes keep your exact words, and anything that isn't a command goes to the AI unchanged. See the Hindi section of [COMMANDS.md](COMMANDS.md).

## Laptop control

Jarvis can drive the whole laptop, Alexa-style. Each area is its own skill file in `jarvis/skills/` (`app_index`, `windows`, `keyboard_control`, `files`, `settings_control`, `media_session`, `routines`, `history`), and the low-level Windows calls live in `jarvis/desktop.py`, so the tests fake them all.

**Apps.** Jarvis indexes every installed app: Start-menu shortcuts (yours and all users'), Store apps (`Get-StartApps`, launched through `shell:AppsFolder\<AppID>`) and the built-in list. The index is cached in `data/app_index.json`, rebuilt once a day in the background, and rebuilt on "refresh apps". "Open X" fuzzy-matches it and handles speech slips ("what's app", "spotfy", "calculater"). If 2 or 3 apps are equally close ("visual studio": VS Code or Visual Studio 2022?), it asks which one. Typing the name into the Start menu is only the last resort.

**Files.** A background index keeps the *names and modified dates* of everything in Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive and `PROJECT_ROOTS` (never the contents). It skips `node_modules`, `.git`, virtualenvs and AppData, and is rebuilt every 6 hours (`FILE_INDEX_HOURS`). Matching is fuzzy: "cv" finds a resume, and "deck" prefers .pptx. When several files match, Jarvis reads out the top 3 and asks which one.

**Routines** live in `data/routines.json`, which is copied from [routines.example.json](routines.example.json) the first time. A routine is a list of ordinary Jarvis commands, plus optional `{"wait": seconds}` and `{"say": "..."}` steps. The built-in ones are work mode, break time, movie mode and the shutdown routine. Schedules ("every weekday at 9:30 start work mode") are saved in the same file and run while Jarvis is running. A scheduled run never says yes for you, so a step that needs a confirmation (closing apps, shutting down) is skipped, and Jarvis tells you so.

**Safety rules** (these apply to everything above):
- Jarvis **always asks first** before it deletes, moves or renames a file, zips or creates something, closes an app that might have unsaved work, shuts down or restarts, empties the Recycle Bin, clears temp files, or types more than 200 characters (`TYPE_CONFIRM_CHARS`). A "no" does nothing.
- **Delete only uses the Recycle Bin** (`send2trash`). Nothing is ever deleted permanently, and "undo that" restores it.
- **System folders are off limits:** Windows, Program Files, ProgramData, AppData, drive roots, your user folder itself, and Jarvis's own folder. They're never indexed, and file actions there are refused.
- The **registry** is only written for the two theme values (`AppsUseLightTheme`, `SystemUsesLightTheme`).
- **No typing into terminals** unless you say "type in terminal ...". Enter and paste are refused there too. Jarvis asks before typing into a **password box**, and never logs what it typed there. **Banking and payment sites** get no typing, pasting or key presses at all. Clipboard text that looks like a password, OTP or API key is never read out.
- "Close X" closes windows the way the X button does, so the app can still ask you to save. It never force-kills.
- Turning Wi-Fi off asks first when speech recognition is online (`STT_ENGINE=google`), because Jarvis couldn't hear you afterwards. Muting the mic asks first for the same reason.
- **Every action is logged** to `data/logs/actions.log`. "What did you just do" reads the last few back, and "undo that" reverts the last action that can be undone (moves, renames, deletes, new folders, zips, dark mode, volume, window changes, Wi-Fi and Bluetooth, deleted routines). It asks first.

Needs `pywin32`, `pywinauto`, `pycaw`, `send2trash` and the `winrt-Windows.Media.Control` / `winrt-Windows.Devices.Radios` packages (all in `requirements.txt`; the WinRT ones support Python 3.13). Windows doesn't let apps flip Night light or airplane mode, so Jarvis opens those settings pages instead. Do Not Disturb is switched through the Settings page with UI Automation, and if that fails, the page is left open for you.

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
jarvis/tts.py           neural voice: edge-tts synthesis + mp3 cache (data/tts_cache), pygame playback
jarvis/persona.py       friend / professional personality: reply variants, AI style, feelings, late-night nudge
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
jarvis/telegram_remote.py  Telegram phone remote: pairing, buttons for risky actions, text/voice notes, /status
jarvis/desktop.py       low-level Windows calls: windows, focus, WM_CLOSE, Unicode typing, clipboard, password box
jarvis/actions.py       action log (data/logs/actions.log), undo stack, protected-folder guard
jarvis/hinglish.py      regex pieces for verb-last Hinglish ("wifi band kar de", "chrome pe switch kar")
jarvis/skills/
    __init__.py         the skill ORDER (earlier wins): general, notes, history, media_session, whatsapp, gmail,
                        calendar, settings_control, spotify, browse, routines, keyboard_control, windows, files,
                        system, web, vscode, app_index, apps, weather, aqi, web_questions, knowledge, ai, briefing
    memory.py           long-term memory (data/memory.json): remember, recall, forget, update, export, date reminders
    history.py          "what did you just do", "undo that"
    media_session.py    what's playing in any app; play/pause/next/previous to the active media session
    settings_control.py Wi-Fi, Bluetooth, dark mode, DND, battery saver, power mode, per-app volume, mic,
                        audio output, Recycle Bin, temp files, ms-settings: pages
    routines.py         routines and schedules (data/routines.json)
    keyboard_control.py type, keys, shortcuts, scroll, clipboard
    windows.py          switch, minimize/maximize/restore, snap, full screen, other screen, virtual desktops
    files.py            file-name index, open/find, create folder, rename, move, zip, delete to Recycle Bin
    app_index.py        installed-apps index (data/app_index.json), fuzzy match, is open / list / restart / close
    general.py          exit, sleep, help, greetings, time, date, calculator, jokes
    notes.py            notes file, reminders, timers
    gmail.py            unread count, read/search email, drafts only; "connect google"
    calendar.py         today/week/next meeting, add/cancel events, reminders before events
    spotify.py          play songs/liked songs/playlists, pause/skip, now playing, volume
    briefing.py         good morning briefing (parallel sections, 40 s budget, BRIEFING_TIME), good night
    system.py           volume, media, brightness, screenshot, battery, status, lock, power
    web.py              YouTube, Google News
    browse.py           web search answered by voice: search, quick answer from the results, pages if needed
    web_questions.py    regex fast path to browse for obvious web questions (no AI routing call)
    whatsapp.py         WhatsApp messages, calls, contacts (data/contacts.json)
    vscode.py           project registry, open in VS Code, git status/pull, run dev servers
    apps.py             open / close apps and websites
    weather.py          Open-Meteo forecast
    aqi.py              CPCB AQI, pollutants, safety advice, red zones
    knowledge.py        Wikipedia summaries
    ai.py               OpenAI-compatible fallback with short conversation memory
tests/                  offline tests: conftest.py blocks network, real apps, Google, Spotify and every Windows
                        API (windows, keys, clipboard, files, registry, radios, audio, media sessions);
                        test_jarvis.py, test_laptop_control.py, test_whatsapp.py, test_vscode.py, test_gmail.py,
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
