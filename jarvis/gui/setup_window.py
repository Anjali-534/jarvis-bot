"""The first-run setup wizard and the Settings window (customtkinter). Same sections, two layouts:

    python main.py --setup      the wizard (also opens by itself on first run)
    python main.py --settings   the Settings window (the tray menu's "Settings")

Exit code SAVED (10) tells the tray app the settings changed, so it restarts Jarvis to apply them.
"""
import logging
import queue
import threading
from tkinter import filedialog, messagebox

import config
from jarvis import setup_logic as logic

log = logging.getLogger(__name__)

SAVED = 10
PAD = {"padx": 16, "pady": (6, 0)}


def _ctk():
    import customtkinter as ctk

    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    return ctk


# ---------------------------------------------------------------- building blocks
class Section:
    title = ""
    hint = ""

    def __init__(self, app, parent):
        self.app, self.ctk = app, app.ctk
        self.frame = self.ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self.errors = {}
        self.ctk.CTkLabel(self.frame, text=self.title, font=self.ctk.CTkFont(size=20, weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 0))
        if self.hint:
            self.ctk.CTkLabel(self.frame, text=self.hint, wraplength=560, justify="left").pack(anchor="w", **PAD)
        self.build()

    def build(self):
        raise NotImplementedError

    def label(self, text, bold=False):
        font = self.ctk.CTkFont(weight="bold") if bold else None
        self.ctk.CTkLabel(self.frame, text=text, font=font, wraplength=560, justify="left").pack(anchor="w", **PAD)

    def entry(self, key, label, value="", secret=False, placeholder=""):
        self.label(label)
        widget = self.ctk.CTkEntry(self.frame, width=420, show="*" if secret else "", placeholder_text=placeholder)
        if value:
            widget.insert(0, str(value))
        widget.pack(anchor="w", padx=16, pady=(2, 0))
        error = self.ctk.CTkLabel(self.frame, text="", text_color="#d9534f")
        error.pack(anchor="w", padx=16)
        self.errors[key] = error
        return widget

    def choice(self, label, options, value):
        """A dropdown over {key: label}; returns a getter for the chosen key."""
        self.label(label)
        names = list(options.values())
        var = self.ctk.StringVar(value=options.get(value, names[0]))
        self.ctk.CTkOptionMenu(self.frame, values=names, variable=var, width=420).pack(anchor="w", padx=16, pady=2)
        reverse = {v: k for k, v in options.items()}
        return lambda: reverse[var.get()]

    def status(self):
        widget = self.ctk.CTkLabel(self.frame, text="", wraplength=560, justify="left")
        widget.pack(anchor="w", padx=16, pady=(2, 4))
        return widget

    def buttons(self, *pairs):
        row = self.ctk.CTkFrame(self.frame, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=4)
        for text, command in pairs:
            self.ctk.CTkButton(row, text=text, command=command, width=150).pack(side="left", padx=(0, 8))
        return row

    def show_errors(self, errors):
        for key, widget in self.errors.items():
            widget.configure(text=errors.get(key, ""))
        return not errors

    def background(self, job, done):
        """Run job() off the UI thread, then done(result) on it."""
        def run():
            try:
                result = job()
            except Exception as exc:
                log.debug("background job failed", exc_info=True)
                result = exc
            self.app.later(lambda: done(result))  # Tk may only be touched from its own thread

        threading.Thread(target=run, daemon=True).start()

    def collect(self):
        """(settings, errors) for this section."""
        return {}, {}

    def leave(self):
        """Called when the section is hidden (stop the mic meter...)."""


# ---------------------------------------------------------------- 1. you
class ProfileSection(Section):
    title = "About you"
    hint = "So Jarvis knows what to call you, which Hindi verb forms to use, and where you are (weather, air quality)."

    def build(self):
        v = self.app.values
        self.name = self.entry("USER_NAME", "Your name", v.get("USER_NAME", ""))
        self.nickname = self.entry("NICKNAME", "What should Jarvis call you? (optional)", v.get("NICKNAME", ""))
        self.gender = self.choice("Gender (for Hindi grammar: 'tu kaisi hai' / 'tu kaisa hai')", logic.GENDERS,
                                  v.get("USER_GENDER", "neutral"))
        self.city = self.entry("DEFAULT_LOCATION", "City or pincode", v.get("DEFAULT_LOCATION", ""),
                               placeholder="e.g. Pune, or 411001")
        self.found = self.status()
        self.buttons(("Check on the map", self.check_city))
        self.language = self.choice("Language", logic.LANGUAGES, v.get("LANGUAGE", "english"))
        self.checked = {}

    def raw(self):
        return {"USER_NAME": self.name.get(), "NICKNAME": self.nickname.get(), "USER_GENDER": self.gender(),
                "DEFAULT_LOCATION": self.city.get(), "LANGUAGE": self.language()}

    def check_city(self):
        self.found.configure(text="Checking...")
        self.background(lambda: logic.validate_profile({**self.raw(), "USER_NAME": self.raw()["USER_NAME"] or "x"}),
                        self._checked)

    def _checked(self, result):
        if isinstance(result, Exception):
            self.found.configure(text=f"Couldn't check: {result}")
            return
        clean, errors = result
        if "DEFAULT_LOCATION" in errors:
            self.found.configure(text=errors["DEFAULT_LOCATION"])
        elif clean.get("_place_name"):
            self.checked = clean
            self.found.configure(text=f"Found: {clean['_place_name']} ({clean['HOME_LAT']}, {clean['HOME_LON']})")
        else:
            self.found.configure(text="Saved; I'll look it up when you're online.")

    def collect(self):
        raw = self.raw()
        if self.checked.get("DEFAULT_LOCATION") == raw["DEFAULT_LOCATION"].strip():  # already checked
            clean, errors = logic.validate_profile(raw, geocode=lambda _: _Place(self.checked))
        else:
            clean, errors = logic.validate_profile(raw)
        self.show_errors(errors)
        return clean, errors


class _Place:
    def __init__(self, checked):
        self.lat, self.lon, self.name = checked["HOME_LAT"], checked["HOME_LON"], checked["_place_name"]


# ---------------------------------------------------------------- 2. the assistant
class AssistantSection(Section):
    title = "Your assistant"
    hint = "Pick a name, a voice and a personality. Neural voices need internet; offline Windows voices always work."

    def build(self):
        v = self.app.values
        self.name = self.entry("ASSISTANT_NAME", "Assistant name", v.get("ASSISTANT_NAME", "Jarvis"))
        voices = {name: label for name, label, _ in logic.VOICES}
        voices.update(dict(logic.offline_voices()))
        current = (f"offline:{v['VOICE_NAME']}" if v.get("TTS_ENGINE") == "pyttsx3" and v.get("VOICE_NAME")
                   else v.get("TTS_VOICE", "en-IN-PrabhatNeural"))
        self.voice = self.choice("Voice", voices, current)
        self.played = self.status()
        self.buttons(("Play sample", self.play))
        self.personality = self.choice("Personality", logic.PERSONALITIES, v.get("PERSONALITY", "friend"))
        self.wake = self.ctk.CTkSwitch(self.frame, text="Wake word ('Hey Jarvis'): listen hands-free")
        if str(v.get("WAKE_WORD_ENABLED", True)).lower() not in ("false", "0", "no"):
            self.wake.select()
        self.wake.pack(anchor="w", padx=16, pady=8)
        self.label("Without the wake word, press Ctrl+Alt+J to talk.")

    def play(self):
        self.played.configure(text="Playing...")
        name = self.app.values.get("NICKNAME") or self.app.values.get("USER_NAME") or ""
        self.background(lambda: logic.play_sample(self.voice(), name, self.name.get() or "Jarvis"),
                        lambda r: self.played.configure(
                            text="" if not isinstance(r, Exception) else f"Couldn't play it: {r}. Pick an offline "
                                                                        "voice if you're not online."))

    def collect(self):
        clean, errors = logic.validate_assistant({"ASSISTANT_NAME": self.name.get(), "TTS_VOICE": self.voice(),
                                                  "PERSONALITY": self.personality(),
                                                  "WAKE_WORD_ENABLED": bool(self.wake.get())})
        self.show_errors(errors)
        return clean, errors


# ---------------------------------------------------------------- 3. microphone
class MicSection(Section):
    title = "Microphone"
    hint = "Pick your microphone and talk: the bar should move. Then try the speech test."

    def build(self):
        names = ["Windows default"] + _safe(logic.microphones, [])
        current = self.app.values.get("MIC_DEVICE") or "Windows default"
        self.label("Microphone")
        self.device = self.ctk.StringVar(value=current if current in names else "Windows default")
        self.ctk.CTkOptionMenu(self.frame, values=names, variable=self.device, width=420,
                               command=lambda _: self.restart()).pack(anchor="w", padx=16, pady=2)
        self.meter = self.ctk.CTkProgressBar(self.frame, width=420)
        self.meter.set(0)
        self.meter.pack(anchor="w", padx=16, pady=10)
        self.result = self.status()
        self.buttons(("Test: say 'hello'", self.speech_test))
        self.mic, self.running = None, False

    def chosen(self):
        return "" if self.device.get() == "Windows default" else self.device.get()

    def restart(self):
        self.leave()
        try:
            self.mic = logic.MicTest(self.chosen()).open()
        except Exception as exc:
            self.result.configure(text=f"Couldn't open that microphone: {exc}")
            return
        self.running = True
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def _meter_loop(self):
        while self.running and self.mic is not None:
            try:
                value = self.mic.read_level()
            except Exception:
                break
            self.app.later(lambda v=value: self.meter.set(v))

    def show(self):
        self.restart()

    def leave(self):
        self.running = False
        if self.mic is not None:
            mic, self.mic = self.mic, None
            threading.Timer(0.2, mic.close).start()

    def speech_test(self):
        if self.mic is None:
            self.restart()
        if self.mic is None:
            return
        self.result.configure(text="Listening for 3 seconds... say 'hello'")
        self.running = False
        mic = self.mic

        def job():
            pcm = mic.record(3.0)
            return logic.transcribe(pcm, config.SPEECH_LANGUAGE)

        def done(text):
            if isinstance(text, Exception):
                self.result.configure(text=f"The test failed: {text}")
            else:
                self.result.configure(text=f"I heard: \"{text}\"" if text else
                                      "I didn't catch anything. Move closer, or pick another microphone.")
            self.running = True
            threading.Thread(target=self._meter_loop, daemon=True).start()

        self.background(job, done)

    def collect(self):
        return {"MIC_DEVICE": self.chosen()}, {}


# ---------------------------------------------------------------- 4. AI
class AISection(Section):
    title = "AI brain (optional)"
    hint = ("With a free AI key Jarvis can chat and answer anything. Without one, everything else still works: "
            "apps, files, settings, reminders, timers, routines, maths, weather, air quality and web search.")

    def build(self):
        v = self.app.values
        options = {k: p["label"] for k, p in logic.AI_PROVIDERS.items()}
        options["skip"] = "Skip for now"
        current = next((k for k, p in logic.AI_PROVIDERS.items()
                        if p["settings"]["AI_BASE_URL"] == v.get("AI_BASE_URL")), "groq")
        self.provider = self.choice("Provider", options, current if v.get("_has_AI_API_KEY") else "groq")
        mask = v.get("_mask_AI_API_KEY")
        self.key = self.entry("AI_API_KEY", "API key", secret=True,
                              placeholder=f"saved ({mask}); leave empty to keep it" if mask else "paste your key")
        self.result = self.status()
        self.buttons(("Get a free key", lambda: logic.open_page(logic.AI_PROVIDERS.get(
            self.provider(), logic.AI_PROVIDERS["groq"])["key_url"])), ("Test key", self.test))

    def test(self):
        self.result.configure(text="Testing...")
        self.background(lambda: logic.test_ai_key(self.provider(), self.key.get()),
                        lambda r: self.result.configure(text=r[1] if isinstance(r, tuple) else str(r)))

    def collect(self):
        provider, key = self.provider(), self.key.get().strip()
        if provider == "skip":
            return {}, {}
        if not key and self.app.values.get("_has_AI_API_KEY"):
            return dict(logic.AI_PROVIDERS[provider]["settings"]), {}  # keep the saved key
        try:
            return logic.ai_settings(provider, key), {}
        except logic.Problem as exc:
            self.show_errors({"AI_API_KEY": str(exc)})
            return {}, {"AI_API_KEY": str(exc)}


# ---------------------------------------------------------------- 5. optional connections
class ConnectionsSection(Section):
    title = "Optional connections"
    hint = "Each one is optional; skip any. You can add them later in Settings."

    def build(self):
        v = self.app.values
        self.fields = {}
        self._secret_field("TELEGRAM_BOT_TOKEN", "Telegram phone remote: bot token from @BotFather", "telegram",
                           logic.test_telegram_token, v)
        self._secret_field("WAQI_TOKEN", "Air quality from real monitoring stations: WAQI token", "waqi",
                           logic.test_waqi_token, v)
        self._secret_field("TAVILY_API_KEY", "Better web search: Tavily API key", "tavily", logic.test_tavily_key, v)
        self.label("Advanced (needs a free developer account)", bold=True)
        self.label("Google (Gmail + Calendar): in Google Cloud Console create an OAuth client of type 'Desktop app', "
                   "enable the Gmail and Calendar APIs, download the JSON, then choose it here.")
        self.google = self.status()
        self.buttons(("Open Google Cloud", lambda: logic.open_page(logic.KEY_PAGES["google"])),
                     ("Choose JSON file...", self.pick_google))
        self.label("Spotify: create an app at developer.spotify.com with the redirect URI "
                   f"{config.SPOTIFY_REDIRECT_URI}, then paste its Client ID.")
        self.spotify = self.entry("SPOTIFY_CLIENT_ID", "Spotify Client ID", v.get("SPOTIFY_CLIENT_ID", ""))
        self.spotify_result = self.status()
        self.buttons(("Open Spotify dashboard", lambda: logic.open_page(logic.KEY_PAGES["spotify"])),
                     ("Check", lambda: self.spotify_result.configure(
                         text=logic.validate_spotify_client_id(self.spotify.get())[1])))

    def _secret_field(self, key, label, page, test, values):
        mask = values.get(f"_mask_{key}")
        widget = self.entry(key, label, secret=True,
                            placeholder=f"saved ({mask}); leave empty to keep it" if mask else "optional")
        result = self.status()

        def run_test():
            result.configure(text="Testing...")
            self.background(lambda: test(widget.get()), lambda r: result.configure(
                text=r[1] if isinstance(r, tuple) else str(r)))

        self.buttons(("Get one", lambda: logic.open_page(logic.KEY_PAGES[page])), ("Test", run_test))
        self.fields[key] = widget

    def pick_google(self):
        path = filedialog.askopenfilename(title="Google OAuth client JSON", filetypes=[("JSON", "*.json")])
        if path:
            self.google.configure(text=logic.import_google_credentials(path)[1])

    def collect(self):
        clean = {k: w.get().strip() for k, w in self.fields.items() if w.get().strip()}
        spotify = self.spotify.get().strip()
        if spotify:
            ok, message = logic.validate_spotify_client_id(spotify)
            if not ok:
                self.show_errors({"SPOTIFY_CLIENT_ID": message})
                return {}, {"SPOTIFY_CLIENT_ID": message}
            clean["SPOTIFY_CLIENT_ID"] = spotify
        self.show_errors({})
        return clean, {}


# ---------------------------------------------------------------- 6. start with Windows (+ Settings extras)
class StartupSection(Section):
    title = "Start with Windows"
    hint = "Jarvis can start in the tray (bottom-right, by the clock) every time you log in."

    def build(self):
        from jarvis import startup

        self.switch = self.ctk.CTkSwitch(self.frame, text="Start Jarvis when I log in")
        if _safe(startup.is_installed, False) or (self.app.wizard and self.app.values.get("START_WITH_WINDOWS", True)):
            self.switch.select()
        self.switch.pack(anchor="w", padx=16, pady=10)
        if not self.app.wizard:
            self.label("Your data", bold=True)
            self.label(f"Everything Jarvis keeps is in {config.DATA_DIR}. API keys are in the Windows Credential "
                       "Manager.")
            self.buttons(("Open data folder", logic.open_data_folder), ("Reset everything...", self.reset))

    def reset(self):
        if not messagebox.askyesno("Reset everything?",
                                   "This deletes all your settings, memories, notes, routines, contacts, sign-ins and "
                                   "API keys. It can't be undone.\n\nReset everything?", icon="warning"):
            return
        removed = logic.reset_everything()
        messagebox.showinfo("Done", f"Removed {removed} items. Jarvis will run the setup again next time.")
        self.app.close(SAVED)

    def collect(self):
        return {"START_WITH_WINDOWS": bool(self.switch.get())}, {}


SECTIONS = [ProfileSection, AssistantSection, MicSection, AISection, ConnectionsSection, StartupSection]
TAB_NAMES = ["You", "Assistant", "Microphone", "AI brain", "Connections", "Startup & data"]


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        log.debug("setup helper failed", exc_info=True)
        return default


# ---------------------------------------------------------------- the two windows
class SetupApp:
    def __init__(self, wizard=True):
        self.ctk = _ctk()
        self.wizard = wizard
        self.values = logic.current_values()
        self.exit_code = 0
        self.root = self.ctk.CTk()
        self.root.title(f"{config.ASSISTANT_NAME} setup" if wizard else f"{config.ASSISTANT_NAME} settings")
        self.root.geometry("640x640")
        icon = config.BASE_DIR / "packaging" / "jarvis.ico"
        if icon.exists():
            _safe(lambda: self.root.iconbitmap(str(icon)), None)
        self.root.protocol("WM_DELETE_WINDOW", lambda: self.close(self.exit_code))
        self.queue = queue.Queue()
        self.root.after(50, self._drain)
        self.sections = []
        if wizard:
            self._build_wizard()
        else:
            self._build_tabs()

    def _build_wizard(self):
        self.body = self.ctk.CTkFrame(self.root, fg_color="transparent")
        self.body.pack(fill="both", expand=True)
        self.sections = [cls(self, self.body) for cls in SECTIONS]
        bar = self.ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=12)
        self.step_label = self.ctk.CTkLabel(bar, text="")
        self.step_label.pack(side="left")
        self.next_button = self.ctk.CTkButton(bar, text="Next", command=self.next)
        self.next_button.pack(side="right")
        self.back_button = self.ctk.CTkButton(bar, text="Back", command=self.back, fg_color="gray")
        self.back_button.pack(side="right", padx=8)
        self.step = 0
        self.show(0)

    def show(self, index):
        for section in self.sections:
            section.frame.pack_forget()
            section.leave()
        self.step = index
        section = self.sections[index]
        section.frame.pack(fill="both", expand=True)
        getattr(section, "show", lambda: None)()
        self.step_label.configure(text=f"Step {index + 1} of {len(self.sections)}")
        self.back_button.configure(state="normal" if index else "disabled")
        self.next_button.configure(text="Finish" if index == len(self.sections) - 1 else "Next")

    def next(self):
        clean, errors = self.sections[self.step].collect()
        if errors:
            return
        self.values.update(clean)
        if self.step < len(self.sections) - 1:
            self.show(self.step + 1)
            return
        logic.finish({k: v for k, v in self.values.items() if not k.startswith("_") or k == "START_WITH_WINDOWS"})
        messagebox.showinfo("All set!", f"{self.values.get('ASSISTANT_NAME', 'Jarvis')} is ready. Say "
                            "'Hey Jarvis, what can you do?' or press Ctrl+Alt+J.")
        self.close(SAVED)

    def back(self):
        if self.step:
            self.show(self.step - 1)

    def _build_tabs(self):
        tabs = self.ctk.CTkTabview(self.root)
        tabs.pack(fill="both", expand=True, padx=8, pady=8)
        for name, cls in zip(TAB_NAMES, SECTIONS):
            tab = tabs.add(name)
            section = cls(self, tab)
            section.frame.pack(fill="both", expand=True)
            self.sections.append(section)
        tabs.configure(command=lambda: self._tab_changed(tabs))
        bar = self.ctk.CTkFrame(self.root, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 12))
        self.ctk.CTkButton(bar, text="Save", command=self.save).pack(side="right")
        self.ctk.CTkButton(bar, text="Cancel", fg_color="gray", command=lambda: self.close(0)).pack(side="right",
                                                                                                     padx=8)

    def _tab_changed(self, tabs):
        for name, section in zip(TAB_NAMES, self.sections):
            if name == tabs.get():
                getattr(section, "show", lambda: None)()
            else:
                section.leave()

    def save(self):
        merged = {}
        for name, section in zip(TAB_NAMES, self.sections):
            clean, errors = section.collect()
            if errors:
                messagebox.showwarning("Check the settings", f"Please fix the {name} tab first.")
                return
            merged.update(clean)
        logic.save(merged)
        messagebox.showinfo("Saved", f"Saved. {config.ASSISTANT_NAME} restarts to use the new settings.")
        self.close(SAVED)

    def later(self, fn):
        """Run fn on the Tk thread (worker threads call this instead of touching widgets)."""
        self.queue.put(fn)

    def _drain(self):
        try:
            while True:
                fn = self.queue.get_nowait()
                try:
                    fn()
                except Exception:
                    log.debug("UI update failed", exc_info=True)
        except queue.Empty:
            pass
        try:
            self.root.after(50, self._drain)
        except Exception:
            pass  # the window is closing

    def close(self, code):
        for section in self.sections:
            section.leave()
        self.exit_code = code
        self.root.after(250, self.root.destroy)

    def run(self):
        self.root.mainloop()
        return self.exit_code


def run_wizard():
    return SetupApp(wizard=True).run()


def run_settings():
    return SetupApp(wizard=False).run()
