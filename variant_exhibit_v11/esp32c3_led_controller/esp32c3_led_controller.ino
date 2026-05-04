/*
 * esp32c3_led_controller.ino
 * ===========================
 * Variant Security Drone Exhibit — XIAO ESP32C3 LED Controller
 *
 * Drives 144 WS2812B LEDs (4 sections of 36 each, one per drone)
 * via FastLED, receiving per-drone role commands from the Pi over USB serial.
 *
 * Protocol (115200 baud, newline-terminated):
 *
 *   MODE:P1P2P3P4\n
 *
 *   MODE = IDLE | LAUNCH | FLY | SHIELD | VIRUS | CRASH | OFF
 *   P1-P4 = per-drone role character:
 *       I = idle (blue pulse)
 *       L = launch (white/green upward chase)
 *       F = fly (green gentle pulse)
 *       S = shield (cyan steady glow)
 *       V = virus (red strobe)
 *       C = crash (red fade-out)
 *       O = off (all black)
 *
 *   Example: "VIRUS:FFSV\n"
 *     Drone 1: fly green, Drone 2: fly green,
 *     Drone 3: shield cyan, Drone 4: virus red strobe
 *
 * Hardware:
 *   - XIAO ESP32C3
 *   - Data pin: GPIO8 (D8) → DIN of first WS2812B in chain
 *   - 144 LEDs total, wired in series: D1[0..35] D2[36..71] D3[72..107] D4[108..143]
 *
 * Dependencies:
 *   - FastLED library (Install via Arduino Library Manager)
 *
 * Build:
 *   Board: "XIAO_ESP32C3" in Arduino IDE / PlatformIO
 *   Upload via USB-C
 */

#include <FastLED.h>

#define LED_PIN       8
#define NUM_LEDS      144
#define LEDS_PER_DRONE 36
#define NUM_DRONES    4
#define SERIAL_BAUD   115200
#define MAX_CMD_LEN   32

CRGB leds[NUM_LEDS];

// Per-drone role: one character each
char droneRole[NUM_DRONES] = {'I', 'I', 'I', 'I'};

// Animation frame counter (wraps at 65535, fine for modular animation)
uint16_t frame = 0;

// Serial input buffer
char cmdBuf[MAX_CMD_LEN];
uint8_t cmdIdx = 0;

// Timing
unsigned long lastFrame = 0;
const unsigned long FRAME_MS = 20;  // 50 fps base tick

// Crash fade brightness (per drone, 0-255, starts at 255 on 'C' entry)
uint8_t crashBright[NUM_DRONES] = {0, 0, 0, 0};
bool    crashActive[NUM_DRONES] = {false, false, false, false};

// ── Helpers ──────────────────────────────────────────────────────────────

// Get the LED index range for a drone (0-based drone index)
inline uint16_t droneStart(uint8_t d) { return (uint16_t)d * LEDS_PER_DRONE; }
inline uint16_t droneEnd(uint8_t d)   { return droneStart(d) + LEDS_PER_DRONE; }

// Fill a drone's section with a solid color
void droneFill(uint8_t d, CRGB color) {
    uint16_t s = droneStart(d);
    uint16_t e = droneEnd(d);
    for (uint16_t i = s; i < e; i++) {
        leds[i] = color;
    }
}

// Sin8-based pulse: returns 0-255 oscillation
uint8_t pulse8(uint16_t speed, uint16_t offset) {
    return sin8((uint8_t)((frame * speed / 10 + offset) & 0xFF));
}

// ── Per-drone effect renderers ───────────────────────────────────────────

void renderIdle(uint8_t d) {
    // Slow blue breathing pulse — each drone slightly phase-offset
    uint8_t bright = scale8(pulse8(3, d * 40), 180) + 20;
    CRGB color = CRGB(0, 0, bright);
    droneFill(d, color);
}

void renderLaunch(uint8_t d) {
    // White/green chase upward (LED 0 = bottom, 35 = top)
    uint16_t s = droneStart(d);
    uint8_t pos = (frame / 2 + d * 5) % LEDS_PER_DRONE;

    for (uint8_t i = 0; i < LEDS_PER_DRONE; i++) {
        int16_t dist = (int16_t)i - (int16_t)pos;
        if (dist < 0) dist += LEDS_PER_DRONE;
        if (dist < 4) {
            // Head of chase: bright white fading to green
            uint8_t fade = 255 - (dist * 60);
            leds[s + i] = CRGB(fade / 3, fade, fade / 4);
        } else if (dist < 10) {
            // Tail: dim green
            uint8_t tail = 60 - ((dist - 4) * 10);
            leds[s + i] = CRGB(0, tail, 0);
        } else {
            leds[s + i] = CRGB::Black;
        }
    }
}

void renderFly(uint8_t d) {
    // Green gentle pulse — alive and flying
    uint8_t bright = scale8(pulse8(5, d * 60), 120) + 50;
    CRGB color = CRGB(0, bright, bright / 8);
    droneFill(d, color);
}

void renderShield(uint8_t d) {
    // Cyan steady glow with a subtle shimmer — drone is protected
    uint8_t shimmer = scale8(pulse8(8, d * 30), 30);
    uint8_t base = 160 + shimmer;
    CRGB color = CRGB(0, base / 2, base);
    droneFill(d, color);
}

void renderVirus(uint8_t d) {
    // Red strobe: rapid flashing with random intensity spikes
    uint8_t strobe = ((frame + d * 7) % 6 < 3) ? 255 : 40;
    uint8_t flicker = random8(0, 60);
    uint8_t bright = qadd8(strobe, flicker);
    CRGB color = CRGB(bright, 0, bright / 8);

    uint16_t s = droneStart(d);
    for (uint8_t i = 0; i < LEDS_PER_DRONE; i++) {
        uint8_t localFlicker = random8(0, 30);
        leds[s + i] = CRGB(qadd8(bright, localFlicker), 0, qadd8(bright / 8, localFlicker / 4));
    }
}

void renderCrash(uint8_t d) {
    // Red fade-out: starts bright red, fades to black over ~3 seconds
    if (!crashActive[d]) {
        crashActive[d] = true;
        crashBright[d] = 255;
    }

    if (crashBright[d] > 1) {
        crashBright[d] = scale8(crashBright[d], 252);  // ~3s fade at 50fps
    } else {
        crashBright[d] = 0;
    }

    CRGB color = CRGB(crashBright[d], 0, 0);
    droneFill(d, color);
}

void renderOff(uint8_t d) {
    droneFill(d, CRGB::Black);
}

// ── Command dispatch ─────────────────────────────────────────────────────

void renderDrone(uint8_t d) {
    char role = droneRole[d];
    switch (role) {
        case 'I': renderIdle(d);    break;
        case 'L': renderLaunch(d);  break;
        case 'F': renderFly(d);     break;
        case 'S': renderShield(d);  break;
        case 'V': renderVirus(d);   break;
        case 'C': renderCrash(d);   break;
        case 'O': renderOff(d);     break;
        default:  renderIdle(d);    break;
    }
}

// ── Serial parsing ───────────────────────────────────────────────────────

void parseCommand(const char* cmd) {
    // Expected: "MODE:ABCD" where A,B,C,D are role chars
    // Find the colon
    const char* colon = strchr(cmd, ':');
    if (!colon || (colon - cmd) < 2) {
        return;  // Malformed
    }

    const char* roles = colon + 1;
    uint8_t len = strlen(roles);
    if (len < NUM_DRONES) {
        return;  // Not enough role characters
    }

    for (uint8_t d = 0; d < NUM_DRONES; d++) {
        char newRole = roles[d];
        char oldRole = droneRole[d];

        // Reset crash fade state when transitioning away from crash
        if (oldRole == 'C' && newRole != 'C') {
            crashActive[d] = false;
            crashBright[d] = 0;
        }
        // Initialize crash fade when entering crash
        if (oldRole != 'C' && newRole == 'C') {
            crashActive[d] = false;  // renderCrash will init on next frame
        }

        droneRole[d] = newRole;
    }
}

// ── Setup & loop ─────────────────────────────────────────────────────────

void setup() {
    Serial.begin(SERIAL_BAUD);

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
    FastLED.setBrightness(200);
    FastLED.setMaxPowerInVoltsAndMilliamps(5, 2000);  // 5V / 2A power budget

    // Start in idle
    for (uint8_t d = 0; d < NUM_DRONES; d++) {
        droneRole[d] = 'I';
    }

    fill_solid(leds, NUM_LEDS, CRGB::Black);
    FastLED.show();

    delay(100);
    Serial.println("READY");
}

void loop() {
    // Read serial commands
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (cmdIdx > 0) {
                cmdBuf[cmdIdx] = '\0';
                parseCommand(cmdBuf);
                cmdIdx = 0;
            }
        } else if (cmdIdx < MAX_CMD_LEN - 1) {
            cmdBuf[cmdIdx++] = c;
        } else {
            cmdIdx = 0;  // Overflow protection: discard
        }
    }

    // Frame-rate limiter
    unsigned long now = millis();
    if (now - lastFrame < FRAME_MS) {
        return;
    }
    lastFrame = now;
    frame++;

    // Render each drone section
    for (uint8_t d = 0; d < NUM_DRONES; d++) {
        renderDrone(d);
    }

    FastLED.show();
}
