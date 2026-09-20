#ifdef __APPLE__

#include "usb_device.h"

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/usb/USB.h>

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace {

bool g_verbose = false;

// Apple's ptpcamerad opens the still-image interface as soon as the camera
// enumerates and ignores the polite seize request, so we ask it to exit once
// and then take the interface. Holding it afterwards is what keeps the session:
// ptpcamerad respawns freely but cannot reclaim an interface we own.
constexpr int kSeizeAttempts = 40;
constexpr int kSeizeDelayMs = 60;
constexpr int kSeizeAttemptsBeforeDaemonStop = 3;
// The device open is optional, so give it a short try rather than a long one.
constexpr int kDeviceOpenAttempts = 3;

void trace(const char* format, ...) __attribute__((format(printf, 1, 2)));

void trace(const char* format, ...) {
    if (!g_verbose) {
        return;
    }
    va_list args;
    va_start(args, format);
    std::fprintf(stderr, "usb: ");
    std::vfprintf(stderr, format, args);
    std::fprintf(stderr, "\n");
    va_end(args);
}

void stop_ptp_daemon() {
    // ptpcamerad ignores SIGTERM while it holds a camera. launchd restarts it
    // on demand, and once we hold the interface it comes back without it.
    trace("stopping ptpcamerad so we can take the interface");
    (void)std::system("/usr/bin/killall -9 ptpcamerad >/dev/null 2>&1");
}

std::string cf_string_property(io_service_t service, CFStringRef key) {
    CFTypeRef value = IORegistryEntryCreateCFProperty(service, key, kCFAllocatorDefault, 0);
    if (!value) {
        return "";
    }
    std::string out;
    if (CFGetTypeID(value) == CFStringGetTypeID()) {
        char buffer[256] = {0};
        if (CFStringGetCString(static_cast<CFStringRef>(value), buffer, sizeof(buffer), kCFStringEncodingUTF8)) {
            out = buffer;
        }
    }
    CFRelease(value);
    return out;
}

uint32_t cf_number_property(io_service_t service, CFStringRef key, uint32_t fallback) {
    CFTypeRef value = IORegistryEntryCreateCFProperty(service, key, kCFAllocatorDefault, 0);
    if (!value) {
        return fallback;
    }
    uint32_t out = fallback;
    if (CFGetTypeID(value) == CFNumberGetTypeID()) {
        SInt32 raw = 0;
        if (CFNumberGetValue(static_cast<CFNumberRef>(value), kCFNumberSInt32Type, &raw)) {
            out = static_cast<uint32_t>(raw);
        }
    }
    CFRelease(value);
    return out;
}

// Interface descriptors come from the IORegistry so we can describe a device
// without opening it (opening would fight ptpcamerad for no reason).
void read_interfaces_from_registry(io_service_t device_service, UsbDeviceInfo& info) {
    io_iterator_t children = IO_OBJECT_NULL;
    if (IORegistryEntryGetChildIterator(device_service, kIOServicePlane, &children) != KERN_SUCCESS) {
        return;
    }
    io_service_t child = IO_OBJECT_NULL;
    while ((child = IOIteratorNext(children))) {
        CFTypeRef marker = IORegistryEntryCreateCFProperty(
            child, CFSTR("bInterfaceClass"), kCFAllocatorDefault, 0);
        if (marker) {
            CFRelease(marker);
            UsbInterfaceInfo iface;
            iface.number = static_cast<uint8_t>(cf_number_property(child, CFSTR("bInterfaceNumber"), 0));
            iface.interface_class = static_cast<uint8_t>(cf_number_property(child, CFSTR("bInterfaceClass"), 0));
            iface.interface_subclass =
                static_cast<uint8_t>(cf_number_property(child, CFSTR("bInterfaceSubClass"), 0));
            iface.interface_protocol =
                static_cast<uint8_t>(cf_number_property(child, CFSTR("bInterfaceProtocol"), 0));
            info.interfaces.push_back(iface);
        }
        IOObjectRelease(child);
    }
    IOObjectRelease(children);
}

UsbDeviceInfo describe_device(io_service_t service) {
    UsbDeviceInfo info;
    info.vendor_id = static_cast<uint16_t>(cf_number_property(service, CFSTR("idVendor"), 0));
    info.product_id = static_cast<uint16_t>(cf_number_property(service, CFSTR("idProduct"), 0));
    info.location = cf_number_property(service, CFSTR("locationID"), 0);
    info.manufacturer = cf_string_property(service, CFSTR("USB Vendor Name"));
    info.product = cf_string_property(service, CFSTR("USB Product Name"));
    info.serial = cf_string_property(service, CFSTR("USB Serial Number"));
    read_interfaces_from_registry(service, info);
    return info;
}

IOUSBDeviceInterface500** create_device_interface(io_service_t service) {
    IOCFPlugInInterface** plugin = nullptr;
    SInt32 score = 0;
    kern_return_t kr = IOCreatePlugInInterfaceForService(
        service, kIOUSBDeviceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
    if (kr != KERN_SUCCESS || !plugin) {
        return nullptr;
    }
    IOUSBDeviceInterface500** device = nullptr;
    HRESULT hr = (*plugin)->QueryInterface(
        plugin, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID500), reinterpret_cast<LPVOID*>(&device));
    (*plugin)->Release(plugin);
    if (hr != S_OK) {
        return nullptr;
    }
    return device;
}

IOUSBInterfaceInterface500** create_interface_interface(io_service_t service) {
    IOCFPlugInInterface** plugin = nullptr;
    SInt32 score = 0;
    kern_return_t kr = IOCreatePlugInInterfaceForService(
        service, kIOUSBInterfaceUserClientTypeID, kIOCFPlugInInterfaceID, &plugin, &score);
    if (kr != KERN_SUCCESS || !plugin) {
        return nullptr;
    }
    IOUSBInterfaceInterface500** iface = nullptr;
    HRESULT hr = (*plugin)->QueryInterface(
        plugin, CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID500), reinterpret_cast<LPVOID*>(&iface));
    (*plugin)->Release(plugin);
    if (hr != S_OK) {
        return nullptr;
    }
    return iface;
}

class DarwinUsbDevice : public UsbDevice {
public:
    DarwinUsbDevice(io_service_t service, const UsbDeviceInfo& info) : service_(service), info_(info) {}

    ~DarwinUsbDevice() override {
        release();
        if (service_) {
            IOObjectRelease(service_);
            service_ = IO_OBJECT_NULL;
        }
    }

    bool claim(std::string& error) override {
        if (interface_) {
            return true;
        }
        daemon_stopped_ = false;
        device_ = create_device_interface(service_);
        if (!device_) {
            error = "Could not open the camera USB device.";
            return false;
        }
        // Opening the device is a convenience, not a requirement: bulk transfers
        // run on the interface, and IOKit will hand us an interface even while
        // another process owns the device. ptpcamerad often holds the device on a
        // cold plug, so treating that as fatal would fail a connect we can win.
        open_device_best_effort();

        if (!claim_still_image_interface(error)) {
            teardown();
            return false;
        }

        // Now that the interface is ours the daemon has been displaced, so a
        // second try usually gets us the device too.
        if (!device_open_) {
            open_device_best_effort();
        }
        return true;
    }

    void release() override { teardown(); }

    bool is_open() const override { return interface_ != nullptr; }

    bool bulk_out(const uint8_t* data, size_t len, unsigned timeout_ms, std::string& error) override {
        if (!interface_ || !bulk_out_pipe_) {
            error = "Camera USB pipe is not open.";
            return false;
        }
        IOReturn kr = (*interface_)->WritePipeTO(
            interface_,
            bulk_out_pipe_,
            const_cast<uint8_t*>(data),
            static_cast<UInt32>(len),
            timeout_ms,
            timeout_ms);
        if (kr != kIOReturnSuccess) {
            error = describe_ioreturn("USB write failed", kr);
            if (kr == kIOUSBPipeStalled) {
                (*interface_)->ClearPipeStallBothEnds(interface_, bulk_out_pipe_);
            }
            return false;
        }
        return true;
    }

    int bulk_in(uint8_t* data, size_t capacity, unsigned timeout_ms, std::string& error) override {
        return read_pipe(bulk_in_pipe_, data, capacity, timeout_ms, error);
    }

    int interrupt_in(uint8_t* data, size_t capacity, unsigned timeout_ms, std::string& error) override {
        if (!interrupt_pipe_) {
            return 0;
        }
        return read_pipe(interrupt_pipe_, data, capacity, timeout_ms, error);
    }

    void reset_pipes() override {
        if (!interface_) {
            return;
        }
        if (bulk_in_pipe_) {
            (*interface_)->ClearPipeStallBothEnds(interface_, bulk_in_pipe_);
        }
        if (bulk_out_pipe_) {
            (*interface_)->ClearPipeStallBothEnds(interface_, bulk_out_pipe_);
        }
    }

    const UsbDeviceInfo& info() const override { return info_; }

private:
    int read_pipe(uint8_t pipe, uint8_t* data, size_t capacity, unsigned timeout_ms, std::string& error) {
        if (!interface_ || !pipe) {
            error = "Camera USB pipe is not open.";
            return -1;
        }
        UInt32 size = static_cast<UInt32>(capacity);
        IOReturn kr = (*interface_)->ReadPipeTO(interface_, pipe, data, &size, timeout_ms, timeout_ms);
        if (kr == kIOReturnSuccess || kr == kIOReturnUnderrun) {
            return static_cast<int>(size);
        }
        if (kr == kIOUSBTransactionTimeout || kr == kIOReturnTimeout) {
            // Routine while polling for events.
            return 0;
        }
        error = describe_ioreturn("USB read failed", kr);
        if (kr == kIOUSBPipeStalled) {
            (*interface_)->ClearPipeStallBothEnds(interface_, pipe);
        }
        return -1;
    }

    void ensure_configuration() {
        UInt8 config = 0;
        IOReturn kr = (*device_)->GetConfiguration(device_, &config);
        trace("GetConfiguration -> 0x%08X value=%u", kr, config);
        if (kr == kIOReturnSuccess && config != 0) {
            return;
        }
        IOUSBConfigurationDescriptorPtr descriptor = nullptr;
        if ((*device_)->GetConfigurationDescriptorPtr(device_, 0, &descriptor) == kIOReturnSuccess &&
            descriptor) {
            kr = (*device_)->SetConfiguration(device_, descriptor->bConfigurationValue);
            trace("SetConfiguration(%u) -> 0x%08X", descriptor->bConfigurationValue, kr);
        }
    }

    bool claim_still_image_interface(std::string& error) {
        IOUSBFindInterfaceRequest request;
        request.bInterfaceClass = kIOUSBFindInterfaceDontCare;
        request.bInterfaceSubClass = kIOUSBFindInterfaceDontCare;
        request.bInterfaceProtocol = kIOUSBFindInterfaceDontCare;
        request.bAlternateSetting = kIOUSBFindInterfaceDontCare;

        io_iterator_t iterator = IO_OBJECT_NULL;
        IOReturn kr = (*device_)->CreateInterfaceIterator(device_, &request, &iterator);
        trace("CreateInterfaceIterator -> 0x%08X", kr);
        if (kr != kIOReturnSuccess) {
            error = describe_ioreturn("Could not list camera USB interfaces", kr);
            return false;
        }

        int seen = 0;
        int still_image_seen = 0;
        IOReturn last_open = kIOReturnSuccess;
        bool pipes_missing = false;
        bool seized = false;
        io_service_t usb_interface = IO_OBJECT_NULL;
        while (!seized && (usb_interface = IOIteratorNext(iterator))) {
            IOUSBInterfaceInterface500** candidate = create_interface_interface(usb_interface);
            IOObjectRelease(usb_interface);
            if (!candidate) {
                trace("interface plugin query failed");
                continue;
            }
            ++seen;
            UInt8 cls = 0;
            UInt8 subclass = 0;
            UInt8 protocol = 0;
            UInt8 number = 0;
            (*candidate)->GetInterfaceClass(candidate, &cls);
            (*candidate)->GetInterfaceSubClass(candidate, &subclass);
            (*candidate)->GetInterfaceProtocol(candidate, &protocol);
            (*candidate)->GetInterfaceNumber(candidate, &number);
            trace("interface %u class=0x%02X subclass=0x%02X protocol=0x%02X", number, cls, subclass,
                  protocol);

            // Still image (6/1/1) is the PTP interface. Sony also ships
            // vendor-specific interfaces we should not talk to.
            if (cls != kStillImageClass) {
                (*candidate)->Release(candidate);
                continue;
            }
            ++still_image_seen;

            IOReturn open_result = kIOReturnExclusiveAccess;
            for (int attempt = 0; attempt < kSeizeAttempts; ++attempt) {
                open_result = (*candidate)->USBInterfaceOpenSeize(candidate);
                if (attempt == 0 || open_result == kIOReturnSuccess) {
                    trace("USBInterfaceOpenSeize(%u) attempt %d -> 0x%08X", number, attempt + 1,
                          open_result);
                }
                if (open_result != kIOReturnExclusiveAccess) {
                    break;
                }
                // Give the polite seize a few tries before displacing the daemon.
                if (attempt + 1 >= kSeizeAttemptsBeforeDaemonStop) {
                    stop_ptp_daemon_once();
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(kSeizeDelayMs));
            }
            if (open_result == kIOReturnExclusiveAccess) {
                trace("USBInterfaceOpenSeize(%u) still busy after %d attempts", number,
                      kSeizeAttempts);
            }
            if (open_result != kIOReturnSuccess) {
                last_open = open_result;
                (*candidate)->Release(candidate);
                continue;
            }

            interface_ = candidate;
            claimed_ = UsbInterfaceInfo{number, cls, subclass, protocol, {}};
            if (collect_pipes()) {
                seized = true;
            } else {
                pipes_missing = true;
                trace("interface %u opened but has no bulk in/out pair", number);
                (*interface_)->USBInterfaceClose(interface_);
                (*interface_)->Release(interface_);
                interface_ = nullptr;
            }
        }
        IOObjectRelease(iterator);
        trace("interfaces seen=%d still-image=%d seized=%d", seen, still_image_seen, seized ? 1 : 0);

        if (seized) {
            return true;
        }
        if (still_image_seen == 0) {
            error = seen == 0
                        ? "macOS did not expose any USB interface for the camera. Unplug, power the "
                          "camera off and on, then plug back in."
                        : "The camera has no PTP interface right now. Set USB mode to Remote Shoot "
                          "(PC Remote), then power the camera off and on.";
            return false;
        }
        if (pipes_missing) {
            error = "The camera's PTP interface has no data pipes. Power the camera off and on.";
            return false;
        }
        if (last_open == kIOReturnExclusiveAccess) {
            error = "Another app owns the camera. Quit Photos, Image Capture, and Imaging Edge.";
            return false;
        }
        error = describe_ioreturn("Could not take the camera's PTP interface", last_open);
        return false;
    }

    bool collect_pipes() {
        UInt8 endpoints = 0;
        if ((*interface_)->GetNumEndpoints(interface_, &endpoints) != kIOReturnSuccess) {
            return false;
        }
        bulk_in_pipe_ = 0;
        bulk_out_pipe_ = 0;
        interrupt_pipe_ = 0;
        for (UInt8 pipe = 1; pipe <= endpoints; ++pipe) {
            UInt8 direction = 0;
            UInt8 number = 0;
            UInt8 transfer_type = 0;
            UInt16 max_packet = 0;
            UInt8 interval = 0;
            if ((*interface_)->GetPipeProperties(
                    interface_, pipe, &direction, &number, &transfer_type, &max_packet, &interval) !=
                kIOReturnSuccess) {
                continue;
            }
            UsbEndpoint endpoint;
            endpoint.pipe_ref = pipe;
            endpoint.number = number;
            endpoint.direction = direction;
            endpoint.type = transfer_type;
            endpoint.max_packet = max_packet;
            claimed_.endpoints.push_back(endpoint);

            if (transfer_type == kUSBBulk && direction == kUSBIn && !bulk_in_pipe_) {
                bulk_in_pipe_ = pipe;
                bulk_in_packet_ = max_packet;
            } else if (transfer_type == kUSBBulk && direction == kUSBOut && !bulk_out_pipe_) {
                bulk_out_pipe_ = pipe;
            } else if (transfer_type == kUSBInterrupt && direction == kUSBIn && !interrupt_pipe_) {
                interrupt_pipe_ = pipe;
            }
        }
        return bulk_in_pipe_ != 0 && bulk_out_pipe_ != 0;
    }

    void teardown() {
        if (interface_) {
            (*interface_)->USBInterfaceClose(interface_);
            (*interface_)->Release(interface_);
            interface_ = nullptr;
        }
        if (device_) {
            if (device_open_) {
                (*device_)->USBDeviceClose(device_);
                device_open_ = false;
            }
            (*device_)->Release(device_);
            device_ = nullptr;
        }
        bulk_in_pipe_ = 0;
        bulk_out_pipe_ = 0;
        interrupt_pipe_ = 0;
    }

    // Try to own the device so we can read its configuration. Never fatal.
    void open_device_best_effort() {
        if (device_open_ || !device_) {
            return;
        }
        IOReturn kr = kIOReturnExclusiveAccess;
        for (int attempt = 0; attempt < kDeviceOpenAttempts; ++attempt) {
            kr = (*device_)->USBDeviceOpenSeize(device_);
            if (kr != kIOReturnExclusiveAccess) {
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(kSeizeDelayMs));
        }
        trace("USBDeviceOpenSeize -> 0x%08X%s", kr,
              kr == kIOReturnSuccess ? "" : " (continuing on the interface alone)");
        if (kr != kIOReturnSuccess) {
            return;
        }
        device_open_ = true;
        ensure_configuration();
    }

    // At most one daemon stop per claim, no matter which stage needed it.
    void stop_ptp_daemon_once() {
        if (daemon_stopped_) {
            return;
        }
        daemon_stopped_ = true;
        stop_ptp_daemon();
    }

    static std::string describe_ioreturn(const char* prefix, IOReturn kr) {
        char buffer[128];
        std::snprintf(buffer, sizeof(buffer), "%s (0x%08X)", prefix, static_cast<unsigned>(kr));
        return buffer;
    }

    io_service_t service_ = IO_OBJECT_NULL;
    UsbDeviceInfo info_;
    UsbInterfaceInfo claimed_;
    IOUSBDeviceInterface500** device_ = nullptr;
    IOUSBInterfaceInterface500** interface_ = nullptr;
    uint8_t bulk_in_pipe_ = 0;
    uint8_t bulk_out_pipe_ = 0;
    uint8_t interrupt_pipe_ = 0;
    uint16_t bulk_in_packet_ = 512;
    bool device_open_ = false;
    bool daemon_stopped_ = false;
};

io_iterator_t matching_usb_devices() {
    CFMutableDictionaryRef matching = IOServiceMatching(kIOUSBDeviceClassName);
    if (!matching) {
        return IO_OBJECT_NULL;
    }
    io_iterator_t iterator = IO_OBJECT_NULL;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator) != KERN_SUCCESS) {
        return IO_OBJECT_NULL;
    }
    return iterator;
}

}  // namespace

void usb_set_verbose(bool verbose) { g_verbose = verbose; }

std::vector<UsbDeviceInfo> usb_list_devices(uint16_t vendor_filter) {
    std::vector<UsbDeviceInfo> devices;
    io_iterator_t iterator = matching_usb_devices();
    if (!iterator) {
        return devices;
    }
    io_service_t service = IO_OBJECT_NULL;
    while ((service = IOIteratorNext(iterator))) {
        UsbDeviceInfo info = describe_device(service);
        if (vendor_filter == 0 || info.vendor_id == vendor_filter) {
            devices.push_back(info);
        }
        IOObjectRelease(service);
    }
    IOObjectRelease(iterator);
    return devices;
}

std::unique_ptr<UsbDevice> usb_open(uint32_t location, std::string& error) {
    io_iterator_t iterator = matching_usb_devices();
    if (!iterator) {
        error = "Could not search USB devices.";
        return nullptr;
    }
    io_service_t service = IO_OBJECT_NULL;
    io_service_t found = IO_OBJECT_NULL;
    UsbDeviceInfo found_info;
    while ((service = IOIteratorNext(iterator))) {
        UsbDeviceInfo info = describe_device(service);
        const bool matches = location ? info.location == location : info.vendor_id == kSonyVendorId;
        if (matches && !found) {
            found = service;
            found_info = info;
            continue;
        }
        IOObjectRelease(service);
    }
    IOObjectRelease(iterator);

    if (!found) {
        error = "No Sony camera on USB.";
        return nullptr;
    }
    return std::unique_ptr<UsbDevice>(new DarwinUsbDevice(found, found_info));
}

#endif  // __APPLE__
