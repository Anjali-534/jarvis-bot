"""Importing a skill module registers its skills. ORDER MATTERS: earlier modules win ties.

gmail, calendar and spotify come before system and web so "search email for invoice" isn't a
Google search and "play X on spotify" isn't YouTube. whatsapp and vscode come before apps so
"open bogie in vs code" isn't caught by the generic "open X" skill. browse comes before system and apps
so "search more about its battery" is a web search and "open the first source" opens the search result.
web_questions (the regex fast path to a web answer: "petrol price today") comes after weather, aqi and
web (news) and before knowledge, so "weather today" stays weather but "who is the current PM" is
searched. To add an integration,
create jarvis/skills/<name>.py with @skill handlers and add it to this list before "knowledge" and "ai".
"""
from jarvis.skills import (  # noqa: F401
    general, notes, gmail, calendar, spotify, browse, system, web, whatsapp, vscode, apps, weather, aqi, web_questions,
    knowledge, ai,
    briefing,  # last: "good morning" falls through from the plain greeting
)
