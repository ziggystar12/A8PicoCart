"""Encode a linked RP2040 flash binary as standard family-tagged UF2 blocks."""
import pathlib
import struct
import sys

data = pathlib.Path(sys.argv[1]).read_bytes()
# The upstream filesystem begins at flash +1MiB. Never package blocks there.
if not data or len(data) > 1024 * 1024:
    raise SystemExit("Firmware must fit entirely below the existing filesystem")
count = (len(data) + 255) // 256
with pathlib.Path(sys.argv[2]).open("wb") as out:
    for index in range(count):
        block = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000,
                            0x10000000 + index * 256, 256, index, count, 0xE48BFF56)
        block += data[index * 256:(index + 1) * 256].ljust(256, b"\0")
        block += bytes(220) + struct.pack("<I", 0x0AB16F30)
        out.write(block)
