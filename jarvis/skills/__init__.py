"""Importing a skill module registers its skills. ORDER MATTERS: earlier modules win ties.

To add an integration (WhatsApp, VS Code, ...): create jarvis/skills/<name>.py with
@skill handlers and add it to this list before "knowledge" and "ai".
"""
from jarvis.skills import general, notes, system, web, apps, weather, aqi, knowledge, ai  # noqa: F401
