// SPDX-License-Identifier: BSD-2-Clause-Patent
// Own static PID 1: real Darwin syscalls; no dynamic linker or device access.
.text
.p2align 2
.globl _start, _checkpoint, _reboot_return
_start:
  mov x16, #20 // getpid
  svc #0x80
  adrp x19, _results@PAGE
  add x19, x19, _results@PAGEOFF
  str x0, [x19]
  mrs x9, nzcv
  str x9, [x19, #16]
  adrp x0, Lmib@PAGE
  add x0, x0, Lmib@PAGEOFF
  mov x1, #2
  adrp x2, _user_page_size@PAGE
  add x2, x2, _user_page_size@PAGEOFF
  adrp x3, _old_length@PAGE
  add x3, x3, _old_length@PAGEOFF
  mov x4, #0
  mov x5, #0
  mov x16, #202 // __sysctl: CTL_HW/HW_PAGESIZE
  svc #0x80
  str x0, [x19, #8]
  mrs x9, nzcv
  str x9, [x19, #24]
_checkpoint:
  ldr x8, [x19]
  cmp x8, #1
  b.ne Lfailed
  ldr x8, [x19, #8]
  cbnz x8, Lfailed
  ldr x8, [x19, #16]
  tbnz x8, #29, Lfailed
  ldr x8, [x19, #24]
  tbnz x8, #29, Lfailed
  adrp x8, _user_page_size@PAGE
  ldr x8, [x8, _user_page_size@PAGEOFF]
  cmp x8, #1, lsl #12
  b.ne Lfailed
  adrp x8, _old_length@PAGE
  ldr x8, [x8, _old_length@PAGEOFF]
  cmp x8, #4
  b.ne Lfailed
  adrp x1, Lok@PAGE
  add x1, x1, Lok@PAGEOFF
  b Lreport
Lfailed:
  adrp x1, Lerror@PAGE
  add x1, x1, Lerror@PAGEOFF
Lreport:
  mov x0, #0xc00 // RB_PANIC | RB_QUICK: normal development-kernel diagnostic
  mov x16, #55 // reboot(opt, message)
  svc #0x80
_reboot_return:
  b _reboot_return // Any return is a failed test; never request a plain reboot.
.data
.p2align 3
.globl _results, _user_page_size, _old_length
_results:
  .quad 0, 0, 0, 0
Lmib:
  .word 6, 7
_user_page_size:
  .quad 0
_old_length:
  .quad 8
Lok:
  .asciz "POLARIS_INIT_OK pid=1 pagesize=4096"
Lerror:
  .asciz "POLARIS_INIT_FAILED"
