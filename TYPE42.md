# 1 MiB Atarimax (CAR type 42)

The optional Type 42 handler loads standard, unchanged 1 MiB Atarimax `.car`
files. Enable it for a purple RP2040 A8PicoCart with **Winbond flash**:

```sh
cmake -S "source/Pico VSCode Project/a8_pico_cart" -B build -G Ninja \
  -DPICO_SDK_PATH=/path/to/pico-sdk -DCMAKE_BUILD_TYPE=Release \
  -DA8_ENABLE_TYPE42=ON
cmake --build build
```

Use Pico SDK 2.3.0 and an Arm GNU toolchain. The SDK's normal UF2 generation
remains unchanged. If building without picotool, add `-DPICO_NO_PICOTOOL=1`
and convert the resulting binary with:

```sh
python "source/Pico VSCode Project/a8_pico_cart/bin_to_uf2.py" \
  build/a8_pico_cart.bin build/a8_pico_cart.uf2
```

`A8_ENABLE_TYPE42` defaults to OFF to preserve the existing SRAM-only build
for other flash chips. The feature uses the existing 250 MHz system clock
and a Type42-only flash divider of 2 (125 MHz QSPI). The menu, USB mode,
other cartridge types, and filesystem layout keep their existing settings.
`A8_TYPE42_FLASH_DIVIDER=4` is available for development comparisons.

## Behavior

- Requires `CART`, full big-endian type 42, exactly 1 MiB plus the 16-byte
  header, and a matching additive payload checksum.
- Maps the original FatFs sectors to flash addresses, supporting fragmented
  files. No payload patches, extra flash copies, or filesystem writes.
- Reuses the existing 128 KiB SRAM buffer for the first file sector and
  final 255 sectors, including initial bank 127 and high code banks.
- The remaining ROM is read through uncached, non-allocating XIP pointers.
- Begins at bank 127. Reads or writes at `$D500-$D57F` select banks 0-127;
  `$D580-$D5FF` disables the cartridge window. Bank selection uses the
  address, not the written value.
- Before launch, checks the payload again through uncached flash at the
  selected speed, then restores the menu divider. A mismatch rejects launch.
- The bus loop runs from SRAM and checks address/select/read-write before
  fetching data and again before driving it. No Atari wait states are added.
- Uses internal mapper ID 43; this is distinct from CAR header type 42 and
  preserves the existing internal IDs for Dawliah and Jacart cartridges.
- Type 61 / 2 MiB cartridges are outside this change.

## Testing

The contributor reports unchanged 1 MiB AGI-ANTIC games loading and running
on the preceding v0.4 implementation, using a Winbond A8PicoCart and an
Atari 800XL with Ultimate 1MB. This integration retains that Type42 bus
implementation and incorporates the current fork's menu/loader updates.
Hardware testing of the combined SDK 2.3.0 build is still pending.

Software tests use only synthetic data unless local game files are explicitly
passed to the loader test. No commercial game data is included.

```sh
python -m pip install unicorn==2.1.4 pyelftools==0.32 py65==1.2.0
SRC="source/Pico VSCode Project/a8_pico_cart"
python "$SRC/tests/compiled_loader.py" build/a8_pico_cart.elf
python "$SRC/tests/compiled_bus.py" build/a8_pico_cart.elf
python "$SRC/tests/make_diagnostic.py" build/diagnostics standard
cc -I"$SRC" -I"$SRC/fatfs" "$SRC/tests/host_type42.c" \
  "$SRC/type42_image.c" "$SRC/fatfs/ff.c" "$SRC/fatfs/ffunicode.c" \
  -o build/host-type42
build/host-type42 build/diagnostics/TYPE42-BANK-TEST-V2.car
```

`compiled_loader.py` checks the actual ARM menu loader with mocked file IO,
including existing mapper routing and enabled/disabled feature builds.
`host_type42.c` tests the production image parser with real FatFs and a
fragmented simulated disk. `compiled_bus.py` executes the actual ARM bus
handler and preflight with mocked GPIO/SSI. It does not model electrical
timing. The diagnostic generator also accepts `ram` and `nodma` variants.

Always remove the cartridge from the Atari before connecting USB, and
disconnect USB before installing it in the Atari.
