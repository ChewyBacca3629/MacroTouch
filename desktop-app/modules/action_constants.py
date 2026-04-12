from __future__ import annotations

BTN_ACTIONS = [
    "None",
    "PlayMusic", "Mute",
    "Mute Mic", "Unmute Mic",
    "Next", "Previous",
    "NextProfile", "PreviousProfile",
    "Open URL",
    "Spotify Playlist",
]

BUTTON_ACTIONS = [
    "PlayMusic", "Mute", "Next", "Previous",
    "Mute Mic", "Unmute Mic",
    "Open URL",
    "HTTP Request",
    "Discord Webhook",
    "Weather Widget",
    "Metric Widget",
    "Spotify Playlist",
    "BrightnessUp", "BrightnessDown",
    "OpenApp",
    "Switch Profile",
    "Minimize Window", "Maximize Window",
    "Close Window", "Switch Window",
    "Lock PC", "Sleep PC", "Shutdown PC", "Restart PC",
    "Copy To Clipboard", "Paste Clipboard",
    "Smart Relay 1 Toggle", "Smart Relay 2 Toggle",
    "Smart Relay 3 Toggle", "Smart Relay 4 Toggle",
]

ACTION_ALIASES = {
    "Brightness Up": "BrightnessUp",
    "Brightness Down": "BrightnessDown",
    "Switch Profile": "SwitchProfile",
    "Minimize Window": "MinimizeWindow",
    "Maximize Window": "MaximizeWindow",
    "Close Window": "CloseWindow",
    "Switch Window": "SwitchWindow",
    "SwitchWindow": "SwitchWindow",
    "Lock PC": "LockPC",
    "Sleep PC": "SleepPC",
    "Shutdown PC": "ShutdownPC",
    "Restart PC": "RestartPC",
    "Copy To Clipboard": "CopyToClipboard",
    "Paste Clipboard": "PasteClipboard",
    "Spotify Playlist": "SpotifyPlaylist",
    "Open URL": "OpenURL",
    "HTTP Request": "HTTPRequest",
    "Discord Webhook": "DiscordWebhook",
    "Weather Widget": "WeatherWidget",
    "Metric Widget": "MetricWidget",
    "Mute Mic": "MuteMic",
    "Unmute Mic": "UnmuteMic",
    "Smart Relay 1 Toggle": "SmartRelay1Toggle",
    "Smart Relay 2 Toggle": "SmartRelay2Toggle",
    "Smart Relay 3 Toggle": "SmartRelay3Toggle",
    "Smart Relay 4 Toggle": "SmartRelay4Toggle",
}

KNOB_MODES = [
    "None",
    "Volume",
    "Brightness",
]
