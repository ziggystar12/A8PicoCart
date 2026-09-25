"""Build and CPU-test an original, redistributable Type42 bank diagnostic.
No AGI/game data is used. Requires py65. Pass an output directory.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
from py65.devices.mpu6502 import MPU

variant = sys.argv[2] if len(sys.argv) > 2 else "standard"
assert variant in ("standard", "ram", "nodma"), variant
ram_only = variant == "ram"
no_dma = variant == "nodma"

class Code:
    def __init__(self, origin):
        self.origin, self.data, self.labels, self.fixups = origin, bytearray(), {}, []
    def label(self, name):
        self.labels[name] = self.origin + len(self.data)
    def emit(self, *values):
        self.data.extend(values)
    def absolute(self, opcode, target):
        self.emit(opcode)
        if isinstance(target, str):
            self.fixups.append((len(self.data), target, False))
            self.emit(0, 0)
        else:
            self.data.extend(struct.pack("<H", target))
    def branch(self, opcode, target):
        self.emit(opcode)
        self.fixups.append((len(self.data), target, True))
        self.emit(0)
    def finish(self):
        for pos, target, relative in self.fixups:
            address = self.labels[target]
            if relative:
                delta = address - (self.origin + pos + 1)
                assert -128 <= delta < 128, (target, delta)
                self.data[pos] = delta & 255
            else:
                self.data[pos:pos + 2] = struct.pack("<H", address)
        return self.data

samples = [0x1000, 0x11EF, 0x11F0, 0x11FF, 0x1200, 0x1555, 0x1801, 0x1F00]
rom = bytearray((i * 37 ^ i >> 8 ^ i >> 13) & 255 for i in range(1048576))
code = Code(0x0600)
code.emit(0x78, 0xA9, 0)  # SEI, LDA #0
code.absolute(0x8D, 0xD40E)  # Disable NMI; keep ANTIC display DMA running.
code.absolute(0xAD, 0xD301)
code.emit(0x09, 2)  # Disable XL/XE BASIC before testing RAM under the cartridge.
code.absolute(0x8D, 0xD301)
code.emit(0xA9, 0, 0x85, 2, 0x85, 4, 0xA0, 0)  # status/pass count; LDY #0
if variant != "standard":
    code.emit(0x85, 14)  # high byte of completed-pass count
if no_dma:
    code.absolute(0xAD, 0x022F)  # SDMCTL shadow; D400 itself is write-only
    code.emit(0x85, 12, 0xA9, 0)
code.label("clear")
code.emit(0x91, 0x58, 0xC8, 0xC0, 160 if variant != "standard" else 120)
code.branch(0xD0, "clear")

messages = []
def print_text(label, text, row):
    encoded = bytes(ord(c) - 32 for c in text)
    messages.append((label, encoded))
    code.emit(0xA2, 0, 0xA0, row * 40)
    code.label(label + "_loop")
    code.absolute(0xBD, label)
    code.emit(0x91, 0x58, 0xE8, 0xC8, 0xE0, len(encoded))
    code.branch(0xD0, label + "_loop")

title = {"standard": "A8 TYPE42 TEST V2: RAM THEN FLASH",
         "ram": "A8 V3: RAM BANK 127 STRESS",
         "nodma": "A8 V3: FLASH / DISPLAY DMA OFF"}[variant]
print_text("title", title, 0)
code.label("repeat")
if no_dma:
    code.emit(0xA9, 0)
    code.absolute(0x8D, 0xD400)  # Blank display only during bank testing.
if ram_only:
    code.emit(0xA9, 0, 0x85, 11)
code.emit(0xA9, 1, 0x85, 10, 0xA2, 127)  # test cached bank 127 before bank 0
code.label("bank")
code.emit(0x86, 3)
for mode in range(2):
    for sample, address in enumerate(samples):
        code.emit(0xA9, (0x10 if mode == 0 else 0x20) + sample, 0x85, 5)
        code.emit(0xA9, address & 255, 0x85, 8, 0xA9, 0xA0 + (address >> 8), 0x85, 9)
        if sample == 0:
            # Keep the first ROM read immediately after the mapper access.
            code.absolute(0x9D if mode == 0 else 0xBD, 0xD500)
        code.absolute(0xAD, 0xA000 + address)
        code.emit(0x85, 6)
        code.absolute(0xBD, 0x0A00 + sample * 128)
        code.emit(0x85, 7, 0xC5, 6)
        good = f"good_{mode}_{sample}"
        code.branch(0xF0, good)
        code.absolute(0x4C, "fail")
        code.label(good)
    code.emit(0xA9, 0x30 + mode, 0x85, 5, 0xA9, 0, 0x85, 8, 0xA9, 0xA0, 0x85, 9)
    code.absolute(0x2C, 0xD580 if mode == 0 else 0xD5FF)
    if not mode:
        code.emit(0xA9, 0x5A)
        code.absolute(0x8D, 0xA000)
    code.absolute(0xAD, 0xA000)
    code.emit(0x85, 6, 0xA9, 0x5A, 0x85, 7, 0xC5, 6)
    code.branch(0xF0, f"disabled_{mode}")
    code.absolute(0x4C, "fail")
    code.label(f"disabled_{mode}")
if ram_only:
    code.emit(0xE6, 11, 0xA5, 11, 0xC9, 128)
    code.branch(0xF0, "pass")
    code.emit(0xA2, 127)
    code.absolute(0x4C, "bank")
else:
    code.emit(0xA5, 10)
    code.branch(0xF0, "next_bank")
    code.emit(0xA9, 0, 0x85, 10, 0xA2, 0)
    code.absolute(0x4C, "bank")
    code.label("next_bank")
    code.emit(0xE8, 0xE0, 128)
    code.branch(0xF0, "pass")
    code.absolute(0x4C, "bank")
code.label("pass")
if no_dma:
    code.emit(0xA5, 12)
    code.absolute(0x8D, 0xD400)
code.emit(0xA9, 0xC4)
code.absolute(0x8D, 0xD018)  # green graphics-0 background
passed = "PASS - 128 BANKS - REPEATING" if not ram_only else "PASS - RAM 127 - REPEATING"
print_text("passed", passed, 1)
code.emit(0xE6, 4)
if variant != "standard":
    code.branch(0xD0, "count_done")
    code.emit(0xE6, 14)
    code.label("count_done")
    print_text("count_text", "PASSES $", 3)
    code.emit(0xA5, 14)
    code.absolute(0x20, "print_hex")
    code.emit(0xA5, 4)
    code.absolute(0x20, "print_hex")
code.emit(0xA9, 0x80, 0x85, 2)
if no_dma:
    # About one second with the display restored between test bursts.
    # This delay is diagnostic-only RAM code, never added to game cartridges.
    code.emit(0xA9, 5, 0x85, 13, 0xA0, 0, 0xA2, 0)
    code.label("display_delay")
    code.emit(0xCA)
    code.branch(0xD0, "display_delay")
    code.emit(0x88)
    code.branch(0xD0, "display_delay")
    code.emit(0xC6, 13)
    code.branch(0xD0, "display_delay")
code.absolute(0x4C, "repeat")
code.label("fail")
if no_dma:
    code.emit(0xA5, 12)
    code.absolute(0x8D, 0xD400)
if variant != "standard":
    code.emit(0xA9, 0, 0xA0, 40)
    code.label("clear_status")
    code.emit(0x91, 0x58, 0xC8, 0xC0, 80)
    code.branch(0xD0, "clear_status")
code.emit(0xA9, 0x34)
code.absolute(0x8D, 0xD018)  # red graphics-0 background
print_text("failed", "FAIL - BANK $", 1)
code.emit(0xA5, 3)
code.absolute(0x20, "print_hex")
text = "ST $00 AT $0000 GOT $00 EXP $00"
print_text("detail", text, 2)
for variable, position in [(5, text.index("00")), (9, text.index("0000")),
                           (8, text.index("0000") + 2), (6, text.index("GOT $") + 5),
                           (7, text.index("EXP $") + 5)]:
    code.emit(0xA0, 80 + position, 0xA5, variable)
    code.absolute(0x20, "print_hex")
code.emit(0xA9, 0xFF, 0x85, 2)
code.label("halt")
code.absolute(0x4C, "halt")
code.label("print_hex")
code.emit(0x48, 0x4A, 0x4A, 0x4A, 0x4A, 0xAA)
code.absolute(0xBD, "hex")
code.emit(0x91, 0x58, 0xC8, 0x68, 0x29, 15, 0xAA)
code.absolute(0xBD, "hex")
code.emit(0x91, 0x58, 0xC8, 0x60)
for label, content in messages:
    code.label(label)
    code.data.extend(content)
code.label("hex")
code.data.extend(ord(c) - 32 for c in "0123456789ABCDEF")
program = code.finish()
assert len(program) <= 0x400, len(program)
blob = bytearray(0x800)
blob[:len(program)] = program
for sample, address in enumerate(samples):
    for bank in range(128):
        blob[0x400 + sample * 128 + bank] = rom[bank * 8192 + address]

boot = Code(0xA010)
boot.emit(0xA2, 0)
boot.label("copy")
for page in range(8):
    boot.absolute(0xBD, 0xA100 + page * 256)
    boot.absolute(0x9D, 0x0600 + page * 256)
boot.emit(0xE8)
boot.branch(0xD0, "copy")
boot.absolute(0x4C, 0x0600)
last = 127 * 8192
rom[last] = 0x60  # CARTINIT: RTS
rom[last + 0x10:last + 0x10 + len(boot.data)] = boot.finish()
rom[last + 0x100:last + 0x900] = blob
rom[last + 0x1FFA:last + 0x2000] = struct.pack("<HBBH", 0xA010, 0, 4, 0xA000)

class CartMemory:
    def __init__(self, corrupt=False, stuck_enabled=False, dma_sensitive=False):
        self.ram, self.bank, self.enabled = bytearray(65536), 127, True
        self.ram[0x58:0x5A] = bytes([0, 0x90])
        self.ram[0x022F] = self.ram[0xD400] = 0x22
        self.sample_reads = []
        self.dma_sensitive = dma_sensitive
        self.corrupt, self.stuck_enabled = corrupt, stuck_enabled
    def __getitem__(self, address):
        if 0xD500 <= address <= 0xD5FF:
            self.bank, self.enabled = address & 127, (self.stuck_enabled or not bool(address & 128))
            return 255
        if 0xA000 <= address <= 0xBFFF and self.enabled:
            if address - 0xA000 in samples:
                self.sample_reads.append((self.bank, bool(self.ram[0xD400] & 0x20)))
            value = rom[self.bank * 8192 + address - 0xA000]
            bad_bank = 127 if ram_only else 37
            bad = self.corrupt and self.bank == bad_bank and address == 0xB1F0
            bad |= self.dma_sensitive and bool(self.ram[0xD400] & 0x20) and address == 0xB555
            return value ^ (1 if bad else 0)
        if 0xA000 <= address <= 0xBFFF and not self.enabled and not (self.ram[0xD301] & 2):
            return 0xA5  # BASIC visible until explicitly disabled.
        return self.ram[address]
    def __setitem__(self, address, value):
        if 0xD500 <= address <= 0xD5FF:
            self.bank, self.enabled = address & 127, (self.stuck_enabled or not bool(address & 128))
        elif not (0xA000 <= address <= 0xBFFF and self.enabled):
            self.ram[address] = value

for corrupt, stuck, dma_sensitive in [(False, False, False), (True, False, False),
                                       (False, True, False), (False, False, True)]:
    mem = CartMemory(corrupt, stuck, dma_sensitive)
    cpu = MPU(memory=mem, pc=0xA010)
    for step in range(200000):
        cpu.step()
        if mem.ram[2]:
            break
    should_fail = corrupt or stuck or (dma_sensitive and not no_dma)
    assert mem.ram[2] == (255 if should_fail else 128), (variant, corrupt, step, mem.ram[2])
    if variant != "standard":
        status_row = ''.join(chr(c + 32) for c in mem.ram[0x9028:0x9050])
        if should_fail:
            assert status_row.rstrip() == f"FAIL - BANK ${mem.ram[3]:02X}"
            detail_row = ''.join(chr(c + 32) for c in mem.ram[0x9050:0x9078])
            expected_detail = (f"ST ${mem.ram[5]:02X} AT ${mem.ram[9]:02X}{mem.ram[8]:02X} "
                               f"GOT ${mem.ram[6]:02X} EXP ${mem.ram[7]:02X}")
            assert detail_row.rstrip() == expected_detail
        else:
            assert status_row.rstrip() == passed
            count_row = ''.join(chr(c + 32) for c in mem.ram[0x9078:0x90A0])
            assert count_row.rstrip() == "PASSES $0001"
    if corrupt:
        assert mem.ram[3] == (127 if ram_only else 37) and mem.ram[5] == 0x12
    if stuck:
        assert mem.ram[3] == 127 and mem.ram[5] == 0x30
    if ram_only:
        assert {bank for bank, _ in mem.sample_reads} == {127}
    if no_dma:
        assert not any(dma for _, dma in mem.sample_reads)
        assert mem.ram[0xD400] == 0x22, "Display not restored on pass/fail"
    if variant != "standard" and not should_fail:
        assert mem.ram[4] == 1
        # Verify that the diagnostic repeats and restores the display again.
        for step in range(2000000):
            cpu.step()
            if mem.ram[4] == 2 and mem.ram[0xD400] == 0x22:
                break
        assert mem.ram[4] == 2 and mem.ram[2] == 128

image = b"CART" + struct.pack(">III", 42, sum(rom) & 0xFFFFFFFF, 0) + rom
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
target = out / {"standard": "TYPE42-BANK-TEST-V2.car",
                "ram": "TYPE42-RAM-STRESS-V3.car",
                "nodma": "TYPE42-FLASH-NODMA-V3.car"}[variant]
target.write_bytes(image)
print(json.dumps({"result": "PASS", "diagnostic": target.name, "banks": 1 if ram_only else 128,
                  "variant": variant,
                  "tests": "6502 pass, corrupt read, stuck mapper, BASIC underlay, DMA-sensitive fault",
                  "size": len(image), "sha256": hashlib.sha256(image).hexdigest(),
                  "physical_display_tested": False}))
