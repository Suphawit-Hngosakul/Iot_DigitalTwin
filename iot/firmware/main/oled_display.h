#ifndef OLED_DISPLAY_H
#define OLED_DISPLAY_H

#include <Arduino.h>

void initOLED();

// Boot-time status screen. Call only from setup() (no I2C locking).
void oledShowStatus(const char *step, const char *detail = nullptr,
                    const char *extra = nullptr);

void oledTask(void *parameter);

#endif // OLED_DISPLAY_H
