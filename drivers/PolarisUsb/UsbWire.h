// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pure protocol helpers for MIX 2S USB CDC-ACM diagnostic terminal:
//   1. Driverless Windows 10/11 usbser.sys CDC-ACM USB descriptors (HS 512B / FS 64B)
//   2. IEEE 802.3 CRC-32 matching Python binascii.crc32
//   3. GSC1 RGB565 frame packet header builder matching UniXNU scripts/debug_screen.py

#ifndef POLARIS_USB_WIRE_H
#define POLARIS_USB_WIRE_H

#include <stddef.h>
#include <stdint.h>

// USB VID/PID: 0x1209:0x0001 (pid.codes test PID), Class 0x02/0x02 binds usbser.sys automatically.
static const uint8_t kUsbCdcDeviceDesc[18] = {
    18, 0x01, 0x00, 0x02,       // bLength, DEVICE, USB 2.00
    0x02, 0x02, 0x00, 64,       // CDC class/ACM subclass, bMaxPacketSize0 = 64
    0x09, 0x12, 0x01, 0x00,     // idVendor = 0x1209, idProduct = 0x0001
    0x00, 0x01,                 // bcdDevice = 1.00
    1, 2, 3, 1                  // iManufacturer, iProduct, iSerialNumber, bNumConfigurations
};

// Build 67-byte CDC-ACM Configuration Descriptor for either High-Speed (bulkMps=512) or Full-Speed (bulkMps=64).
// Endpoint mapping matches DWC3 physical endpoints:
//   0x82 -> dep5 (EP2 IN, Interrupt, 16 bytes, FIFO 2)
//   0x01 -> dep2 (EP1 OUT, Bulk, 512/64 bytes, FIFO 0)
//   0x81 -> dep3 (EP1 IN, Bulk, 512/64 bytes, FIFO 1)
static inline unsigned usb_cdc_build_config_desc(uint8_t *out, uint16_t bulkMps) {
    const uint8_t mpsLo = (uint8_t)(bulkMps & 0xFFu);
    const uint8_t mpsHi = (uint8_t)((bulkMps >> 8) & 0xFFu);
    const uint8_t tpl[67] = {
        9, 0x02, 67, 0, 2, 1, 0, 0x80, 0xFA,          // Config 1, 2 interfaces, bus-powered 500mA
        9, 0x04, 0, 0, 1, 0x02, 0x02, 0x01, 0,        // Interface 0: CDC Control, ACM, ATv250
        5, 0x24, 0x00, 0x10, 0x01,                    // CDC Header 1.10
        5, 0x24, 0x01, 0x00, 0x01,                    // Call Management -> Data Interface 1
        4, 0x24, 0x02, 0x02,                          // ACM Capabilities (Line Coding + Control Line State)
        5, 0x24, 0x06, 0x00, 0x01,                    // Union: Control 0, Data 1
        7, 0x05, 0x82, 0x03, 16, 0, 9,                // EP2 IN (0x82), Interrupt, 16B, bInterval=9 (32ms HS)
        9, 0x04, 1, 0, 2, 0x0A, 0x00, 0x00, 0,        // Interface 1: CDC Data
        7, 0x05, 0x01, 0x02, mpsLo, mpsHi, 0,         // EP1 OUT (0x01 -> dep2), Bulk
        7, 0x05, 0x81, 0x02, mpsLo, mpsHi, 0          // EP1 IN  (0x81 -> dep3), Bulk
    };
    for (unsigned i = 0; i < 67; ++i) out[i] = tpl[i];
    return 67;
}


#endif
