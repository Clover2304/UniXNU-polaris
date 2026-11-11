// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>
// DWC3 TX RAM depth is measured in master-bus words. Single-port RAM shares
// space with RX, so retain the firmware's first TX address in that layout.
static inline bool polaris_usb_fifos(uint32_t h0,uint32_t h1,uint32_t h6,uint32_t h7,uint32_t fifo0,uint32_t (&out)[3]) {
    unsigned bits=(h0>>8)&255u;
    if(bits!=32 && bits!=64 && bits!=128 && bits!=256)return false;
    unsigned bytes=bits/8;
    bool single=(h1>>23)&1;
    unsigned address=single?(fifo0>>16):0,limit=single?(h6>>16):(h7&65535u);
    unsigned words[3]={(512+bytes-1)/bytes,(2048+bytes-1)/bytes,(128+bytes-1)/bytes};
    unsigned total=words[0]+words[1]+words[2];
    if(address>limit || total>limit-address)return false;
    for(unsigned i=0;i<3;i++){out[i]=(address<<16)|words[i];address+=words[i];}
    return true;
}
