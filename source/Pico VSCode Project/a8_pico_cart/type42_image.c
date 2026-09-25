#include "type42_image.h"
#include "flash_fs.h"
#include <string.h>

const uint8_t *type42_pages[TYPE42_FILE_PAGES];
const uint8_t *type42_bank_base[128];
uint32_t type42_payload_checksum;

static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

bool type42_load_image(FIL *file, uint8_t *cache_128k, char error[40]) {
    uint8_t sector[512];
    uint32_t checksum = 0, expected_checksum = 0;
    unsigned cache_page = 0;
    memset(type42_pages, 0, sizeof(type42_pages));
    memset(type42_bank_base, 0, sizeof(type42_bank_base));
    if (f_size(file) != TYPE42_ROM_BYTES + TYPE42_HEADER_BYTES) {
        strcpy(error, "Type42 needs exactly 1MB+16 bytes");
        return false;
    }
    if (f_lseek(file, 0) != FR_OK) {
        strcpy(error, "Type42 seek failed");
        return false;
    }
    for (unsigned page = 0; page < TYPE42_FILE_PAGES; ++page) {
        const unsigned count = page == TYPE42_FILE_PAGES - 1 ? 16 : 512;
        UINT got;
        /* A partial read makes FatFs set FIL.sect. A direct 512-byte read
         * bypasses that cache field, so must NOT be used to discover sectors. */
        if (f_read(file, sector, 1, &got) != FR_OK || got != 1) goto read_error;
        const uint8_t *physical = flash_fs_sector_pointer(file->sect);
        if (!physical) goto read_error;
        if (f_read(file, sector + 1, count - 1, &got) != FR_OK || got != count - 1)
            goto read_error;
        if (page == 0) {
            if (memcmp(sector, "CART", 4) || be32(sector + 4) != 42) {
                strcpy(error, "Invalid Type42 CAR header");
                goto failed;
            }
            expected_checksum = be32(sector + 8);
        }
        for (unsigned i = page == 0 ? 16 : 0; i < count; ++i)
            checksum += sector[i];
        /* Immutable SRAM cache: first file page plus the last 255 pages.
         * This keeps boot bank 127 and AGI's high ROM service banks resident.
         * No game detection, patching, flash writes, or demand-load stalls. */
        if (page == 0 || page >= TYPE42_FILE_PAGES - (TYPE42_CACHE_PAGES - 1)) {
            uint8_t *cached = cache_128k + cache_page++ * 512;
            memcpy(cached, sector, count);
            if (count < 512) memset(cached + count, 0xff, 512 - count);
            type42_pages[page] = cached;
        } else {
            type42_pages[page] = physical;
        }
    }
    if (checksum != expected_checksum) {
        strcpy(error, "Type42 CAR checksum mismatch");
        goto failed;
    }
    for (unsigned bank = 0; bank < 128; ++bank) {
        unsigned first = bank * 16;
        bool contiguous = true;
        /* The CAR header means each ROM bank touches 17 file sectors.
         * Integer comparisons avoid pointer arithmetic across allocations. */
        for (unsigned i = 1; i <= 16; ++i) {
            if ((uintptr_t)type42_pages[first + i] !=
                (uintptr_t)type42_pages[first] + i * 512)
                contiguous = false;
        }
        if (contiguous) type42_bank_base[bank] = type42_pages[first] + 16;
    }
    type42_payload_checksum = checksum;
    return true;
read_error:
    strcpy(error, "Type42 flash mapping/read failed");
failed:
    memset(type42_pages, 0, sizeof(type42_pages));
    memset(type42_bank_base, 0, sizeof(type42_bank_base));
    return false;
}
