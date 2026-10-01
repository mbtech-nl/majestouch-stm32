// Copyright 2026 MB Tech
// SPDX-License-Identifier: MIT

#include "quantum.h"

// The Filco lock LED anodes are tied to +5 V on the main PCB. These pins are
// 5 V tolerant only as inputs/open-drain, so never drive them push-pull high.
static const pin_t lock_leds[] = {B3, A15, A14}; // num, caps, scroll

void keyboard_pre_init_kb(void) {
    for (uint8_t i = 0; i < ARRAY_SIZE(lock_leds); i++) {
        gpio_write_pin_high(lock_leds[i]);
        gpio_set_pin_output_open_drain(lock_leds[i]);
    }
    keyboard_pre_init_user();
}

bool led_update_kb(led_t state) {
    bool res = led_update_user(state);
    if (res) {
        gpio_write_pin(lock_leds[0], !state.num_lock);
        gpio_write_pin(lock_leds[1], !state.caps_lock);
        gpio_write_pin(lock_leds[2], !state.scroll_lock);
    }
    return res;
}
