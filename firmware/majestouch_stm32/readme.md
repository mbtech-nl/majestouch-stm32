# majestouch_stm32

QMK keyboard definition for the [majestouch-stm32](https://github.com/mbtech-nl/majestouch-stm32)
replacement controller (STM32F072) for the full-size Filco Majestouch.

* Keyboard maintainer: [mbtech-nl](https://github.com/mbtech-nl)
* Hardware supported: majestouch-stm32 controller board in a full-size Filco Majestouch
* Hardware availability: https://github.com/mbtech-nl/majestouch-stm32

Copy this folder into `qmk_firmware/keyboards/`, then:

    qmk compile -kb majestouch_stm32 -km default
    qmk flash -kb majestouch_stm32 -km default

## Bootloader

* **First flash**: bridge the BOOT jumper while plugging in USB (or bridge BOOT and tap RESET).
* **Bootmagic reset**: hold Esc while plugging in.

## License

The files in this folder are MIT licensed, like the rest of the project.
Firmware built from them includes QMK, which is GPL-2.0-or-later, so compiled
binaries are covered by the GPL.

`tools/check.py` verifies the matrix and LED pins in these files against the
schematic.
