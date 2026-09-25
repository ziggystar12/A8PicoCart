"""Execute the actual compiled ARM bus handler with mocked SIO/SSI registers.
This verifies logic, not RP2040 / flash / Atari cycle timing.
Requires unicorn and pyelftools; pass the linked firmware ELF path.
"""
import json
import random
import struct
import sys
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x10000000, 0x1000000)
uc.mem_map(0x20000000, 0x42000)
uc.mem_map(0x18000000, 0x1000)
uc.mem_map(0xD0000000, 0x1000)
with open(sys.argv[1], "rb") as stream:
    elf = ELFFile(stream)
    symbols = {s.name: s.entry.st_value for s in elf.get_section_by_name(".symtab").iter_symbols()}
    for segment in elf.iter_segments():
        if segment.header.p_type == "PT_LOAD" and segment.header.p_filesz:
            uc.mem_write(segment.header.p_vaddr, segment.data())

assert 0x20000000 <= symbols["emulate_type42"] < 0x20042000
file_data = bytes((i * 37 ^ i >> 8 ^ i >> 13) & 255 for i in range(1048592))
page_pointers = []
cache_slot = 0
for page in range(2049):
    if page == 0 or page >= 1794:
        pointer = symbols["cart_ram"] + cache_slot * 512
        cache_slot += 1
    elif 16 * 16 <= page <= 17 * 16:
        # One direct flash bank exercises conversion of the base table too.
        pointer = 0x10400000 + page * 512
    else:
        pointer = 0x10800000 + ((page * 1031) % 4096) * 512
    uc.mem_write(pointer, file_data[page * 512:(page + 1) * 512].ljust(512, b"\xff"))
    page_pointers.append(pointer)
uc.mem_write(symbols["type42_pages"], struct.pack("<2049I", *page_pointers))
bank_bases = []
for bank in range(128):
    first = bank * 16
    contiguous = all(page_pointers[first + j] == page_pointers[first] + j * 512 for j in range(17))
    bank_bases.append(page_pointers[first] + 16 if contiguous else 0)
uc.mem_write(symbols["type42_bank_base"], struct.pack("<128I", *bank_bases))

# Execute the production fast-flash verifier, including divider restoration
# and a deliberately corrupt uncached byte. No cycle/timing model is claimed.
uc.mem_map(0x13000000, 0x1000000)
for page, pointer in enumerate(page_pointers):
    if 0x10000000 <= pointer < 0x11000000:
        uc.mem_write(pointer + 0x03000000,
                     file_data[page * 512:(page + 1) * 512].ljust(512, b"\xff"))
uc.mem_write(symbols["type42_payload_checksum"], struct.pack("<I", sum(file_data[16:])))
bad_address = page_pointers[1] + 0x03000000
original = bytes(uc.mem_read(bad_address, 1))
for corrupt in (False, True):
    uc.mem_write(bad_address, bytes([original[0] ^ (1 if corrupt else 0)]))
    uc.mem_write(0x18000014, struct.pack("<I", 4))
    uc.reg_write(UC_ARM_REG_SP, 0x20041000)
    uc.reg_write(UC_ARM_REG_LR, 0x10000001)
    uc.emu_start(symbols["type42_verify_fast_flash"] | 1, 0x10000000, count=20000000)
    assert uc.reg_read(UC_ARM_REG_R0) == (0 if corrupt else 1), "fast flash verification"
    assert struct.unpack("<I", uc.mem_read(0x18000014, 4))[0] == 4, "menu divider not restored"
uc.mem_write(bad_address, original)

# Production menu preparation must convert both lookup tables before launch.
# Run twice to check it never offsets already-converted pointers again.
for _ in range(2):
    uc.reg_write(UC_ARM_REG_SP, 0x20041000)
    uc.reg_write(UC_ARM_REG_LR, 0x10000001)
    uc.emu_start(symbols["type42_prepare_bus"] | 1, 0x10000000, count=100000)

PHI = 1 << 22
RW = 1 << 23
CCTL = 1 << 21
S4 = 1 << 24
S5 = 1 << 25
RD5 = 1 << 27
DATA = 255 << 13
cases = []
expected_bank, enabled = 127, True

def read(address, **kwargs):
    active = enabled and not kwargs.get("write") and not kwargs.get("unselected")
    pins = CCTL | S4 | address
    if not kwargs.get("write"):
        pins |= RW
    if kwargs.get("unselected"):
        pins |= S5
    cases.append({"pins": pins, "data": file_data[16 + expected_bank * 8192 + address] if active else None,
                  "enabled": enabled, "fault": kwargs.get("fault"), "bank": expected_bank,
                  "fault_before_fetch": kwargs.get("fault_before_fetch", False)})

def control(address, write):
    global expected_bank, enabled
    expected_bank, enabled = address & 127, not bool(address & 128)
    cases.append({"pins": S4 | S5 | (0 if write else RW) | 0x1500 | address,
                  "data": None, "enabled": enabled, "control": True})

read(0x1fff)  # Type42 must start in bank 127, before any control access.
for write in (False, True):
    for bank in range(128):
        control(bank, write)
        for address in (0, 495, 496, 511, 512, 4095, 8191):
            read(address)
        read(0x123, write=True)
        read(0x123, unselected=True)
        control(bank | 128, write)
        read(0x123)  # Even a spurious S5 select must not drive while disabled.
rng = random.Random(42)
for _ in range(1024):
    control(rng.randrange(128), bool(rng.getrandbits(1)))
    read(rng.randrange(8192))
control(3, True)
read(37, fault="address")
read(38)
read(39, fault="deselect")
read(40)
read(41, fault="write")
read(42)
for bank in (3, 16, 127):  # fragmented flash, direct flash, direct SRAM
    control(bank, True)
    for fault in ("address", "deselect", "write"):
        read(0x1555, fault=fault, fault_before_fetch=True)

index = 0
steps = 0
started = False
output = 0
oe = 0
checked = 0
prefetch_drives = 0
faulted = set()
request_seen = set()
early_faults = set()
uncached_bus_reads = 0
PERIOD = 180

def pins_now():
    case = cases[index]
    phase = steps % PERIOD
    if phase >= 150:
        return CCTL | S4 | S5 | RW
    pins = case["pins"]
    if index in faulted:
        if case["fault"] == "address":
            pins = (pins & ~8191) | ((pins + 1) & 8191)
        elif case["fault"] == "deselect":
            pins |= S5
        elif case["fault"] == "write":
            pins &= ~RW
    return pins | (PHI if 50 <= phase < 150 else 0)

def hook_code(emu, address, size, user):
    global index, steps, checked
    if not started:
        return
    steps += 1
    if steps % PERIOD == 0:
        index += 1
        if index == len(cases):
            emu.emu_stop()
            return
    if steps % PERIOD == 125:
        case = cases[index]
        expected = case["data"]
        if case.get("fault") == "address":
            expected = file_data[16 + case["bank"] * 8192 + ((case["pins"] + 1) & 8191)]
        elif case.get("fault"):
            expected = None
        if expected is None:
            assert not oe & DATA, (index, "unexpected bus drive", oe, case)
        else:
            assert oe & DATA == DATA, (index, "no ROM data", case)
            assert (output >> 13) & 255 == expected, (index, "wrong ROM data", output, expected)
        assert bool(output & RD5) == case["enabled"], (index, "RD5", output, case)
        checked += 1

def hook_read(emu, access, address, size, value, user):
    global started, uncached_bus_reads
    if started:
        assert not 0x10000000 <= address < 0x11000000, "cached XIP access during bus emulation"
        if 0x13000000 <= address < 0x14000000:
            uncached_bus_reads += 1
        in_rom = 0x13000000 <= address < 0x14000000
        in_cache = symbols["cart_ram"] <= address < symbols["cart_ram"] + 131072
        if (in_rom or in_cache) and index in early_faults:
            # An address/select/write change already occurred before the load.
            # A costly load for the abandoned request must not start at all.
            case = cases[index]
            assert case["fault"] == "address", (index, "obsolete ROM fetch", case)
            offset = 16 + case["bank"] * 8192 + ((case["pins"] + 1) & 8191)
            pointer = page_pointers[offset >> 9]
            if 0x10000000 <= pointer < 0x11000000:
                pointer += 0x03000000
            assert address == pointer + (offset & 511), (index, "fetch used obsolete address", hex(address))
    if address == 0xD0000004:
        started = True
        pins = pins_now()
        if pins & (CCTL | S5 | RW) == CCTL | RW:
            request_seen.add(index)
        emu.mem_write(address, struct.pack("<I", pins))
    elif started and cases[index].get("fault") and index not in faulted:
        in_rom = 0x13000000 <= address < 0x14000000
        in_cache = symbols["cart_ram"] <= address < symbols["cart_ram"] + 131072
        if (in_rom or in_cache) and not cases[index]["fault_before_fetch"]:
            faulted.add(index)

def hook_write(emu, access, address, size, value, user):
    global output, oe, prefetch_drives
    if address == 0xD0000010:
        output = value
    elif address == 0xD0000014:
        output |= value
    elif address == 0xD0000018:
        output &= ~value
    elif address == 0xD0000028:
        oe &= ~value
        if started and index in request_seen and index not in faulted and cases[index]["fault_before_fetch"]:
            faulted.add(index)
            early_faults.add(index)
    elif address == 0xD0000024:
        oe |= value
        if started and value & DATA:
            pins = pins_now()
            assert pins & (CCTL | S5 | RW) == CCTL | RW, (index, "invalid ROM drive", hex(pins))
            expected = cases[index]["data"]
            if index in faulted:
                assert cases[index]["fault"] == "address"
                expected = file_data[16 + cases[index]["bank"] * 8192 + (pins & 8191)]
            assert (output >> 13) & 255 == expected, (index, "stale byte driven", expected, output)
            if steps % PERIOD < 50:
                prefetch_drives += 1

uc.hook_add(UC_HOOK_CODE, hook_code)
uc.hook_add(UC_HOOK_MEM_READ, hook_read)
uc.hook_add(UC_HOOK_MEM_WRITE, hook_write, begin=0xD0000000, end=0xD0000FFF)
uc.reg_write(UC_ARM_REG_SP, 0x20041000)
uc.reg_write(UC_ARM_REG_LR, 0x10000101)
uc.emu_start(symbols["emulate_type42"] | 1, 0, count=3000000)
assert checked == len(cases), (checked, len(cases))
stale = struct.unpack("<I", uc.mem_read(symbols["type42_stale_reads"], 4))[0]
assert stale == 3 and len(faulted) == 12 and len(early_faults) == 9, (stale, faulted, early_faults)
assert prefetch_drives > 500, prefetch_drives
assert uncached_bus_reads > 1000, uncached_bus_reads
def converted(pointer):
    return pointer + 0x03000000 if 0x10000000 <= pointer < 0x11000000 else pointer
assert struct.unpack("<2049I", uc.mem_read(symbols["type42_pages"], 2049 * 4)) == tuple(map(converted, page_pointers))
assert struct.unpack("<128I", uc.mem_read(symbols["type42_bank_base"], 128 * 4)) == tuple(map(converted, bank_bases))
print(json.dumps({"result": "PASS", "compiled_bus_cases": checked,
                  "fast_flash_preflight_and_corruption_rejection": True,
                  "startup_bank": 127, "all_control_addresses_read_and_write": True,
                  "rom_drives_before_phi2_high": prefetch_drives,
                  "obsolete_address_deselect_and_write_not_driven": True,
                  "bus_flash_reads_bypass_cache": uncached_bus_reads,
                  "direct_and_fragmented_pointer_conversion": True,
                  "obsolete_fetches_prevented_before_load": len(early_faults),
                  "physical_timing_validated": False}))
