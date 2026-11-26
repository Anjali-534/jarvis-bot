"""Regex pieces for Hinglish written in English letters ("yaar wifi band kar de", "chrome pe switch kar").

Hindi puts the verb last, so the laptop-control skills add a verb-final pattern next to the English one:
    rf"{LEAD}(.+?){OPEN}$"   ->  "meri resume khol", "zara notepad khol do"
"""

# Fillers in front: "yaar", "zara", "bhai", "please", "jarvis"
LEAD = r"^(?:(?:yaar|yar|zara|jara|bhai|please|plz|jarvis|ek\s+kaam\s+kar|sun)\s+)*"
# "kar", "karo", "kar do", "kar de", "kar dena", "do", "de" (optionally followed by "na", "please", "yaar")
KAR = r"(?:\s+(?:kar|karo|kardo|karde|kar\s+do|kar\s+de|kar\s+dena|kar\s+dijiye|de|do|dena|dijiye))?" \
      r"(?:\s+(?:na|please|plz|yaar|yar|jaldi))*"
OPEN = rf"\s+(?:khol|kholo|khol\s+do|khol\s+de|kholna|khol\s+dena|open){KAR}"
CLOSE = rf"\s+(?:band|bandh|close|bnd){KAR}"
ON = rf"\s+(?:on|chalu|chaalu|start|shuru){KAR}"
OFF = rf"\s+(?:off|band|bandh|bnd){KAR}"
SWITCH = rf"\s+(?:pe|par|pr|mein|me)\s+(?:switch|jao|chalo|le\s+chal|le\s+jao){KAR}"
FIND = rf"\s+(?:dhoondh|dhundh|dhundo|dhoondho|dhoondo|dhund|search|find){KAR}"
MY = r"(?:(?:meri|mera|mere|my|apni|apna|the)\s+)?"
