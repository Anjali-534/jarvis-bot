import speech_recognition as sr
import pyttsx3

# Initialize TTS engine
engine = pyttsx3.init()
engine.setProperty('rate', 150)
engine.setProperty('volume', 1.0)
voices = engine.getProperty('voices')
engine.setProperty('voice', voices[1].id)  # female voice

def speak(text):
    print(f"Bot: {text}")
    engine.say(text)
    engine.runAndWait()

def listen():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        recognizer.adjust_for_ambient_noise(source)
        audio = recognizer.listen(source)
    try:
        command = recognizer.recognize_google(audio)
        print(f"You said: {command}")
        return command.lower()
    except sr.UnknownValueError:
        speak("Sorry, I didn't catch that. Please say it again.")
        return ""
    except sr.RequestError:
        speak("Speech service is down.")
        return ""

def run_bot():
    speak("Hey! I am here to give you AQI and pollution info.")
    speak("Please tell me your area or pincode.")

    while True:
        command = listen()

        if 'pm2.5' in command:
            speak("The PM2.5 in your area is XX micrograms per cubic meter, AQI Zone: Green, Yellow, or Red")

        elif 'no2' in command:
            speak("The NO2 in your area is XX micrograms per cubic meter, AQI Zone: Green, Yellow, or Red")

        elif 'safe' in command or 'pollution safe' in command:
            speak("Today's AQI in your area is Green, Yellow, or Red. Advice: Stay indoors, Mask recommended, or Safe")

        elif 'red zones' in command:
            speak("Today's Red AQI zones are: Area1, Area2, Area3")

        elif 'help' in command:
            speak("You can say: PM2.5, NO2, Safe, Red Zones, or Help")

        elif 'exit' in command or 'quit' in command:
            speak("Goodbye! Stay safe!")
            break

        else:
            speak("Sorry, I didn't get your location. Please say your area or pincode again.")

if __name__ == "__main__":
    run_bot()
