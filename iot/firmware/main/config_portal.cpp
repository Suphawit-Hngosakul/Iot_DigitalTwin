#include "config_portal.h"
#include "config.h"
#include "globals.h"
#include "oled_display.h"
#include "rtc_helper.h"
#include "settings.h"

#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>


static WebServer server(80);
static DNSServer dnsServer;

static const IPAddress AP_IP(192, 168, 4, 1);
static const IPAddress AP_MASK(255, 255, 255, 0);

static unsigned long lastActivityMs = 0;   // last real page request
static unsigned long restartAtMs    = 0;   // 0 = no restart scheduled


// =============================================================================
//  Boot Button
// =============================================================================

bool configButtonRequested() {
  pinMode(CONFIG_BUTTON_PIN, INPUT_PULLUP);

  unsigned long start     = millis();
  unsigned long pressedAt = 0;
  int           lastShown = -1;

  while (millis() - start < CONFIG_BUTTON_WINDOW_MS) {
    bool pressed = digitalRead(CONFIG_BUTTON_PIN) == LOW;

    if (pressed) {
      if (pressedAt == 0) pressedAt = millis();
      if (millis() - pressedAt >= CONFIG_BUTTON_HOLD_MS) {
        LOG("CFG", "Setup button held - entering setup mode");
        return true;
      }
    } else {
      pressedAt = 0;
    }

    int left = (CONFIG_BUTTON_WINDOW_MS - (millis() - start) + 999) / 1000;
    if (left != lastShown) {
      char buf[24];
      snprintf(buf, sizeof(buf), "%ds...", left);
      oledShowStatus("Hold BOOT button", "to enter Setup Mode", buf);
      lastShown = left;
    }
    delay(20);
  }
  return false;
}


// =============================================================================
//  HTML Helpers
// =============================================================================

static String htmlEscape(const char *s) {
  String out;
  for (; *s; s++) {
    switch (*s) {
      case '&':  out += F("&amp;");  break;
      case '<':  out += F("&lt;");   break;
      case '>':  out += F("&gt;");   break;
      case '"':  out += F("&quot;"); break;
      case '\'': out += F("&#39;");  break;
      default:   out += *s;
    }
  }
  return out;
}

static const char PAGE_HEAD[] PROGMEM = R"HTML(<!DOCTYPE html>
<html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IoT-Walking Setup</title>
<style>
body{font-family:sans-serif;margin:0;background:#f2f4f7;color:#222}
main{max-width:520px;margin:auto;padding:12px}
h1{font-size:1.3em;margin:8px 0}
fieldset{background:#fff;border:1px solid #ccd;border-radius:8px;margin:12px 0;padding:10px}
legend{font-weight:bold;padding:0 4px}
label{display:block;margin:8px 0 2px;font-size:.92em}
small{color:#667}
input,select{width:100%;box-sizing:border-box;padding:8px;font-size:1em;border:1px solid #aab;border-radius:6px}
button{width:100%;padding:11px;font-size:1em;border:0;border-radius:6px;margin:6px 0;background:#2563eb;color:#fff}
button.sec{background:#64748b} button.warn{background:#dc2626}
.msg{padding:10px;border-radius:6px;background:#dcfce7;margin:10px 0}
.err{background:#fee2e2}
</style></head><body><main>
)HTML";

static const char PAGE_TAIL[] PROGMEM = "</main></body></html>";

static void sendPage(const String &body, int code = 200) {
  String html;
  html.reserve(strlen_P(PAGE_HEAD) + body.length() + 32);
  html += FPSTR(PAGE_HEAD);
  html += body;
  html += FPSTR(PAGE_TAIL);
  server.send(code, "text/html; charset=utf-8", html);
}

static void addField(String &b, const char *label, const char *name,
                     const String &value, const char *hint,
                     const char *type = "text", const char *extra = "") {
  b += F("<label>");  b += label;  b += F("</label><input type=\"");
  b += type;          b += F("\" name=\"");  b += name;
  b += F("\" value=\""); b += value; b += F("\" ");  b += extra;  b += F(">");
  if (hint && *hint) { b += F("<small>"); b += hint; b += F("</small>"); }
}

static String fmtFloat(float v, int decimals) { return String(v, decimals); }


// =============================================================================
//  Pages
// =============================================================================

static void handleRoot() {
  lastActivityMs = millis();
  const RuntimeSettings &s = settings;

  String b;
  b.reserve(7500);
  b += F("<h1>IoT-Walking : ตั้งค่า</h1>");

  // ── RTC ──────────────────────────────────────────────────────────────
  char now[DATETIME_BUF_SIZE];
  getDisplayTimeStr(now, sizeof(now));
  b += F("<fieldset><legend>เวลา (RTC)</legend><p>เวลาในเครื่อง: <b>");
  b += now;
  b += F("</b><br>เวลามือถือ: <b id=\"pt\">-</b></p>");
  if (rtcAvailable) {
    b += F("<form method=\"post\" action=\"/time\" onsubmit=\"fillTime()\">"
           "<input type=\"hidden\" name=\"y\" id=\"ty\"><input type=\"hidden\" name=\"mo\" id=\"tmo\">"
           "<input type=\"hidden\" name=\"d\" id=\"td\"><input type=\"hidden\" name=\"h\" id=\"th\">"
           "<input type=\"hidden\" name=\"mi\" id=\"tmi\"><input type=\"hidden\" name=\"s\" id=\"ts\">"
           "<button>ตั้งเวลา RTC ตามมือถือ</button></form>"
           "<small>ใช้เวลาท้องถิ่นของมือถือ (ควรตั้งมือถือเป็นเขตเวลาไทย UTC+7)</small>");
  } else {
    b += F("<small>ไม่พบ DS3231 จึงตั้งเวลาไม่ได้</small>");
  }
  b += F("</fieldset>");

  // ── Settings form ────────────────────────────────────────────────────
  b += F("<form method=\"post\" action=\"/save\">");

  b += F("<fieldset><legend>อุปกรณ์</legend>");
  addField(b, "Device ID", "devid", htmlEscape(s.deviceId),
           "A-Z a-z 0-9 - _ ไม่เกิน 16 ตัว (เว้นว่าง = สร้างจาก MAC)", "text",
           "maxlength=\"16\" pattern=\"[A-Za-z0-9_\\-]*\"");
  b += F("<label>ประเภทอุปกรณ์</label><select name=\"devtype\">");
  for (uint8_t t = DEVICE_STATIONARY; t <= DEVICE_MOBILE; t++) {
    b += F("<option value=\""); b += String(t); b += '"';
    if (t == s.deviceType) b += F(" selected");
    b += '>'; b += deviceTypeName(t); b += F("</option>");
  }
  b += F("</select><small>ใส่ลงทุกแถวของ CSV คู่กับ Session และ Record_ID</small></fieldset>");

  b += F("<fieldset><legend>WiFi (sync เวลา NTP + ส่งข้อมูล)</legend>");
  addField(b, "SSID 1", "ssid1", htmlEscape(s.wifiSsid1), "", "text", "maxlength=\"32\"");
  addField(b, "รหัสผ่าน 1", "pass1", "", s.wifiPass1[0] ? "เว้นว่าง = ใช้รหัสเดิม" : "",
           "password", "maxlength=\"64\" autocomplete=\"off\"");
  addField(b, "SSID 2", "ssid2", htmlEscape(s.wifiSsid2), "", "text", "maxlength=\"32\"");
  addField(b, "รหัสผ่าน 2", "pass2", "", s.wifiPass2[0] ? "เว้นว่าง = ใช้รหัสเดิม" : "",
           "password", "maxlength=\"64\" autocomplete=\"off\"");
  b += F("<small>ลบ SSID ออก = ไม่ใช้ WiFi ชุดนั้น (รหัสจะถูกลบด้วย)</small></fieldset>");

  b += F("<fieldset><legend>เซิร์ฟเวอร์</legend>");
  addField(b, "Ingest URL (อัป CSV)", "ingest", htmlEscape(s.ingestUrl),
           "http://<relay>:8080/ingest (เว้นว่าง = ยังไม่ส่ง)", "url", "maxlength=\"128\"");
  addField(b, "Presign URL (อัป WAV)", "presign", htmlEscape(s.presignUrl),
           "https://... (เว้นว่าง = ยังไม่ส่ง)", "url", "maxlength=\"128\"");
  addField(b, "Realtime WSS URL", "wss", htmlEscape(s.wssUrl),
           "wss://.../device (เว้นว่าง = ยังไม่ส่ง)", "text", "maxlength=\"128\"");
  b += F("<small>Token ของเครื่องตั้งใน secrets.h (DEVICE_TOKEN) เท่านั้น ");
  b += DEVICE_TOKEN[0] ? F("- ตั้งแล้ว") : F("- <b>ยังไม่ได้ตั้ง</b>");
  b += F("</small></fieldset>");

  b += F("<fieldset><legend>Calibration</legend>");
  addField(b, "MIC_OFFSET_DB (dB)", "mic", fmtFloat(s.micOffsetDb, 2),
           "dBFS + ค่านี้ = dB SPL (50 ถึง 200)", "number", "step=\"any\" required");
  addField(b, "DHT22 Temp offset (&deg;C)", "dhtT", fmtFloat(s.dhtTempOffset, 2),
           "-20 ถึง 20", "number", "step=\"any\" required");
  addField(b, "DHT22 Humidity offset (%)", "dhtH", fmtFloat(s.dhtHumidOffset, 2),
           "-50 ถึง 50", "number", "step=\"any\" required");
  addField(b, "Lux slope", "luxS", fmtFloat(s.luxCalSlope, 4),
           "lux = raw &times; slope + offset (0.01 ถึง 10)", "number", "step=\"any\" required");
  addField(b, "Lux offset", "luxO", fmtFloat(s.luxCalOffset, 2),
           "-1000 ถึง 1000", "number", "step=\"any\" required");
  b += F("</fieldset>");

  b += F("<fieldset><legend>การบันทึก</legend>");
  addField(b, "RECORD_TIME (วินาที/ไฟล์ WAV)", "rec", String(s.recordTimeSec),
           "5 ถึง 3600", "number", "min=\"5\" max=\"3600\" required");
  addField(b, "CSV_INTERVAL_MS (ms)", "csv", String(s.csvIntervalMs),
           "100 ถึง 60000", "number", "min=\"100\" max=\"60000\" required");
  addField(b, "SD_MIN_FREE_MB (MB)", "sdmin", String(s.sdMinFreeMb),
           "0 ถึง 100000", "number", "min=\"0\" max=\"100000\" required");
  b += F("</fieldset>");

  b += F("<button>บันทึกและรีสตาร์ท</button></form>");

  b += F("<form method=\"post\" action=\"/exit\"><button class=\"sec\">ออก (ไม่บันทึก) และเริ่มเก็บข้อมูล</button></form>"
         "<form method=\"post\" action=\"/reset\" onsubmit=\"return confirm('คืนค่าทั้งหมดเป็นค่าใน config.h?')\">"
         "<button class=\"warn\">คืนค่าเริ่มต้น (config.h)</button></form>");

  char tail[96];
  snprintf(tail, sizeof(tail),
           "<small>ไม่มีการใช้งาน %lu นาที เครื่องจะออกจากโหมดนี้เอง</small>",
           (unsigned long)(CONFIG_PORTAL_TIMEOUT_MS / 60000));
  b += tail;

  b += F("<script>"
         "function p(n){return n<10?'0'+n:n}"
         "function fillTime(){var t=new Date();"
         "ty.value=t.getFullYear();tmo.value=t.getMonth()+1;td.value=t.getDate();"
         "th.value=t.getHours();tmi.value=t.getMinutes();ts.value=t.getSeconds();}"
         "function show(){var t=new Date();pt.textContent=p(t.getDate())+'/'+p(t.getMonth()+1)+'/'+"
         "t.getFullYear()+' '+p(t.getHours())+':'+p(t.getMinutes())+':'+p(t.getSeconds());}"
         "show();setInterval(show,1000);"
         "</script>");

  sendPage(b);
}


static void sendResult(const String &msg, bool error, bool restarting) {
  String b = F("<h1>IoT-Walking : ตั้งค่า</h1><div class=\"msg");
  if (error) b += F(" err");
  b += F("\">");
  b += msg;
  b += F("</div>");
  if (restarting)
    b += F("<p>เครื่องกำลังรีสตาร์ทเข้าโหมดเก็บข้อมูล ปิดหน้านี้ได้เลย</p>");
  else
    b += F("<p><a href=\"/\">กลับหน้าตั้งค่า</a></p>");
  sendPage(b, error ? 400 : 200);
}

static void scheduleRestart() { restartAtMs = millis() + 1500; }


// Parse a float field; append an error message and return false if invalid.
static bool argFloat(const char *name, float lo, float hi, float &out, String &err) {
  String v = server.arg(name);
  v.trim();
  char *end = nullptr;
  float f = strtof(v.c_str(), &end);
  if (v.length() == 0 || end == v.c_str() || *end != '\0' || f != f || f < lo || f > hi) {
    err += name; err += F(" ต้องอยู่ระหว่าง "); err += String(lo, 2);
    err += F(" ถึง "); err += String(hi, 2); err += F("<br>");
    return false;
  }
  out = f;
  return true;
}

static bool argUInt(const char *name, uint32_t lo, uint32_t hi, uint32_t &out, String &err) {
  String v = server.arg(name);
  v.trim();
  char *end = nullptr;
  unsigned long n = strtoul(v.c_str(), &end, 10);
  if (v.length() == 0 || end == v.c_str() || *end != '\0' || v[0] == '-' || n < lo || n > hi) {
    err += name; err += F(" ต้องเป็นจำนวนเต็ม "); err += lo;
    err += F(" ถึง "); err += hi; err += F("<br>");
    return false;
  }
  out = (uint32_t)n;
  return true;
}

// SSID + password pair. Empty password with unchanged SSID keeps the old one.
static void argWifi(const char *ssidKey, const char *passKey,
                    char *ssid, char *pass, String &err) {
  String newSsid = server.arg(ssidKey);
  String newPass = server.arg(passKey);
  newSsid.trim();

  if (newSsid.length() >= SETTINGS_SSID_LEN || newPass.length() >= SETTINGS_PASS_LEN) {
    err += ssidKey; err += F(" / "); err += passKey; err += F(" ยาวเกินไป<br>");
    return;
  }
  if (newPass.length() > 0 && newPass.length() < 8) {
    err += passKey; err += F(" ต้องยาวอย่างน้อย 8 ตัว (หรือเว้นว่าง)<br>");
    return;
  }

  bool sameSsid = newSsid.equals(ssid);
  if (newSsid.length() == 0) {
    pass[0] = '\0';                              // WiFi slot removed
  } else if (newPass.length() > 0 || !sameSsid) {
    strncpy(pass, newPass.c_str(), SETTINGS_PASS_LEN - 1);
    pass[SETTINGS_PASS_LEN - 1] = '\0';
  }
  strncpy(ssid, newSsid.c_str(), SETTINGS_SSID_LEN - 1);
  ssid[SETTINGS_SSID_LEN - 1] = '\0';
}


// URL field: empty, or http(s):// (ws(s):// when websocket) up to 128 chars.
static void argUrl(const char *name, bool websocket, char *dst, String &err) {
  String v = server.arg(name);
  v.trim();
  if (!settingsUrlValid(v.c_str(), websocket)) {
    err += name;
    err += websocket ? F(" ต้องขึ้นต้นด้วย wss:// หรือ ws://")
                     : F(" ต้องขึ้นต้นด้วย https:// หรือ http://");
    err += F(" ไม่มีช่องว่าง และยาวไม่เกิน 128 ตัว (หรือเว้นว่าง)<br>");
    return;
  }
  strncpy(dst, v.c_str(), SETTINGS_URL_LEN - 1);
  dst[SETTINGS_URL_LEN - 1] = '\0';
}

// Device ID (empty -> MAC default) + Device Type.
static void argDevice(RuntimeSettings &n, String &err) {
  String id = server.arg("devid");
  id.trim();
  if (id.length() == 0) {
    RuntimeSettings def;
    settingsDefaults(def);
    id = def.deviceId;
  }
  if (!deviceIdValid(id.c_str())) {
    err += F("Device ID ใช้ได้เฉพาะ A-Z a-z 0-9 - _ และยาวไม่เกิน 16 ตัว<br>");
  } else {
    strncpy(n.deviceId, id.c_str(), SETTINGS_DEVID_LEN - 1);
    n.deviceId[SETTINGS_DEVID_LEN - 1] = '\0';
  }

  uint32_t type = n.deviceType;
  if (argUInt("devtype", DEVICE_STATIONARY, DEVICE_MOBILE, type, err))
    n.deviceType = (uint8_t)type;
}

static void handleSave() {
  lastActivityMs = millis();

  RuntimeSettings n = settings;   // start from current, overwrite fields
  String err;

  argDevice(n, err);
  argWifi("ssid1", "pass1", n.wifiSsid1, n.wifiPass1, err);
  argWifi("ssid2", "pass2", n.wifiSsid2, n.wifiPass2, err);

  argFloat("mic",  50.0f,   200.0f,  n.micOffsetDb,    err);
  argFloat("dhtT", -20.0f,  20.0f,   n.dhtTempOffset,  err);
  argFloat("dhtH", -50.0f,  50.0f,   n.dhtHumidOffset, err);
  argFloat("luxS", 0.01f,   10.0f,   n.luxCalSlope,    err);
  argFloat("luxO", -1000.0f, 1000.0f, n.luxCalOffset,  err);

  argUInt("rec",   5,   3600,   n.recordTimeSec, err);
  argUInt("csv",   100, 60000,  n.csvIntervalMs, err);
  argUInt("sdmin", 0,   100000, n.sdMinFreeMb,   err);

  argUrl("ingest",  false, n.ingestUrl,  err);
  argUrl("presign", false, n.presignUrl, err);
  argUrl("wss",     true,  n.wssUrl,     err);

  if (err.length()) {
    sendResult(String(F("ยังไม่ได้บันทึก ค่าไม่ถูกต้อง:<br>")) + err, true, false);
    return;
  }

  if (!settingsSave(n)) {
    sendResult(F("บันทึกลงหน่วยความจำ (NVS) ไม่สำเร็จ"), true, false);
    return;
  }
  settingsPrint();
  sendResult(F("บันทึกเรียบร้อย"), false, true);
  scheduleRestart();
}


static void handleTime() {
  lastActivityMs = millis();
  if (!rtcAvailable) {
    sendResult(F("ไม่พบ RTC (DS3231)"), true, false);
    return;
  }

  struct tm t = {};
  t.tm_year = server.arg("y").toInt() - 1900;
  t.tm_mon  = server.arg("mo").toInt() - 1;
  t.tm_mday = server.arg("d").toInt();
  t.tm_hour = server.arg("h").toInt();
  t.tm_min  = server.arg("mi").toInt();
  t.tm_sec  = server.arg("s").toInt();

  if (t.tm_year < 2020 - 1900 || t.tm_year > 2099 - 1900 ||
      t.tm_mon < 0 || t.tm_mon > 11 || t.tm_mday < 1 || t.tm_mday > 31 ||
      t.tm_hour < 0 || t.tm_hour > 23 || t.tm_min < 0 || t.tm_min > 59 ||
      t.tm_sec < 0 || t.tm_sec > 59) {
    sendResult(F("เวลาที่ส่งมาไม่ถูกต้อง (เปิด JavaScript ในเบราว์เซอร์หรือไม่?)"), true, false);
    return;
  }

  rtcSetTime(t);
  char buf[DATETIME_BUF_SIZE];
  getDateTimeStr(buf, sizeof(buf));
  LOG("CFG", "RTC set from phone: %s", buf);
  sendResult(String(F("ตั้งเวลา RTC แล้ว: ")) + buf, false, false);
}


static void handleExit() {
  sendResult(F("ออกจากโหมดตั้งค่า"), false, true);
  scheduleRestart();
}

static void handleReset() {
  settingsReset();
  sendResult(F("คืนค่าเริ่มต้นจาก config.h แล้ว"), false, true);
  scheduleRestart();
}

// Captive portal: anything else (OS connectivity checks included) goes to "/".
static void handleNotFound() {
  server.sendHeader("Location", String("http://") + AP_IP.toString() + "/", true);
  server.send(302, "text/plain", "");
}


// =============================================================================
//  Portal Loop
// =============================================================================

void runConfigPortal() {
  LOG("CFG", "Starting setup AP '%s'", CONFIG_AP_SSID);

  WiFi.persistent(false);
  WiFi.mode(WIFI_AP);
  WiFi.softAPConfig(AP_IP, AP_IP, AP_MASK);
  const char *apPass = (strlen(CONFIG_AP_PASS) >= 8) ? CONFIG_AP_PASS : nullptr;
  if (!WiFi.softAP(CONFIG_AP_SSID, apPass)) {
    LOG("ERROR", "softAP failed - restarting");
    oledShowStatus("Setup Mode", "AP start FAILED", "Restarting...");
    delay(3000);
    ESP.restart();
  }

  dnsServer.setErrorReplyCode(DNSReplyCode::NoError);
  dnsServer.start(53, "*", AP_IP);

  server.on("/",      HTTP_GET,  handleRoot);
  server.on("/save",  HTTP_POST, handleSave);
  server.on("/time",  HTTP_POST, handleTime);
  server.on("/exit",  HTTP_POST, handleExit);
  server.on("/reset", HTTP_POST, handleReset);
  server.onNotFound(handleNotFound);
  server.begin();

  LOG("CFG", "Open http://%s  (AP '%s', pass '%s')",
      AP_IP.toString().c_str(), CONFIG_AP_SSID, apPass ? apPass : "<open>");

  char line2[26], line3[26];
  snprintf(line2, sizeof(line2), "%s", CONFIG_AP_SSID);
  snprintf(line3, sizeof(line3), "PW:%s %s", apPass ? apPass : "-",
           AP_IP.toString().c_str());
  oledShowStatus("SETUP MODE (WiFi)", line2, line3);

  lastActivityMs = millis();

  while (true) {
    dnsServer.processNextRequest();
    server.handleClient();

    unsigned long now = millis();
    if (restartAtMs && (long)(now - restartAtMs) >= 0) break;
    if (now - lastActivityMs >= CONFIG_PORTAL_TIMEOUT_MS) {
      LOG("CFG", "Setup mode idle timeout");
      break;
    }
    delay(2);
  }

  LOG("CFG", "Leaving setup mode - restarting into logging mode");
  oledShowStatus("Setup done", "Restarting...");
  server.stop();
  dnsServer.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  delay(500);
  ESP.restart();
}
