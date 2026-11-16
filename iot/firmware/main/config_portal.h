#ifndef CONFIG_PORTAL_H
#define CONFIG_PORTAL_H

#include <Arduino.h>

// =============================================================================
//  Setup Mode (WiFi AP + web page)
//
//  Hold the BOOT button (CONFIG_BUTTON_PIN) during the boot window to enter.
//  The ESP32 opens its own access point CONFIG_AP_SSID, and every page a phone
//  opens is redirected to http://192.168.4.1 (captive portal). No internet or
//  router needed. Saving or leaving the page restarts into logging mode;
//  so does CONFIG_PORTAL_TIMEOUT_MS of inactivity.
// =============================================================================

// Watches the button for CONFIG_BUTTON_WINDOW_MS (shows a prompt on OLED).
// Returns true if it was held down for CONFIG_BUTTON_HOLD_MS.
bool configButtonRequested();

// Runs the AP + web server. Never returns (ends with ESP.restart()).
// Call from setup() after OLED, RTC and settingsLoad(), before tasks start.
void runConfigPortal();

#endif // CONFIG_PORTAL_H
