#ifndef A8_TYPE42_IMAGE_H
#define A8_TYPE42_IMAGE_H

#include <stdbool.h>
#include <stdint.h>
#include "ff.h"

#define TYPE42_ROM_BYTES (1024u * 1024u)
#define TYPE42_HEADER_BYTES 16u
#define TYPE42_FILE_PAGES 2049u
#define TYPE42_CACHE_PAGES 256u
#define TYPE42_INITIAL_BANK 127u

/* File-sector pointers include the 16-byte CAR header. This preserves every
 * original byte and works with fragmented FAT files / flash-sector mappings. */
extern const uint8_t *type42_pages[TYPE42_FILE_PAGES];
extern const uint8_t *type42_bank_base[128];
extern uint32_t type42_payload_checksum;
bool type42_load_image(FIL *file, uint8_t *cache_128k, char error[40]);
bool type42_verify_fast_flash(void);
void type42_prepare_bus(void);
void emulate_type42(void);

#endif
