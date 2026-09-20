#pragma once

// Direct USB access for the PTP backend.
//
// The Sony SDK hands USB arbitration to Sony's adapter, which loses races with
// Apple's ptpcamerad. Here we take the still-image interface ourselves and keep
// it for the whole session, the way Capture One does.

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

enum UsbDirection {
    kUsbOut = 0,
    kUsbIn = 1,
};

enum UsbTransferType {
    kUsbControl = 0,
    kUsbIsoc = 1,
    kUsbBulk = 2,
    kUsbInterrupt = 3,
};

struct UsbEndpoint {
    uint8_t pipe_ref = 0;
    uint8_t number = 0;
    uint8_t direction = 0;
    uint8_t type = 0;
    uint16_t max_packet = 0;
};

struct UsbInterfaceInfo {
    uint8_t number = 0;
    uint8_t interface_class = 0;
    uint8_t interface_subclass = 0;
    uint8_t interface_protocol = 0;
    std::vector<UsbEndpoint> endpoints;
};

struct UsbDeviceInfo {
    uint16_t vendor_id = 0;
    uint16_t product_id = 0;
    uint32_t location = 0;
    std::string manufacturer;
    std::string product;
    std::string serial;
    std::vector<UsbInterfaceInfo> interfaces;
};

class UsbDevice {
public:
    virtual ~UsbDevice() = default;

    // Seize the still-image interface. Takes it from ptpcamerad if needed.
    virtual bool claim(std::string& error) = 0;
    virtual void release() = 0;
    virtual bool is_open() const = 0;

    virtual bool bulk_out(const uint8_t* data, size_t len, unsigned timeout_ms, std::string& error) = 0;
    // Returns bytes read, or -1 on error. 0 means the pipe timed out with no data.
    virtual int bulk_in(uint8_t* data, size_t capacity, unsigned timeout_ms, std::string& error) = 0;
    virtual int interrupt_in(uint8_t* data, size_t capacity, unsigned timeout_ms, std::string& error) = 0;

    // Clear a stalled bulk pipe so the next transaction can start clean.
    virtual void reset_pipes() = 0;

    virtual const UsbDeviceInfo& info() const = 0;
};

// Trace claim/seize steps to stderr. Off by default.
void usb_set_verbose(bool verbose);

// vendor_filter of 0 lists everything.
std::vector<UsbDeviceInfo> usb_list_devices(uint16_t vendor_filter);

std::unique_ptr<UsbDevice> usb_open(uint32_t location, std::string& error);

constexpr uint16_t kSonyVendorId = 0x054C;

// USB still image class. Sony exposes this in Remote Shooting / PC Remote.
constexpr uint8_t kStillImageClass = 0x06;
