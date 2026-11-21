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
