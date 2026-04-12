# MacroTouch  
   
 MacroTouch is an open-source touchscreen macro controller built with an ESP32-S3 and a cross-platform Python desktop configuration application.  
   
 The system allows users to create customizable button layouts, control media, monitor system performance, and manage application audio volumes using a dedicated hardware device with a touchscreen and rotary encoder.  
   
 ---  
   
 # Features  
   
 ## Hardware  
   
 - **ESP32-S3 Microcontroller** – Dual-core processor with WiFi and Bluetooth  
 - **3.5" ILI9488 TFT Touchscreen** – 480×320 resolution display  
 - **XPT2046 Touch Controller**  
 - **Rotary Encoder with Push Button** – Navigation and value adjustment  
 - **USB-C Interface** – Power, programming, and communication  
   
 ## Software  
   
 - **Cross-platform Desktop App** (Python + PyQt6)  
 - **Multiple Profile Modes**  
   
 ### Grid Mode  
 Customizable button grid layouts for macros and commands.  
   
 ### Media Mode  
 Music player controls with album artwork display.  
   
 ### Monitor Mode  
 Real-time system performance monitoring.  
   
 ### Mixer Mode  
 Control audio levels of running applications.  
   
 Other capabilities:  
   
 - Real-time serial communication with the device  
 - Profile management (save, load, switch)  
 - Configuration upload from desktop app to device  
 - Firmware flashing via USB  
   
 ---  
   
 # Hardware Requirements  
   
 - ESP32-S3 development board  
 - ILI9488 TFT touchscreen display  
 - XPT2046 touch controller  
 - Rotary encoder with push button  
 - USB-C cable for power and programming  
   
 ---  
   
 # Software Requirements  
   
 - Python **3.8+**  
 - PyQt6  
 - pyserial  
 - Pillow  
 - psutil  
 - requests  
   
 ---  
   
 # Project Architecture  
MacroTouch/  
├── firmware/           # ESP32-S3 Arduino firmware  
├── desktop-app/        # Python desktop configurator  
│   ├── main.py         # Application entry point  
│   ├── modules/        # Application logic  
│   ├── ui/             # Qt Designer UI files  
│   └── assets/         # Icons and images  
├── images/             # Screenshots and device photos  
├── example-config/     # Sample configuration files  
└── docs/               # Documentation  
 ---  
   
 # Installation  
   
 ## Desktop Application  
   
 Clone the repository:  
git clone [https://github.com/yourusername/MacroTouch.git](https://github.com/yourusername/MacroTouch.git "https://github.com/yourusername/MacroTouch.git")  
  cd MacroTouch  
 Create virtual environment:  
python -m venv .venv  
 Activate environment  
   
 Linux / macOS  
source .venv/bin/activate  
 Windows  
.venv\Scripts\activate  
 Install dependencies:  
pip install -r requirements.txt  
 Run the application:  
python desktop-app/main.py  
 ---  
   
 # Firmware  
   
 1. Install **Arduino IDE** with ESP32 board support    
 2. Open:  
firmware/MacroTouch.ino  
 3. Select board:  
ESP32S3 Dev Module  
 4. Install required libraries:  
   
 - LovyanGFX    
 - ArduinoJson  
   
 5. Upload firmware to your ESP32-S3 device  
   
 ---  
   
 # Usage  
   
 ## Desktop Application  
   
 1. Launch the application  
 2. Connect the MacroTouch device via USB  
 3. Create or load a configuration profile  
 4. Configure button actions and layouts  
 5. Upload the configuration to the device  
   
 ## Device Controls  
   
 | Control | Function |  
 |------|------|  
 Touchscreen | Tap buttons to execute actions |  
 Rotary Encoder | Rotate to navigate |  
 Encoder Press | Confirm selection |  
 USB Connection | Power + communication |  
   
 ---  
   
 # Configuration  
   
 Device behaviour is defined using JSON profiles.  
   
 Example:  
{  
  "rows": 3,  
  "cols": 4,  
  "mode": "grid",  
  "btnA_action": "None",  
  "btnB_action": "None",  
  "pot_action": "None"  
  }  
 Example profiles are available in:  
example-config/  
 ---  
   
 # Screenshots  
   
 Add screenshots of:  
   
 - Desktop configuration app  
 - Hardware device  
 - UI running on the screen  
   
 Example:  
images/app.png  
  images/device.jpg  
 ---  
   
 # Development  
   
 Run tests:  
python -m pytest tests/  
 ### Code Style  
   
 - Follow **PEP8**  
 - Use **type hints**  
 - Maximum line length **100 characters**  
   
 ---  
   
 # Contributing  
   
 Contributions are welcome.  
   
 Please read **CONTRIBUTING.md** for development guidelines and pull request process.  
   
 ---  
   
 # License  
   
 This project is licensed under the **MIT License**.  
   
 See the `LICENSE` file for details.  
   
 ---  
   
 # Acknowledgments  
   
 - ESP32 Arduino Core  
 - LovyanGFX  
 - PyQt6  
 - Qt Designer  
-    
