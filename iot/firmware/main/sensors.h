#ifndef SENSORS_H
#define SENSORS_H

#include <Arduino.h>

void initBH1750();
void initDHT();

void luxTask(void *parameter);
void dhtTask(void *parameter);

#endif // SENSORS_H
