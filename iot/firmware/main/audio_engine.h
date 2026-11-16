#ifndef AUDIO_ENGINE_H
#define AUDIO_ENGINE_H

#include <Arduino.h>
#include <FS.h>

#define WAV_HEADER_SIZE 44   // bytes; PCM data starts right after the header

// Seek to start of file and write a header describing `dataSize` bytes of PCM.
void writeWAVHeader(File &file, uint32_t dataSize);

// Core 1: read I2S, push blocks to the SD pipeline (readyQueue).
void micTask(void *parameter);

#endif // AUDIO_ENGINE_H
