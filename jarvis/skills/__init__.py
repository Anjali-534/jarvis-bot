"""Importing a skill module registers its skills. ORDER MATTERS: earlier modules win ties.

whatsapp and vscode come before apps so "open bogie in vs code" isn't caught by the
generic "open X" skill. To add an integration, create jarvis/skills/<name>.py with
@skill handlers and add it to this list before "knowledge" and "ai".
"""
from jarvis.skills import (  # noqa: F401
    general, notes, system, web, whatsapp, vscode, apps, weather, aqi, knowledge, ai,
)
