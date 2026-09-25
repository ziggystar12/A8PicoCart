/* v0.4: select-driven, SRAM-resident Type42 handler. No calls or flash-resident
 * instructions in the bus loop. Does not insert Atari wait states or patch ROM.
 * r7: 0 = disabled, 1 = fragmented bank, otherwise direct ROM/cache pointer.
 * Control writes/reads both latch the ADDRESS; D580-D5FF disables RD5.
 */
.syntax unified
.cpu cortex-m0plus
.thumb
.section .time_critical.type42_bus_loop,"ax",%progbits
.global type42_bus_loop
.type type42_bus_loop, %function
.thumb_func
type42_bus_loop:
    mov r11, r0                 /* all file-page pointers */
    mov r10, r1                 /* direct-bank pointer table */
    ldr r2, =8128               /* bank 127 * 16 pages * 4 */
    adds r0, r0, r2
    mov r8, r0                  /* active bank's page pointers */
    ldr r2, =508
    ldr r7, [r1, r2]            /* standard Type42 starts in bank 127 */
    cmp r7, #0
    bne 1f
    movs r7, #1
1:
    ldr r4, =0xd0000000         /* SIO */
    ldr r5, =0x001fe000         /* data GPIO mask */
    ldr r6, =0x02a01fff         /* address + S5/RW/CCTL validity mask */
    ldr r0, =0x08000000         /* RD5 */
    mov r9, r0
    ldr r0, =0x02a00000         /* S5 | R/W | CCTL */
    mov r12, r0
    ldr r0, =0x00a00000         /* read = S5 low, R/W and CCTL high */
    mov lr, r0

.Lpoll:
    ldr r0, [r4, #4]
.Ldecode:
    mov r1, r12
    ands r1, r0
    cmp r1, lr
    bne .Lcontrol_or_idle
    cmp r7, #0
    beq .Lrelease
    /* Like upstream's standard-8K handler, begin on S5, before PHI2 high
     * when select/address are already valid. Never wait for the clock to
     * begin a cold flash fetch. Release old data while a new fetch is pending. */
    str r5, [r4, #40]
    mov r3, r0
    lsls r0, r0, #19
    lsrs r0, r0, #19            /* low 13 address bits */
    cmp r7, #1
    beq .Lfragmented
    /* Do not begin a blocking flash read for a request which changed while
     * decoding it. The post-read check below still handles changes in flight.
     * Leave the data pins released and immediately sample the new request. */
    ldr r2, [r4, #4]
    eors r2, r3
    tst r2, r6
    bne .Lpoll
    ldrb r0, [r7, r0]
.Ldrive:
    ldr r1, [r4, #4]
    eors r1, r3
    tst r1, r6
    bne .Lstale                 /* don't drive a byte for an obsolete request */
    lsls r0, r0, #13
    mov r1, r9
    orrs r0, r1
    str r0, [r4, #16]           /* data latches and RD5 */
    str r5, [r4, #36]           /* data output enable */
.Lhold:
    ldr r0, [r4, #4]
    mov r1, r0
    eors r1, r3
    tst r1, r6
    beq .Lhold                  /* keep output while address/select stay valid */
    b .Ldecode
.Lrelease:
    str r5, [r4, #40]           /* release all data pins */
    b .Lpoll

.Lfragmented:
    adds r0, #16               /* preserve CAR-header displacement */
    lsrs r1, r0, #9
    lsls r1, r1, #2
    mov r2, r8
    ldr r1, [r2, r1]
    lsls r0, r0, #23
    lsrs r0, r0, #23
    ldr r2, [r4, #4]
    eors r2, r3
    tst r2, r6
    bne .Lpoll
    ldrb r0, [r1, r0]
    b .Ldrive

.Lcontrol_or_idle:
    str r5, [r4, #40]
    lsls r1, r0, #10           /* CCTL bit 21 into sign */
    bmi .Lpoll                 /* not a D5xx access */
    lsls r1, r0, #9            /* latch D5xx only while PHI2 high */
    bpl .Lpoll
    lsls r1, r0, #24           /* low-address bit 7 disables */
    bmi .Ldisable
    movs r1, #127
    ands r0, r1
    lsls r1, r0, #6
    add r1, r11
    mov r8, r1
    lsls r0, r0, #2
    mov r1, r10
    ldr r7, [r1, r0]
    cmp r7, #0
    bne .Lenable
    movs r7, #1
.Lenable:
    mov r0, r9
    str r0, [r4, #20]
    b .Lcontrol_end
.Ldisable:
    movs r7, #0
    mov r0, r9
    str r0, [r4, #24]
.Lcontrol_end:
    ldr r0, [r4, #4]
    lsls r1, r0, #9
    bmi .Lcontrol_end
    b .Ldecode
.Lstale:
    str r5, [r4, #40]
    ldr r1, =type42_stale_reads
    ldr r0, [r1]
    adds r0, #1
    str r0, [r1]
    b .Lpoll
    .balign 4
    .ltorg
.size type42_bus_loop, .-type42_bus_loop
