"""Exercise compiled load_file routing with synthetic files and mocked FatFs IO.

The real FatFs/image parser is covered separately by host_type42.c. This test
catches integration mistakes such as internal mapper-ID collisions and failure
to run the Type42 preflight. Pass the firmware ELF (feature enabled or disabled).
"""
import json
import struct
import sys
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x10000000, 0x100000)
uc.mem_map(0x20000000, 0x42000)
with open(sys.argv[1], "rb") as stream:
    elf = ELFFile(stream)
    symbols = {s.name: s.entry.st_value for s in elf.get_section_by_name(".symtab").iter_symbols()}
    for segment in elf.iter_segments():
        if segment.header.p_type == "PT_LOAD" and segment.header.p_filesz:
            uc.mem_write(segment.header.p_vaddr, segment.data())

enabled = "type42_load_image" in symbols
mock_names = ("f_mount", "f_open", "f_close", "f_read", "type42_load_image",
              "type42_verify_fast_flash", "type42_prepare_bus", "__wrap_memcpy", "__wrap_memset")
mock_addresses = {symbols[name] & ~1: name for name in mock_names if name in symbols}
state = {}

def hook(emu, address, size, user):
    name = mock_addresses.get(address)
    if not name:
        return
    state["calls"].append(name)
    result = 0
    if name == "f_read":
        amount = min(emu.reg_read(UC_ARM_REG_R2), len(state["file"]) - state["offset"])
        if amount:
            emu.mem_write(emu.reg_read(UC_ARM_REG_R1), state["file"][state["offset"]:state["offset"] + amount])
        emu.mem_write(emu.reg_read(UC_ARM_REG_R3), struct.pack("<I", amount))
        state["offset"] += amount
    elif name == "type42_load_image":
        result = int(state["image_ok"])
    elif name == "type42_verify_fast_flash":
        result = int(state["flash_ok"])
    elif name in ("__wrap_memcpy", "__wrap_memset"):
        destination = emu.reg_read(UC_ARM_REG_R0)
        source = emu.reg_read(UC_ARM_REG_R1)
        count = emu.reg_read(UC_ARM_REG_R2)
        data = bytes(emu.mem_read(source, count)) if name.endswith("memcpy") else bytes([source & 255]) * count
        emu.mem_write(destination, data)
        result = destination
    emu.reg_write(UC_ARM_REG_R0, result)
    emu.reg_write(UC_ARM_REG_PC, emu.reg_read(UC_ARM_REG_LR))

uc.hook_add(UC_HOOK_CODE, hook)
cases = 0

def check(car_type, payload_size, expected, image_ok=True, flash_ok=True):
    global cases, state
    state = {"file": b"CART" + struct.pack(">III", car_type, 0, 0) + bytes(payload_size),
             "offset": 0, "calls": [], "image_ok": image_ok, "flash_ok": flash_ok}
    # Use unused scratch-X memory for the name, outside the actual stack.
    uc.mem_write(0x20040000, b"TEST.CAR\0")
    uc.reg_write(UC_ARM_REG_R0, 0x20040000)
    uc.reg_write(UC_ARM_REG_SP, 0x20042000)
    uc.reg_write(UC_ARM_REG_LR, 0x10000001)
    uc.emu_start(symbols["load_file"] | 1, 0x10000000, count=1000000)
    assert uc.reg_read(UC_ARM_REG_R0) == expected, (car_type, expected, uc.reg_read(UC_ARM_REG_R0))
    calls = state["calls"]
    assert "f_close" in calls
    assert calls[-1] == "f_mount"  # unmount before returning to the menu
    if car_type == 42 and enabled:
        assert calls.count("type42_load_image") == 1
        assert calls.count("type42_verify_fast_flash") == int(image_ok)
        assert calls.count("type42_prepare_bus") == int(image_ok and flash_ok)
    else:
        assert not any(name.startswith("type42_") for name in calls)
    cases += 1

for kind, size, mapper in [(1,8192,1), (41,131072,14), (22,65536,15),
                           (69,32768,34), (78,8192,35), (53,8192,37),
                           (104,8192,38), (105,16384,39), (106,32768,40),
                           (107,65536,41), (108,131072,42), (160,65536,36),
                           (252,16384,252), (253,8192,253)]:
    check(kind, size, mapper)
check(42, 1048576, 43 if enabled else 0)
check(0x1000002A, 1048576, 0)
check(61, 2097152, 0)
if enabled:
    check(42, 1048576, 0, image_ok=False)
    check(42, 1048576, 0, flash_ok=False)
print(json.dumps({"result": "PASS", "type42_enabled": enabled, "compiled_loader_cases": cases}))
