# Jarvis commands cheat sheet

## Start and stop

| Command | What it does |
|---|---|
| `python main.py` | Voice mode. Say "Jarvis" before each command. |
| `python main.py --no-wake` | Voice mode without the wake word. Every phrase is a command. |
| `python main.py --text` | Type commands instead of speaking. No wake word needed. |
| `python main.py --mute` | Print replies instead of speaking them. Combine with the others, e.g. `--text --mute`. |
| `python main.py --timing` | Print how long each step of every turn took (wake word, speech, speech-to-text, skill, voice). |
| `python -m jarvis.benchmark` | Compare Google and Whisper speech-to-text on your recorded samples. |

**Wake word:** say **"Hey Jarvis"**, wait for the **beep**, then say the command. The wake word is detected offline on your laptop. (If the offline detector can't load, it falls back to "Jarvis", "Hey Jarvis" or "OK Jarvis" at the start or end of the sentence.)
**Interrupt:** press **Ctrl+Alt+J** while Jarvis is talking to stop it and give a new command.
**Microphone:** "recalibrate mic" if it struggles to hear you, or keeps hearing noise. "Record samples" saves 5 test phrases for `python -m jarvis.benchmark`.
After Jarvis replies you have **20 seconds** to carry on without the wake word.

**Pause and resume:** "go to sleep" or "stop listening", then just "Jarvis" to wake it up.

**Stop:** "goodbye", "exit", "turn off Jarvis", or press **Ctrl+C**.

## Running in the background

| Command | What it does |
|---|---|
| Double-click `run_jarvis.pyw` (or `pythonw run_jarvis.pyw`) | Start Jarvis in the system tray, with no console window |
| `python install_startup.py` / say "start with Windows" | Start Jarvis in the tray every time you log in |
| `python uninstall_startup.py` / say "don't start with Windows" | Stop starting with Windows |
| **Ctrl+Alt+J** | Wake Jarvis instantly. You'll hear a beep, then just say the command, with no wake word. |

**Tray icon** (orange "J"): a green dot means listening, grey means sleeping, red means the mic is muted. Hover over it for the status.
Right-click it for **Wake up, Sleep, Mute mic, Open COMMANDS.md, Open notes, Open logs, Restart Jarvis, Quit**.
Reminders and timers also pop up a Windows notification, so you see them while you're muted or in a meeting.
Starting Jarvis a second time doesn't open a second copy; it wakes the one already running.

**Help:** "help" lists the categories; "help with WhatsApp", "help laptop" or "what can you do with VS Code" gives examples for one category.

---

**Long answers:** lists (help, news, calendar, email, notes, reminders, projects) are spoken 3 at a time. Say "yes" to "Want more?" to hear the rest. The full list is printed in the terminal. The AI answers in 1 or 2 sentences unless you say "explain in detail".

## Air quality
Live Indian CPCB AQI for any area or pincode. Jarvis remembers your area for the session.

- "What's the AQI" / "air quality in Noida" / "pollution in 110085" / "is there smog"
- "PM2.5" / "PM 2.5 in Dwarka" / "PM2 point 5"
- "PM10" / "PM10 in Gurugram"
- "NO2 level" / "nitrogen dioxide in Anand Vihar"
- "Is it safe to go out" / "do I need a mask" / "is it safe for a morning walk"
- "Red zones" / "most polluted areas"
- "My area is Rohini" / "I live in Lajpat Nagar" / "110085"

## Time and weather
- "What time is it" / "what's the date today"
- "What's the weather" / "weather in Mumbai tomorrow" / "temperature"
- "Will it rain tomorrow" / "do I need an umbrella"

## Apps
- "Open Chrome" / "open VS Code and Notepad" / "open WhatsApp" / "open wifi settings"
- "Open GitHub" / "open Gmail" / "open github.com" / "open example dot com" / "open Gmail in Chrome"
- "Open Figma" / "open snipping tool": any installed app, Store apps included. Speech slips are fine: "what's app", "spotfy", "calculater". If two or three apps are equally close ("open visual studio"), Jarvis asks which one. Typing into the Start menu is only the last resort.
- "Close Chrome" / "quit Spotify" / "close this window" / "close this tab": closes like the X button. Apps that might have unsaved work (Notepad, Word, VS Code...) ask first.
- "Is Chrome open?" / "list open apps" / "what apps are open"
- "Restart Spotify" / "close all apps" (asks first; terminals are left alone)
- "Refresh apps" (rebuilds the installed-apps list; it also refreshes itself daily)

## Laptop controls
- "Volume up" / "volume up by 20" / "turn down the volume" / "mute" / "volume 30"
- "Pause the music" / "resume" / "play" / "next song" / "previous song" (these go to whatever is playing: Spotify, YouTube in Chrome, VLC...; otherwise the media keys)
- "What's playing" / "what song is this" (any app)
- "What did you just do" / "undo that" (asks first)
- "Brightness 70" / "brightness up" / "dim the screen"
- "Take a screenshot" (saved to Pictures\Jarvis Screenshots)
- "Battery status" / "system status" / "CPU usage"
- "Lock the laptop"
- "Shut down the laptop" / "restart in 10 minutes" / "turn off my computer" (always asks first)
- "Cancel shutdown"
- "Put the laptop to sleep" (asks first)
- "Start with Windows" / "don't start with Windows" (asks first)

## Windows
- "Switch to Chrome" / "go to Notepad" / "chrome pe switch kar" (if it isn't open, Jarvis offers to open it)
- "Minimize Chrome" / "maximize this window" / "restore Notepad"
- "Minimize everything" / "show desktop"
- "Snap Chrome left" / "snap this window right"
- "Close this window" (asks first if there might be unsaved work)
- "Full screen" / "exit full screen"
- "Move this to the other screen"
- "New desktop" / "next desktop" / "previous desktop" / "close this desktop"
- "What's on my screen right now" (the app in front and its window title)

## Typing and keyboard
- "Type hello comma see you at 5 full stop": types into the window in front, keeping your capitals. Says "comma", "full stop", "question mark" and "new line" as punctuation.
- "Type in terminal git status": the only way to type into a terminal. Jarvis never presses Enter for you there.
- Asks first before typing into a password box, or typing more than 200 characters. Never types on banking or payment sites.
- "Press enter" / "press escape" / "press tab 3 times" / "press F5"
- "Select all" / "copy" / "cut" / "paste" / "undo" / "redo" / "save"
- "New tab" / "close tab" / "reopen tab" / "next tab" / "go back" / "go forward" / "refresh"
- "Zoom in" / "zoom out" / "reset zoom" / "find on this page"
- "Scroll down" / "scroll up a lot" / "go to the top"
- "Read my clipboard" (text only, 2 sentences; passwords, OTPs and keys are never read out)
- "Copy that" (copies Jarvis's last answer)

## Files
Jarvis knows the **names** of your files in Desktop, Documents, Downloads, Pictures, Videos, Music, OneDrive and your project folders (never their contents), refreshed every 6 hours.
- "Open my resume" / "meri resume khol" / "open the bogie pitch deck": when several files match, Jarvis reads out the top 3 and asks which one
- "Find the bogie pitch deck" / "where is my resume" / "meri resume kahan hai"
- "Open the latest download" (programs and installers ask before running)
- "Show files I downloaded today" / "yesterday" / "this week"
- "Open my Downloads folder" / "open documents"
- "Create folder Receipts on desktop" / "rename notes.txt to ideas.txt" / "move resume to Documents" / "zip the jarvis folder" (each asks first)
- "Delete old notes.txt": Recycle Bin only, asks first, never a permanent delete
- "Undo that": puts it back (moves, renames, deletes, new folders, zips)
- "Refresh files" (rescans now)
- Windows, Program Files, ProgramData, AppData and other system folders are never touched.

## Settings
- "Turn off wifi" / "wifi on" / "wifi status" / "yaar wifi band kar de" (asks first if you're talking to Jarvis, since speech recognition needs the internet)
- "Connect to Home WiFi" (saved networks only)
- "Turn on Bluetooth" / "Bluetooth off" / "is Bluetooth on?" / "is airplane mode on?"
- "Dark mode on" / "switch to light mode" / "dark mode laga do"
- "Night light" (opens its settings; Windows doesn't let apps flip it)
- "Do not disturb on" / "DND off" / "focus mode off"
- "Battery saver on" / "battery saver off" / "best performance mode" / "balanced power mode"
- "Volume 30" / "Spotify volume 30" / "Chrome volume 50" / "mute Chrome" / "unmute Chrome"
- "Mute my mic" / "unmute mic" (asks first: Jarvis can't hear you while it's muted)
- "Switch to headphones" / "switch to speakers" / "switch audio to AirPods"
- "Empty the recycle bin" / "clear temp files" (both ask first; temp is your own temp folder only)
- "Open display settings" / "sound" / "wifi" / "bluetooth" / "battery" / "apps" / "updates" / "privacy" / "storage" / "notifications" / "taskbar"... settings

## Routines
One phrase runs several commands. They're in `data/routines.json` (copied from `routines.example.json` the first time), so you can edit them by hand too.
- "Start work mode": VS Code with bogie, Gmail and GitHub in Chrome, your Spotify focus playlist, Do not disturb on, volume 30
- "Break time": pauses the music, Do not disturb off, reminds you in 15 minutes to get back to work
- "Movie mode": dark mode, brightness 40, volume 70, Netflix
- "Shutdown routine": a reminder to save your work, closes your apps (asks first), shuts down in 5 minutes (asks first)
- "Create a routine called study time": Jarvis asks for each step ("open notion", "do not disturb on", "wait 5 seconds"...). Say "done" to finish.
- "List my routines" / "delete routine study time" (asks first)
- "Every weekday at 9:30 start work mode" / "every day at 7 pm start movie mode" / "on weekends at 10 am start break time" / "cancel the work mode schedule". These run while Jarvis is running. A scheduled run never says yes for you, so a step that needs your yes is skipped and you're told.

## Web
- "Play Arijit Singh songs" / "play lofi beats on YouTube"
- "Search lofi music on YouTube"
- "News" / "news about cricket" / "tech news"

## Web search (answered by voice)
Jarvis says "Checking online", searches, and answers in 1 or 2 sentences from the search results, with the date for prices, scores and news (usually 2 to 5 seconds). It reads the top pages only if the results don't answer it. The full answer and the numbered sources are printed in the terminal. If the pages don't clearly answer either, it says "I couldn't find a reliable answer" instead of guessing.
- "Search the web for the latest iPhone" / "look up petrol price in Delhi today" / "google Python decorators"
- "Who won the match yesterday" / "who is the current CEO of Twitter" / "latest iPhone" / "gold rate today" (no "search" needed; these skip the AI routing step)
- "Sensex internet pe dekho" / "aaj Delhi mein petrol ka rate kya hai" (answers in Hinglish)
- Then: "tell me more" (reads the pages for a longer answer), "which website said that" / "source?", "open the first source" / "open that in Chrome", "search more about its battery life"
- "Open best laptops under 60000 in browser" / "show me in Chrome": opens Google results instead

## Memory
Jarvis remembers things for good, like a friend. Memories are kept in `data/memory.json`, which is never committed to git.
- "Remember that Dhruv's birthday is 12 Feb" / "remember I like cold coffee" / "yaad rakhna ki meri bike ki service March mein hai" / "Dhruv ka birthday 12 Feb ko hai, yaad rakhna"
- A date in a memory sets a reminder: Jarvis says it and shows a toast at 9 am on that day (`MEMORY_REMINDER_HOUR`). Birthdays and anniversaries remind you every year, and the day before too. "March mein" means 1 March.
- "Dhruv ka birthday kab hai?" / "meri bike ki service kab hai?" / "what did I tell you about my bike?" / "tujhe yaad hai maine kya bola tha bike ke baare mein?" / "what do you know about me?"
- In normal chat Jarvis uses what it remembers when it's relevant: "kuch thanda peene ka mann hai" gets "Cold coffee bana le, tujhe toh pasand hai na!"
- "Forget that" / "bhool ja ki meri bike ki service March mein hai": Jarvis reads the memory back and asks first. A "no" keeps it.
- "Update: Dhruv's birthday is 14 Feb" replaces the old one. Saying a changed fact again also offers to update it.
- "List my memories": how many, plus the latest 5 (the full list is printed). "Export my memories" writes `Documents\Jarvis memories.txt`.
- Never saved: passwords, OTPs, PINs, card and bank numbers, Aadhaar, PAN ("Ye mat bata mujhe yaar, kahin safe jagah rakh").
- Health and sad personal memories are never brought up unless you bring the topic up first.

## Notes and reminders
- "Take a note buy milk" / "note down the meeting is at 5"
- "Read my notes" / "clear my notes" (asks first)
- "Remind me in 10 minutes to drink water" / "remind me at 6 pm to call mom" / "remind me in an hour and 30 minutes to stretch"
- "Set a timer for 5 minutes"
- "What are my reminders" / "cancel my reminders"

Reminders only work while Jarvis is running.

## WhatsApp
- "Send a WhatsApp to mom saying I'll be late"
- "Message Dhruv on WhatsApp that the demo is at 5"
- "WhatsApp mom" (asks what to say)
- "Call mom on WhatsApp" (opens the chat; you tap call)
- "Add contact Riya 98765 43210"

Jarvis always reads the message back and asks before sending.

## Email (Gmail)
Say "connect google" once first (setup steps are in the README).
- "Check my email" / "any new emails": the unread count in your Primary inbox, plus the sender and subject of the latest 5. Bodies are never read out.
- "Read the email from Dhruv": a short summary (an AI summary if `AI_API_KEY` is set, otherwise the first two sentences)
- "Search email for invoice": the top 3 matches
- "Email Dhruv saying the demo is at 5" / "write an email to Riya about the trip": reads the draft back, asks first, then saves a **draft** in Gmail. Jarvis never sends email.
- "Connect google" / "disconnect google" (asks first)

Email addresses come from `data/contacts.json` (`"dhruv": {"phone": "...", "email": "dhruv@example.com"}`) or from people in your recent mail.

## Calendar (Google Calendar)
Times are in IST. Uses the same "connect google".
- "What's on my calendar today" / "tomorrow" / "on Friday" / "my meetings this week"
- "My next meeting" / "when is my next meeting"
- "Add meeting with Dhruv tomorrow at 5 pm for 30 minutes" / "schedule dentist on Monday at 11" (reads it back and asks first; 30 minutes if you don't say how long)
- "Cancel my 5 pm meeting" / "cancel the meeting with Dhruv" (asks first)
- While Google is connected, Jarvis says a reminder and shows a toast 10 minutes before each event.

"At 11" on another day means a working hour: 1 to 7 is PM, 8 to 11 is AM.

## Spotify
Say "connect spotify" once first (setup steps are in the README). Playback control needs Spotify Premium.
- "Play Believer on Spotify" / "play my liked songs" / "play playlist workout" / "play my chill playlist on Spotify"
- "Pause Spotify" / "resume Spotify" / "next song on Spotify" / "previous song on Spotify"
- "What's playing" / "Spotify volume 50"
- If Spotify isn't open, Jarvis opens the app and tries again.
- If Spotify isn't connected, "play X on Spotify" opens a Spotify search, and pause and next use the media keys.
- Plain "next song" and "pause the music" always use the media keys, which work with Spotify too.

## Daily briefing
- "Good morning Jarvis" / "morning briefing" / "brief me": date, weather, AQI and mask advice, first 3 events, unread email, today's reminders, top headline. Under 40 seconds; the full version is printed.
- "Good night Jarvis": tomorrow's first event and weather, tomorrow's reminders, offers a wake-up reminder, sets the volume to 20%.
- Set `BRIEFING_TIME=08:30` in `.env` to get the briefing automatically each morning (once a day, when you've been idle). `BRIEFING_SECTIONS` picks the sections and their order; `BRIEFING_MUSIC` plays something on Spotify afterwards.

## VS Code
- "Open jarvis in VS Code" / "open bogie admin project" / "work on bogie backend"
- "New VS Code window" / "open VS Code here"
- "Open file main dot py in jarvis"
- "List my projects" / "rescan projects"
- "Git status of jarvis"
- "Pull jarvis" (asks first; Jarvis never pushes, commits or resets)
- "Run bogie backend" (asks first, opens a new terminal)

## Personality
Jarvis talks like a close friend by default (casual Hinglish: "Ho gaya!", "Kya bola? Phir se bol na"). Set `PERSONALITY=professional` in `.env` for the polite assistant, and `NICKNAME` for what it calls you.
- "Talk professionally" / "formal mode": polite assistant for this session
- "Be my friend again" / "friend mode": back to friend mode
- "I'm tired" / "I'm bored" / "bore ho rahi hoon" / "I'm stressed" / "I'm so happy": a short, friendly reply (a break, music, a joke)

## General questions
- "What is 25 times 4" / "what is 10 percent of 250" / "what is the square root of 144"
- "Who is Sachin Tendulkar" / "tell me about the Taj Mahal"
- Facts are checked online before Jarvis answers: "who wrote Godaan" / "godaan kisne likha", "capital of Australia" / "australia ki rajdhani kya hai", "when was the Taj Mahal built" / "taj mahal kab bana", "how many states are there in India". The answer comes only from what the search found. If it can't confirm one, it says "Pakka nahi hoon yaar" and offers Google instead of guessing.
- "Tell me a joke" / "who are you" / "how are you" / "thank you"
- Anything else goes to the AI, which answers like a friend: "tu kaisa hai", "mujhe neend nahi aa rahi", "explain recursion simply", "tu kya sochta hai, mujhe MBA karna chahiye?", "kaunsa better hai iPhone ya Pixel"
- Follow-ups: "aur?", "kyun?", "uska kya?", "explain karo" / "detail mein bata" (a longer answer)
- Things it can't do yet get an alternative: "order me a pizza" -> "Swiggy khol doon?", then "haan" opens it (or "nahi"). If nothing closer fits, it offers Google.
- Sensible advice: "mujhe neend nahi aa rahi", "sar dard ho raha hai", "kya main saara paisa crypto mein daal doon?" get safe, practical answers (doctor or CA when it matters). If you're feeling really low, it's there for you, and the free Tele-MANAS helpline is 14416.
- Unclear questions get one short question back ("Kaunsa match? India wala?")
- "Forget our conversation" (clears the AI's memory)
- "Which AI model are you using" (if a model is retired, Jarvis switches to a newer one by itself and tells you once; it also says whether the Gemini backup is in use)
- **Hindi / Hinglish:** talk to the AI in Hinglish and it replies in Hinglish, written in English letters.
- "Switch to Hindi" makes Jarvis listen for Hindi. "Switch to English" switches back. This lasts for the current session only.

## Say it your way
You don't need the exact phrases above. If no command matches, the AI works out the action and Jarvis prints what it understood:

| You say | Jarvis does |
|---|---|
| "yaar thoda sound badha do" / "I can't hear anything" | volume up |
| "ping Dhruv that I'm running late" | WhatsApp Dhruv (asks first) |
| "kal subah 9 baje yaad dilana gym jaana hai" | reminder tomorrow at 9 AM |
| "how's the air outside" / then "what about Gurgaon?" | AQI, then AQI in Gurgaon |
| "is it going to be cold tomorrow" / "and tomorrow?" | tomorrow's weather |
| "put on some chill music" | plays it on YouTube (or Spotify if you say so) |
| "make it 5 minutes instead" (after a timer) | a new 5-minute timer |
| "who won the match yesterday" / "petrol price in delhi today" | searches the web and answers |
| "tell me a fun fact" | just answers |
| "yaar wifi band kar de" / "bluetooth chalu karo" | Wi-Fi off / Bluetooth on (no AI needed) |
| "chrome pe switch kar" / "meri resume khol" / "notepad khol do" | switch to Chrome / open your resume / open Notepad (no AI needed) |
| "laptop band kar do" | shut down (asks first) |
| "chrome window pe le chal" | switch to Chrome |

Anything that sends, deletes, moves, renames, shuts down or closes apps always asks first. Needs `AI_API_KEY` (the "no AI needed" rows work without it).

Every laptop action is logged in `data/logs/actions.log`: "what did you just do" reads it back.

## Hindi commands (after "switch to Hindi")
In Hindi mode you can give commands in natural Hindi. Jarvis rewrites them into English and prints what it understood, for example `(understood as: open chrome)`. Messages, notes and reminder texts are kept exactly as you said them. Anything that isn't a command goes to the AI unchanged, so you can chat in Hindi too.

| Say | Jarvis does |
|---|---|
| "जार्विस, क्रोम खोलो" / "क्रोम और नोटपैड खोल दो" | open Chrome / open Chrome and Notepad |
| "क्रोम बंद करो" | close Chrome |
| "अरिजीत सिंह के गाने बजाओ" | play on YouTube |
| "यूट्यूब पर लोफी म्यूज़िक सर्च करो" / "गूगल पर बेस्ट लैपटॉप सर्च करो" | search YouTube / search the web and answer |
| "आवाज़ बढ़ाओ" / "आवाज़ कम करो" / "म्यूट करो" | volume up / down / mute |
| "अगला गाना" / "गाना रोको" | next song / pause |
| "अभी क्या टाइम है" / "आज की तारीख क्या है" | time / date |
| "दिल्ली का मौसम कैसा है" / "कल बारिश होगी क्या" | weather / rain |
| "रोहिणी में एक्यूआई क्या है" / "प्रदूषण कितना है" / "पीएम 2.5 कितना है" | AQI / PM2.5 |
| "क्या बाहर जाना सुरक्षित है" / "मास्क चाहिए" | is it safe to go out / do I need a mask |
| "10 मिनट में पानी पीने के लिए याद दिलाना" | remind me in 10 minutes |
| "शाम 6 बजे मम्मी को कॉल करने के लिए याद दिलाना" | remind me at 6 pm |
| "नोट करो कि दूध लाना है" / "मेरे नोट्स पढ़ो" | take a note / read notes |
| "माँ को व्हाट्सएप पर मैसेज भेजो कि मैं लेट हूँ" | WhatsApp Mom "मैं लेट हूँ" (asks first) |
| "माँ को व्हाट्सएप पर कॉल करो" | open Mom's chat to call |
| "स्क्रीनशॉट लो" / "बैटरी कितनी है" / "लैपटॉप लॉक करो" | screenshot / battery / lock |
| "लैपटॉप बंद करो" | shut down (asks first) |
| "जार्विस प्रोजेक्ट वीएस कोड में खोलो" / "जार्विस का गिट स्टेटस बताओ" | open in VS Code / git status |
| "मेरे प्रोजेक्ट्स दिखाओ" | list my projects |
| "क्रिकेट की खबरें सुनाओ" / "एक चुटकुला सुनाओ" | news / joke |
| "मदद" / "सो जाओ" / "अलविदा" | help / sleep / goodbye |
| "हाँ" / "नहीं" | yes / no when Jarvis asks to confirm |
| "स्विच टू इंग्लिश" | back to English |
