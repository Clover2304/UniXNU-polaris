/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef POLARIS_RUNTIME_H
#define POLARIS_RUNTIME_H
typedef unsigned long u64;
typedef long s64;
typedef unsigned char u8;
struct reply { s64 value, second; u64 flags; };
void *memset(void *p,int c,u64 n) { u8*b=p;for(u64 i=0;i<n;i++)b[i]=(u8)c;return p; }
void *memcpy(void *p,const void*q,u64 n) { u8*a=p;const u8*b=q;for(u64 i=0;i<n;i++)a[i]=b[i];return p; }
static inline int same(const void*p,const void*q,u64 n) { const u8*a=p,*b=q;for(u64 i=0;i<n;i++)if(a[i]!=b[i])return 0;return 1; }
static inline __attribute__((always_inline)) struct reply
sc(s64 number,s64 a,s64 b,s64 c,s64 d,s64 e,s64 f)
{
 register s64 x0 __asm__("x0")=a,x1 __asm__("x1")=b,x2 __asm__("x2")=c,
              x3 __asm__("x3")=d,x4 __asm__("x4")=e,x5 __asm__("x5")=f,
              x16 __asm__("x16")=number;
 u64 flags;
 __asm__ volatile("svc #0x80\n\tmrs %7, nzcv"
   : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3),"+r"(x4),"+r"(x5),"+r"(x16),"=r"(flags)
   : : "memory","cc");
 return (struct reply){x0,x1,flags};
}
#define CALL(n,a,b,c,d) sc(n,(s64)(a),(s64)(b),(s64)(c),(s64)(d),0,0)
#define ERROR(r) ((r).flags&(1UL<<29))
#define POLARIS_ENTRY(target) \
 __attribute__((naked,noreturn)) void start(void) { \
 __asm__ volatile("mov x0, sp\n\tand x9, x0, #0xfffffffffffffff0\n\tmov sp, x9\n\tb _" #target); }
static inline void print_number(u64 value)
{
 char digits[24];u64 n=sizeof(digits);
 do { digits[--n]=(char)('0'+value%10);value/=10; }while(value&&n);
 (void)CALL(4,1,digits+n,sizeof(digits)-n,0);
}
#define PROBE_MESSAGE "POLARIS MULTIFILE ROOT\n"
#define CHILD_MARKER "POLARIS CHILD RAM WRITE\n"
#define CHILD_REPORT_MAGIC 0x504f4c4152495333UL
struct child_report { u64 magic,pid,ppid,initial_sp,c_stack_sp,checks; };
#endif
