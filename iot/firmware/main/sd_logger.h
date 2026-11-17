#ifndef SD_LOGGER_H
#define SD_LOGGER_H

#include <Arduino.h>

// First line of every CSV (spec 01 §1). The uploader (#7) sends it before
// every chunk and skips files whose header differs.
#define CSV_HEADER "Device_ID,Device_Type,Session,Record_ID," \
                   "DateTime,dBA,dB_SPL,Lux,Temp_C,Humidity_pct,Latitude," \
                   "Longitude,Altitude_m,Speed_kmh,Satellites,HDOP," \
                   "GPS_Valid,Lux_Valid,DHT_Valid," \
                   "Time_Valid,SPL_Valid,Flags"

// Mount SD_MMC (1-bit mode) and set sdFreeMB. Returns false on failure:
// the logger then keeps running, shows NO SD CARD and retries every
// SD_REMOUNT_INTERVAL_MS; the CSV is created when a card is inserted.
bool mountSDCard();

// Create "<date>/spl_log_<timestamp>.csv" with header row.
bool createCSVLog();

// Path of the CSV this boot writes to ("" before it exists). The uploader
// treats it as "still growing": only complete lines are sent.
void csvActivePath(char *out, size_t len);

// Core 0: WAV recording + SPL compute + CSV logging + SD health/remount.
void sdProcessTask(void *parameter);

#endif // SD_LOGGER_H
