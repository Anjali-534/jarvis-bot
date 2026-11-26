"""Importing a skill module registers its skills. ORDER MATTERS: earlier modules win ties.

gmail, calendar and spotify come before system and web so "search email for invoice" isn't a
Google search and "play X on spotify" isn't YouTube. whatsapp and vscode come before apps so
"open bogie in vs code" isn't caught by the generic "open X" skill. browse comes before system and apps
so "search more about its battery" is a web search and "open the first source" opens the search result.
web_questions (the regex fast path to a web answer: "petrol price today") comes after weather, aqi and
web (news) and before knowledge, so "weather today" stays weather but "who is the current PM" is
searched.

memory ("remember that...", "Dhruv ka birthday kab hai?") comes right after general, before notes ("take a
note" stays a note).

Laptop control:
* history ("undo that") comes before keyboard_control ("undo" = Ctrl+Z).
* media_session ("what's playing" in any app) comes before spotify, which keeps "what's playing on spotify".
* settings_control comes before spotify ("spotify volume 30" sets the app's own volume slider) and before
  windows ("switch to headphones" is audio, "switch to dark mode" is the theme).
* routines comes before keyboard, windows, files and apps; it only takes "start X" when X is a routine.
* keyboard_control comes before files ("find on this page" is Ctrl+F); windows before files ("move this to
  the other screen"); files before apps ("open my resume"); app_index before apps ("close all apps").

facts (who wrote / capital of / when was / how many: checked on the web, never answered from memory) comes before
web_questions, Wikipedia and the AI.

To add an integration, create jarvis/skills/<name>.py with @skill handlers and add it to this list before
"knowledge" and "ai".
"""
# whatsapp sits before gmail because gmail imports it (that's always been the effective order)
ORDER = [
    "general", "memory", "notes", "history", "media_session", "whatsapp", "gmail", "calendar", "settings_control", "spotify",
    "browse", "routines", "keyboard_control", "windows", "files", "system", "web", "vscode", "app_index", "apps",
    "weather", "aqi", "facts", "web_questions", "knowledge", "ai",
    "briefing",  # last: "good morning" falls through from the plain greeting
]

from jarvis.skills import (  # noqa: E402,F401
    general, memory, notes, history, media_session, gmail, calendar, settings_control, spotify, browse, routines,
    keyboard_control, windows, files, system, web, whatsapp, vscode, app_index, apps, weather, aqi, facts,
    web_questions, knowledge, ai, briefing,
)


def _order_skills():
    """Modules import each other (windows uses system), which would register them early: sort by ORDER.
    The sort is stable, so the order inside each module is kept."""
    from jarvis import router

    rank = {f"jarvis.skills.{name}": i for i, name in enumerate(ORDER)}
    router.SKILLS.sort(key=lambda sk: rank.get(sk.module, len(ORDER)))


_order_skills()
