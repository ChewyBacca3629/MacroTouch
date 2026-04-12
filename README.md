# MacroTouch

ESP32-powered touchscreen macro deck that controls your PC — a DIY alternative to Elgato Stream Deck.

MacroTouch is an open-source hardware + software system that turns an ESP32-S3 touchscreen device into a fully customizable macro controller for your computer.

It allows you to create profiles, assign actions, control media, manage applications, adjust system volume, and monitor system performance — all from a dedicated physical touchscreen interface.

---

# 🚀 Key Features

## 🧠 Core Concept
- DIY Stream Deck alternative based on ESP32-S3
- Fully customizable touchscreen interface
- Multi-profile system for different workflows (gaming, work, media)

## 🖥️ Desktop Application
- Cross-platform configuration tool (Python + PyQt6)
- Drag-and-drop button layout editor
- Profile management (create, save, switch, import/export)
- Real-time USB serial communication with device
- Firmware upload from app

## 📱 Device Capabilities
- Touchscreen macro buttons
- Rotary encoder navigation and control
- Real-time action execution on PC
- Profile switching directly on device

---

# 🔧 Supported Modes

## Grid Mode
Custom macro button layouts for shortcuts, apps, and automation.

## Media Mode
Control music playback with visual feedback and album display.

## Monitor Mode
Real-time system performance monitoring (CPU, RAM, etc.).

## Mixer Mode
Adjust per-application audio levels.

---

# 🧩 Hardware

- ESP32-S3 microcontroller (WiFi + Bluetooth)
- 3.5" ILI9488 TFT touchscreen (480×320)
- XPT2046 touch controller
- Rotary encoder
- USB-C for power, flashing, and communication

---

# 💻 Software Stack

- Python 3.8+
- PyQt6 (desktop configurator)
- Arduino framework (ESP32 firmware)
- pyserial
- psutil
- Pillow
- requests

---

# 🏗️ Project Structure

MacroTouch/
├── firmware/           # ESP32-S3 Arduino firmware
├── desktop-app/        # Python configuration tool
│   ├── main.py
│   ├── modules/
│   ├── ui/
│   └── assets/
├── images/             # Screenshots & device photos
├── example-config/     # Example profiles (JSON)
└── docs/               # Documentation

---

# 📟 Firmware Setup

1. Install Arduino IDE with ESP32 support  
2. Open firmware/MacroTouch.ino  
3. Select board: ESP32S3 Dev Module  
4. Install dependencies:
   - LovyanGFX
   - ArduinoJson  
5. Upload firmware to device

---

# 🎮 Usage

1. Connect device via USB  
2. Launch desktop app  
3. Create or load profile  
4. Assign actions to buttons  
5. Upload configuration to device  

---

# 📦 Configuration

MacroTouch uses JSON-based profiles:

{
  "rows": 3,
  "cols": 4,
  "mode": "grid",
  "profiles": ["gaming", "work", "media"]
}

Example configs are in:
example-config/

---

# 📄 License

MIT License — see LICENSE file.
