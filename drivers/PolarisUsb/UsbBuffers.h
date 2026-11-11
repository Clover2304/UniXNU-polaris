// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>
#include <stddef.h>

// Caller supplies synchronization. Head and tail count bytes since reset.
template<size_t Capacity> struct PolarisBytes {
    uint8_t data[Capacity]{};
    uint64_t head=0,tail=0,dropped=0;
    size_t available() const {return static_cast<size_t>(head-tail);}
    bool append(const void *input,size_t length) {
        if(length>Capacity-available())return false;
        const auto *p=static_cast<const uint8_t *>(input);
        for(size_t i=0;i<length;i++)data[(head+i)%Capacity]=p[i];
        head+=length;return true;
    }
    void capture(uint8_t c) {
        if(available()==Capacity){tail++;dropped++;}
        data[head++%Capacity]=c;
    }
    size_t peek(void *output,size_t capacity) const {
        size_t count=available()<capacity?available():capacity;
        auto *p=static_cast<uint8_t *>(output);
        for(size_t i=0;i<count;i++)p[i]=data[(tail+i)%Capacity];
        return count;
    }
    void consume(size_t count) {if(count<=available())tail+=count;}
};
struct PolarisLine {
    enum Result {More,Ready,TooLong};
    char data[256]{};unsigned count=0;bool overflow=false;
    Result feed(char c,char (&out)[256]) {
        if(c=='\n'||c=='\r') {
            if(overflow){overflow=false;count=0;return TooLong;}
            if(!count)return More;
            for(unsigned i=0;i<count;i++)out[i]=data[i];out[count]=0;count=0;return Ready;
        }
        if(c<0x20 || c>0x7e){overflow=true;return More;}
        if(count+1>=sizeof(data)){overflow=true;return More;}
        if(!overflow)data[count++]=c;
        return More;
    }
};
