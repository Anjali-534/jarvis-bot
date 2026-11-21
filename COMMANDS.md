# Jarvis commands cheat sheet

## Start and stop

| Command | What it does |
|---|---|
| `python main.py` | Voice mode. Say "Jarvis" before each command. |
| `python main.py --no-wake` | Voice mode without the wake word. Every phrase is a command. |
| `python main.py --text` | Type commands instead of speaking. No wake word needed. |
| `python main.py --mute` | Print replies instead of speaking them. Combine with the others, e.g. `--text --mute`. |

**Wake word:** "Jarvis", "Hey Jarvis" or "OK Jarvis", at the start or end of the sentence: "Jarvis, what's the AQI?" / "What's the weather, Jarvis?"
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
- "Open GitHub" / "open Gmail" / "open github.com" / "open example dot com"
- "Open Figma" (anything unknown is searched in the Start menu)
- "Close Chrome" / "quit Spotify" / "close this window" / "close this tab"

## Laptop controls
- "Volume up" / "volume up by 20" / "turn down the volume" / "mute"
- "Pause the music" / "resume" / "next song" / "previous song"
- "Brightness 70" / "brightness up" / "dim the screen"
- "Take a screenshot" (saved to Pictures\Jarvis Screenshots)
- "Battery status" / "system status" / "CPU usage"
- "Lock the laptop"
- "Shut down the laptop" / "restart in 10 minutes" / "turn off my computer" (always asks first)
- "Cancel shutdown"
- "Put the laptop to sleep" (asks first)
- "Start with Windows" / "don't start with Windows" (asks first)

## Web
- "Play Arijit Singh songs" / "play lofi beats on YouTube"
- "Search lofi music on YouTube"
- "Search best laptops under 60000" / "google weather in Mumbai" / "look up Python decorators"
- "News" / "news about cricket" / "tech news"

## Notes and reminders
- "Take a note buy milk" / "remember that the meeting is at 5"
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

## VS Code
- "Open jarvis in VS Code" / "open bogie admin project" / "work on bogie backend"
- "New VS Code window" / "open VS Code here"
- "Open file main dot py in jarvis"
- "List my projects" / "rescan projects"
- "Git status of jarvis"
- "Pull jarvis" (asks first; Jarvis never pushes, commits or resets)
- "Run bogie backend" (asks first, opens a new terminal)

## General questions
- "What is 25 times 4" / "what is 10 percent of 250" / "what is the square root of 144"
- "Who is Sachin Tendulkar" / "tell me about the Taj Mahal"
- "Tell me a joke" / "who are you" / "how are you" / "thank you"
- Anything else goes to the AI: "do you know Hindi", "write a 2 line poem about Delhi winters"
- "Forget our conversation" (clears the AI's memory)
- **Hindi / Hinglish:** talk to the AI in Hinglish and it replies in Hinglish, written in English letters so the voice can read it.
- "Switch to Hindi" makes Jarvis listen for Hindi. "Switch to English" switches back. This lasts for the current session only.

## Hindi commands (after "switch to Hindi")
In Hindi mode you can give commands in natural Hindi. Jarvis rewrites them into English and prints what it understood, for example `(understood as: open chrome)`. Messages, notes and reminder texts are kept exactly as you said them. Anything that isn't a command goes to the AI unchanged, so you can chat in Hindi too.

| Say | Jarvis does |
|---|---|
| "जार्विस, क्रोम खोलो" / "क्रोम और नोटपैड खोल दो" | open Chrome / open Chrome and Notepad |
| "क्रोम बंद करो" | close Chrome |
| "अरिजीत सिंह के गाने बजाओ" | play on YouTube |
| "यूट्यूब पर लोफी म्यूज़िक सर्च करो" / "गूगल पर बेस्ट लैपटॉप सर्च करो" | search YouTube / Google |
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
