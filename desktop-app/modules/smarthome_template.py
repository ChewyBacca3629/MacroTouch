SMART_HOME_TEMPLATE = r'''
#include <Arduino.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <NTPClient.h>
#include <WebServer.h>

// ============= Pomocná funkcia RGB =============
uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

// ============= LGFX SETUP (ILI9341 + XPT2046) =============

class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9341  _panel_instance;
  lgfx::Bus_SPI        _bus_instance;
  lgfx::Touch_XPT2046  _touch_instance;

public:
  LGFX() {
    auto panel = &_panel_instance;
    auto bus   = &_bus_instance;
    auto touch = &_touch_instance;

    {
      auto cfg = bus->config();
      cfg.spi_host   = SPI3_HOST;
      cfg.spi_mode   = 0;
      cfg.freq_write = 40000000;
      cfg.freq_read  = 16000000;
      cfg.spi_3wire  = false;
      cfg.use_lock   = true;
      cfg.dma_channel = 1;
      cfg.pin_sclk = 12;
      cfg.pin_mosi = 11;
      cfg.pin_miso = 15;
      cfg.pin_dc   = 7;
      bus->config(cfg);
      panel->setBus(bus);
    }

    {
      auto cfg = panel->config();
      cfg.pin_cs   = 10;
      cfg.pin_rst  = -1;
      cfg.pin_busy = -1;
      cfg.panel_width   = 240;
      cfg.panel_height  = 320;
      cfg.memory_width  = 240;
      cfg.memory_height = 320;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.readable   = true;
      cfg.invert     = false;
      cfg.rgb_order  = false;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      panel->config(cfg);
    }

    {
      auto cfg = touch->config();
      cfg.spi_host = SPI2_HOST;
      cfg.freq     = 1000000;
      cfg.pin_sclk = 13;
      cfg.pin_mosi = 6;
      cfg.pin_miso = 5;
      cfg.pin_cs   = 14;
      cfg.pin_int  = 4;
      cfg.x_min = 200;
      cfg.x_max = 3800;
      cfg.y_min = 200;
      cfg.y_max = 3800;
      cfg.bus_shared = false;
      touch->config(cfg);
      panel->setTouch(touch);
    }

    setPanel(panel);
  }
};

LGFX lcd;

// ================== RELÉ PINS ==================

const int RELAY1_PIN = 16;
const int RELAY2_PIN = 17;
const int RELAY3_PIN = 18;
const int RELAY4_PIN = 21;

bool relayState[4] = {false, false, false, false};

String relayLabels[4] = {
  "__R1__",
  "__R2__",
  "__R3__",
  "__R4__"
};

void setupRelayPins() {
  pinMode(RELAY1_PIN, OUTPUT);
  pinMode(RELAY2_PIN, OUTPUT);
  pinMode(RELAY3_PIN, OUTPUT);
  pinMode(RELAY4_PIN, OUTPUT);

  // Relé modul aktívny v LOW – HIGH = vypnuté
  digitalWrite(RELAY1_PIN, HIGH);
  digitalWrite(RELAY2_PIN, HIGH);
  digitalWrite(RELAY3_PIN, HIGH);
  digitalWrite(RELAY4_PIN, HIGH);
}

void applyRelayStates() {
  digitalWrite(RELAY1_PIN, relayState[0] ? LOW : HIGH);
  digitalWrite(RELAY2_PIN, relayState[1] ? LOW : HIGH);
  digitalWrite(RELAY3_PIN, relayState[2] ? LOW : HIGH);
  digitalWrite(RELAY4_PIN, relayState[3] ? LOW : HIGH);
}

// ================== WiFi + NTP + HTTP ==================

const char* WIFI_SSID = "__WIFI_SSID__";
const char* WIFI_PASS = "__WIFI_PASS__";

WiFiUDP ntpUDP;
NTPClient timeClient(ntpUDP, "pool.ntp.org", 3600, 60000);

unsigned long lastTimeUpdate = 0;

WebServer server(80);

// Blokujúce pripojenie s retry + resetom
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);         // rýchlejšie reakčné časy
  WiFi.setAutoReconnect(true);  // automatický reconnect
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  int attempts = 0;
  Serial.print("Connecting to WiFi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    attempts++;

    if (attempts >= 40) {  // ~20 sekúnd
      Serial.println("\nWiFi failed, restarting...");
      ESP.restart();
    }
  }

  Serial.print("\nConnected! IP address: ");
  Serial.println(WiFi.localIP());
}

// ---- UI / tiles ----

struct Tile {
  int x, y, w, h;
  int relayIndex;
};

const int SCREEN_W = 320;
const int SCREEN_H = 240;
const int STATUS_BAR_H = 38;

Tile tiles[4];

uint16_t COLOR_BG        = rgb565(11,12,18);
uint16_t COLOR_PANEL     = rgb565(25,29,40);
uint16_t COLOR_TILE_OFF  = rgb565(30,41,59);
uint16_t COLOR_TILE_ON   = rgb565(34,197,94);
uint16_t COLOR_TILE_ON_D = rgb565(21,128,61);
uint16_t COLOR_ACCENT    = rgb565(96,165,250);
uint16_t COLOR_TEXT      = TFT_WHITE;

// ---- HTTP API ----

void drawTile(int idx);  // forward deklarácia, aby ju vedel volať HTTP handler

void handleRoot() {
  server.send(200, "text/plain", "SmartHome ESP OK");
}

void handleState() {
  String json = "{";
  json += "\"wifi_status\":\"";
  json += (WiFi.status() == WL_CONNECTED ? "connected" : "disconnected");
  json += "\",\"ip\":\"";
  if (WiFi.status() == WL_CONNECTED) {
    json += WiFi.localIP().toString();
  } else {
    json += "0.0.0.0";
  }
  json += "\",\"relays\":[";
  for (int i = 0; i < 4; ++i) {
    json += relayState[i] ? "1" : "0";
    if (i < 3) json += ",";
  }
  json += "],\"labels\":[";
  for (int i = 0; i < 4; ++i) {
    json += "\"";
    json += relayLabels[i];
    json += "\"";
    if (i < 3) json += ",";
  }
  json += "]}";

  server.send(200, "application/json", json);
}

// /toggle?ch=1 – rýchly toggle + okamžité prekreslenie na displeji
void handleToggle() {
  if (!server.hasArg("ch")) {
    server.send(400, "text/plain", "Missing ch");
    return;
  }
  int ch = server.arg("ch").toInt();
  if (ch < 1 || ch > 4) {
    server.send(400, "text/plain", "ch must be 1..4");
    return;
  }
  int idx = ch - 1;
  relayState[idx] = !relayState[idx];
  applyRelayStates();
  drawTile(idx);  // UI update okamžite

  String json = "{\"ch\":";
  json += ch;
  json += ",\"state\":";
  json += (relayState[idx] ? "1" : "0");
  json += "}";
  server.send(200, "application/json", json);
}

// /set?ch=1&state=on/off/1/0 – explicitný ON/OFF
void handleSetRelay() {
  if (!server.hasArg("ch") || !server.hasArg("state")) {
    server.send(400, "text/plain", "Missing ch or state");
    return;
  }

  int ch = server.arg("ch").toInt();
  if (ch < 1 || ch > 4) {
    server.send(400, "text/plain", "ch must be 1..4");
    return;
  }
  int idx = ch - 1;

  String s = server.arg("state");
  s.toLowerCase();
  bool on = (s == "1" || s == "on" || s == "true");

  relayState[idx] = on;
  applyRelayStates();
  drawTile(idx);  // UI refresh

  String json = "{\"ch\":";
  json += ch;
  json += ",\"state\":";
  json += (relayState[idx] ? "1" : "0");
  json += "}";
  server.send(200, "application/json", json);
}

void setupHttpServer() {
  server.on("/",      HTTP_GET, handleRoot);
  server.on("/state", HTTP_GET, handleState);
  server.on("/toggle",HTTP_GET, handleToggle);
  server.on("/set",   HTTP_GET, handleSetRelay);
  server.begin();
  Serial.println("HTTP server bezi na porte 80");
}

// ---- UI ----

void setupTiles() {
  int bodyH = SCREEN_H - STATUS_BAR_H - 6;
  int tileW = (SCREEN_W - 3*8) / 2;
  int tileH = (bodyH - 3*8) / 2;

  int x1 = 8;
  int x2 = x1 + tileW + 8;
  int y1 = STATUS_BAR_H + 8;
  int y2 = y1 + tileH + 8;

  tiles[0] = {x1, y1, tileW, tileH, 0};
  tiles[1] = {x2, y1, tileW, tileH, 1};
  tiles[2] = {x1, y2, tileW, tileH, 2};
  tiles[3] = {x2, y2, tileW, tileH, 3};
}

void drawBackground() {
  lcd.fillScreen(COLOR_BG);
}

void drawStatusBar() {
  // pozadie status baru
  lcd.fillRoundRect(4, 4, SCREEN_W - 8, STATUS_BAR_H - 6, 10, COLOR_PANEL);

  // -------- ČAS vľavo (väčší text) --------
  lcd.setTextColor(COLOR_TEXT, COLOR_PANEL);
  lcd.setTextSize(2);

  String tstr = timeClient.getFormattedTime();
  lcd.setCursor(10, 10);
  lcd.print(tstr);

  // -------- WiFi block vpravo (menší text) --------
  int rightMargin = 10;
  int top = 8;

  if (WiFi.status() == WL_CONNECTED) {
    String ip = WiFi.localIP().toString();

    // WiFi label (menšie písmo)
    lcd.setTextSize(1);
    lcd.setCursor(SCREEN_W - rightMargin - 30, top);
    lcd.print("WiFi");

    int charW = 6 * 1;
    int ipWidth = ip.length() * charW;

    int ipX = SCREEN_W - rightMargin - ipWidth;
    int ipY = top + 12; // pod "WiFi"

    lcd.setCursor(ipX, ipY);
    lcd.print(ip);
  } else {
    lcd.setTextSize(1);
    lcd.setCursor(SCREEN_W - rightMargin - 30, top);
    lcd.print("OFF");
    lcd.drawLine(SCREEN_W - rightMargin - 10, top + 3,
                 SCREEN_W - rightMargin + 8, top + 15, TFT_RED);
  }
}

void drawTile(int idx) {
  Tile &tile = tiles[idx];

  bool on = relayState[tile.relayIndex];
  uint16_t base = on ? COLOR_TILE_ON_D : COLOR_TILE_OFF;
  uint16_t hi   = on ? COLOR_TILE_ON   : COLOR_TILE_OFF;

  int x = tile.x;
  int y = tile.y;
  int w = tile.w;
  int h = tile.h;

  int cx = x + w / 2;
  int cy = y + h / 2 - 8;

  String label = relayLabels[tile.relayIndex];
  const char* stateText = on ? "ZAP" : "VYP";
  uint16_t stColor = on ? COLOR_BG : COLOR_ACCENT;

  // --- začiatok rýchlej transakcie na SPI ---
  lcd.startWrite();

  // tieň + "plast" dlaždice
  lcd.fillRoundRect(x,     y + 3, w,     h,     14, COLOR_BG);
  lcd.fillRoundRect(x,     y,     w,     h,     14, base);
  lcd.fillRoundRect(x + 2, y + 2, w - 4, h - 6, 12, hi);

  // text – label
  lcd.setTextColor(COLOR_TEXT, hi);
  lcd.setTextSize(2);
  int labelWidth = label.length() * 6;  // approx, pre textSize=2 cca x2, ale stačí
  lcd.setCursor(cx - labelWidth / 2, cy);
  lcd.print(label);

  // text – stav
  lcd.setTextColor(stColor, hi);
  lcd.setTextSize(2);
  lcd.setCursor(cx - 18, cy + 16);
  lcd.print(stateText);

  lcd.endWrite();
  // --- koniec transakcie ---
}


void drawAllTiles() {
  for (int i = 0; i < 4; ++i) {
    drawTile(i);
  }
}

int hitTestTile(int x, int y) {
  for (int i = 0; i < 4; ++i) {
    Tile &tile = tiles[i];
    if (x >= tile.x && x <= tile.x + tile.w &&
        y >= tile.y && y <= tile.y + tile.h) {
      return i;
    }
  }
  return -1;
}

bool getTouch(int &sx, int &sy) {
  lgfx::touch_point_t tp;
  if (!lcd.getTouch(&tp)) {
    return false;
  }
  sx = lcd.width() - tp.x;
  sy = tp.y;
  return true;
}

// ============= SETUP & LOOP =============

void setup() {
  Serial.begin(115200);

  setupRelayPins();
  applyRelayStates();

  lcd.init();
  lcd.setRotation(1);

  drawBackground();
  setupTiles();

  // UI IDE HNED – základný status bar a tiles
  drawStatusBar();   // ukáže OFF / bez IP
  drawAllTiles();

  // Až potom riešime WiFi a sieť
  connectWiFi();
  timeClient.begin();
  timeClient.update();
  setupHttpServer();

  // Po úspešnom WiFi update status baru s IP
  drawStatusBar();
}

void loop() {
  server.handleClient();

  // aktualizácia času + status baru
  if (WiFi.status() == WL_CONNECTED) {
    if (millis() - lastTimeUpdate > 5000) {
      lastTimeUpdate = millis();
      timeClient.update();
      drawStatusBar();
    }
  } else {
    if (millis() - lastTimeUpdate > 1000) {
      lastTimeUpdate = millis();
      drawStatusBar();
    }
  }

  // pravidelný reconnect
  static unsigned long lastCheck = 0;
  if (millis() - lastCheck > 5000) {
    lastCheck = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("Lost WiFi! Reconnecting...");
      connectWiFi();
      drawStatusBar();
    }
  }

  // dotyk + prepínanie relé (debounce cez millis)
  int x, y;
  static unsigned long lastTouchMs = 0;
  if (getTouch(x, y)) {
    unsigned long now = millis();
    if (now - lastTouchMs > 180) {
      lastTouchMs = now;
      int idx = hitTestTile(x, y);
      if (idx >= 0) {
        relayState[idx] = !relayState[idx];
        applyRelayStates();
        drawTile(idx);
      }
    }
  }
}


'''
