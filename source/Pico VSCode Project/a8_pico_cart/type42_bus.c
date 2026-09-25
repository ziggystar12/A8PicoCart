#include "pico/stdlib.h"
#include "hardware/sync.h"
#include "hardware/structs/ssi.h"
#include "hardware/regs/ssi.h"
#include "type42_image.h"

#ifndef A8_TYPE42_FLASH_DIVIDER
#define A8_TYPE42_FLASH_DIVIDER 2
#endif
#if A8_TYPE42_FLASH_DIVIDER != 2 && A8_TYPE42_FLASH_DIVIDER != 4
#error A8_TYPE42_FLASH_DIVIDER must be 2 or 4
#endif

#define DATA_MASK 0x001fe000u
#define CCTL_MASK 0x00200000u
#define PHI2_MASK 0x00400000u
#define RW_MASK   0x00800000u
#define S5_MASK   0x02000000u
#define RD4_MASK  0x04000000u
#define RD5_MASK  0x08000000u

/* Debugger-visible count of reads discarded because address/select changed.
 * Neither zero nor nonzero establishes electrical timing acceptance. */
volatile uint32_t type42_stale_reads;
void type42_bus_loop(const uint8_t **pages, const uint8_t **bases) __attribute__((noreturn));

/* Before the menu launches the cart, test the actual game's uncached flash
 * bytes at the experimental speed. The Atari is still in its existing menu
 * RAM polling stub here. Restore the menu divider before returning. This
 * separates flash data integrity from the still-unproven bus deadline. */
bool __not_in_flash_func(type42_verify_fast_flash)(void) {
    uint32_t irq = save_and_disable_interrupts();
    uint32_t divider = ssi_hw->baudr;
    while (ssi_hw->sr & SSI_SR_BUSY_BITS) { }
    ssi_hw->ssienr = 0;
    ssi_hw->baudr = A8_TYPE42_FLASH_DIVIDER;
    ssi_hw->ssienr = 1;
    __dsb();
    uint32_t checksum = 0;
    for (unsigned page = 0; page < TYPE42_FILE_PAGES; ++page) {
        uintptr_t address = (uintptr_t)type42_pages[page];
        if (address >= 0x10000000u && address < 0x11000000u)
            address += 0x03000000u; /* RP2040 uncached, nonallocating XIP alias */
        const volatile uint8_t *bytes = (const volatile uint8_t *)address;
        unsigned end = page == TYPE42_FILE_PAGES - 1 ? 16 : 512;
        for (unsigned i = page == 0 ? 16 : 0; i < end; ++i)
            checksum += bytes[i];
    }
    while (ssi_hw->sr & SSI_SR_BUSY_BITS) { }
    ssi_hw->ssienr = 0;
    ssi_hw->baudr = divider;
    ssi_hw->ssienr = 1;
    __dsb();
    __isb();
    restore_interrupts(irq);
    return checksum == type42_payload_checksum;
}

void type42_prepare_bus(void) {
    /* v0.3: bypass XIP cache for bus reads, just as the preflight does.
     * A normal cache miss allocates an eight-byte line; the no-cache,
     * no-allocation alias avoids that refill and cache-history dependence.
     * Convert while the menu is still polling, before launch. Leave
     * the immutable SRAM cache and NULL fragmented-bank markers untouched.
     * The loader rebuilds both tables whenever another image is loaded. */
    for (unsigned page = 0; page < TYPE42_FILE_PAGES; ++page) {
        uintptr_t address = (uintptr_t)type42_pages[page];
        if (address >= 0x10000000u && address < 0x11000000u)
            type42_pages[page] = (const uint8_t *)(address + 0x03000000u);
    }
    for (unsigned bank = 0; bank < 128; ++bank) {
        uintptr_t address = (uintptr_t)type42_bank_base[bank];
        if (address >= 0x10000000u && address < 0x11000000u)
            type42_bank_base[bank] = (const uint8_t *)(address + 0x03000000u);
    }
}

void __not_in_flash_func(emulate_type42)(void) {
    save_and_disable_interrupts();
    sio_hw->gpio_oe_clr = DATA_MASK;
    sio_hw->gpio_clr = RD4_MASK;
    sio_hw->gpio_set = RD5_MASK;
    /* Menu, USB and all other cartridge types retain upstream divider 4.
     * Only this RAM-resident handler switches to the experimental fast rate.
     * At the upstream 250MHz system clock: divider 2 = 125MHz QSPI. */
    while (ssi_hw->sr & SSI_SR_BUSY_BITS) { }
    ssi_hw->ssienr = 0;
    ssi_hw->baudr = A8_TYPE42_FLASH_DIVIDER;
    ssi_hw->ssienr = 1;
    __dsb();
    __isb();
    type42_bus_loop(type42_pages, type42_bank_base);
}
