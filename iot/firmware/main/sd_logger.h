#ifndef SD_LOGGER_H
#define SD_LOGGER_H

#include <Arduino.h>

// Mount SD_MMC (1-bit mode) and set sdFreeMB. Returns false on failure:
// the logger then keeps running, shows NO SD CARD and retries every
// SD_REMOUNT_INTERVAL_MS; the CSV is created when a card is inserted.
bool mountSDCard();

// Create "<date>/spl_log_<timestamp>.csv" with header row.
bool createCSVLog();

// Core 0: WAV recording + SPL compute + CSV logging + SD health/remount.
void sdProcessTask(void *parameter);

#endif // SD_LOGGER_H
