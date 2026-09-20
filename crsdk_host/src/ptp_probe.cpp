// Hardware probe for the PTP backend.
//
// Run with the camera plugged in and set to Remote Shoot (PC Remote):
//   crsdk_host/build/ptp_probe
//
// Prints the USB layout, seizes the still-image interface, and asks the camera
// to describe itself. Use --hold to keep the interface and prove that
// ptpcamerad cannot take it back.

#include "ptp.h"
#include "ptp_sony.h"
#include "usb_device.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <thread>

namespace {

const char* transfer_name(uint8_t type) {
    switch (type) {
        case kUsbControl:
            return "control";
        case kUsbIsoc:
            return "isoc";
        case kUsbBulk:
            return "bulk";
        case kUsbInterrupt:
            return "interrupt";
        default:
            return "?";
    }
}

void print_devices() {
    auto devices = usb_list_devices(kSonyVendorId);
    std::printf("Sony USB devices: %zu\n", devices.size());
    for (const auto& device : devices) {
        std::printf("  %04X:%04X  %s %s  serial=%s  location=0x%08X\n",
                    device.vendor_id,
                    device.product_id,
                    device.manufacturer.c_str(),
                    device.product.c_str(),
                    device.serial.c_str(),
                    device.location);
        for (const auto& iface : device.interfaces) {
            std::printf("    interface %u  class=0x%02X subclass=0x%02X protocol=0x%02X%s\n",
                        iface.number,
                        iface.interface_class,
                        iface.interface_subclass,
                        iface.interface_protocol,
                        iface.interface_class == kStillImageClass ? "   <- PTP" : "");
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    bool hold = false;
    bool watch = false;
    bool dump_props = false;
    bool dump_raw = false;
    bool diff = false;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--hold") == 0) {
            hold = true;
        } else if (std::strcmp(argv[i], "--watch") == 0) {
            watch = true;
        } else if (std::strcmp(argv[i], "--props") == 0) {
            dump_props = true;
        } else if (std::strcmp(argv[i], "--raw") == 0) {
            dump_raw = true;
        } else if (std::strcmp(argv[i], "--diff") == 0) {
            diff = true;
        }
    }

    usb_set_verbose(true);
    print_devices();

    std::printf("\nProcesses that compete for PTP:\n");
    std::fflush(stdout);
    (void)std::system("pgrep -l 'ptpcamerad|PTPCamera|Photos|Image Capture|Imaging Edge' || echo '  none'");

    std::string error;
    auto device = usb_open(0, error);
    if (!device) {
        std::printf("\nopen failed: %s\n", error.c_str());
        return 1;
    }

    std::printf("\nSeizing the still-image interface...\n");
    if (!device->claim(error)) {
        std::printf("claim failed: %s\n", error.c_str());
        return 1;
    }
    std::printf("claimed %s (we now own the pipe)\n", device->info().product.c_str());

    PtpDevice ptp(device.get());
    if (!ptp.open_session(error)) {
        std::printf("OpenSession failed: %s\n", error.c_str());
        return 1;
    }
    std::printf("session open\n");

    PtpDeviceInfo info;
    if (!ptp.get_device_info(info, error)) {
        std::printf("GetDeviceInfo failed: %s\n", error.c_str());
        return 1;
    }

    std::printf("\nmanufacturer : %s\n", info.manufacturer.c_str());
    std::printf("model        : %s\n", info.model.c_str());
    std::printf("firmware     : %s\n", info.device_version.c_str());
    std::printf("serial       : %s\n", info.serial_number.c_str());
    std::printf("ptp version  : %u.%02u\n", info.standard_version / 100, info.standard_version % 100);
    std::printf("vendor ext   : id=0x%08X version=%u desc=%s\n",
                info.vendor_extension_id,
                info.vendor_extension_version,
                info.vendor_extension_desc.c_str());

    std::printf("\nSony operations advertised:\n");
    bool any_sony = false;
    for (uint16_t opcode : info.operations) {
        if (opcode >= 0x9200 && opcode <= 0x92FF) {
            std::printf("  0x%04X  %s\n", opcode, sony_opcode_name(opcode));
            any_sony = true;
        }
    }
    if (!any_sony) {
        std::printf("  none - the camera is not in Remote Shoot (PC Remote) mode\n");
    }

    std::printf("\nRunning the Sony handshake...\n");
    SonyCamera sony(&ptp);
    if (!sony.handshake(error)) {
        std::printf("handshake failed: %s\n", error.c_str());
        return 1;
    }
    std::printf("handshake ok, SDIO version %u (Mode %u)\n", sony.sdio_version(),
                sony.sdio_version());
    std::printf("remote properties: %zu, controls: %zu\n", sony.properties().size(),
                sony.controls().size());

    if (sony.refresh_properties(error)) {
        // If decoded is far below claimed, the descriptor parser is misaligned
        // and every value after that point is meaningless.
        std::printf("properties claimed=%u decoded=%zu\n", sony.last_property_count(),
                    sony.parsed_properties().size());
        SonyProperty pending;
        if (sony.property(kSonyPropObjectInMemory, pending)) {
            std::printf("ObjectInMemory (0xD215) = 0x%04llX\n",
                        static_cast<unsigned long long>(pending.value));
        } else {
            std::printf("ObjectInMemory (0xD215) not reported by this body\n");
        }
    } else {
        std::printf("property read failed: %s\n", error.c_str());
    }

    if (dump_raw) {
        const auto& blob = sony.last_property_blob();
        const size_t stop = sony.last_property_stop_offset();
        std::printf("\nGetAllDevicePropData: %zu bytes, parser stopped at offset %zu\n", blob.size(),
                    stop);
        // Window the dump around the stall so the broken descriptor is visible.
        const size_t begin = stop > 32 ? (stop - 32) & ~size_t(15) : 0;
        const size_t limit = begin + 192 < blob.size() ? begin + 192 : blob.size();
        for (size_t i = begin; i < limit; i += 16) {
            std::printf("  %04zx ", i);
            for (size_t j = i; j < i + 16 && j < limit; ++j) {
                std::printf("%s%02X%s", j == stop ? "[" : " ", blob[j], j == stop ? "]" : "");
            }
            std::printf("\n");
        }
    }

    if (dump_props) {
        std::printf("\nAdvertised by SDIOGetExtDeviceInfo (%zu):\n", sony.properties().size());
        size_t column = 0;
        for (uint16_t code : sony.properties()) {
            std::printf(" %04X", code);
            if (++column % 16 == 0) {
                std::printf("\n");
            }
        }
        std::printf("\n\nDecoded from GetAllDevicePropData (%zu):\n", sony.parsed_properties().size());
        column = 0;
        for (const auto& entry : sony.parsed_properties()) {
            std::printf(" %04X", entry.first);
            if (++column % 16 == 0) {
                std::printf("\n");
            }
        }
        std::printf("\n");
    }

    if (diff) {
        std::printf("\nWatching for setting changes. Change something in the camera menu.\n");
        std::printf("Ctrl-C to stop.\n");
        std::map<uint16_t, uint64_t> previous;
        for (const auto& entry : sony.parsed_properties()) {
            previous[entry.first] = entry.second.value;
        }
        while (true) {
            std::string poll_error;
            if (!sony.refresh_properties(poll_error)) {
                std::this_thread::sleep_for(std::chrono::milliseconds(400));
                continue;
            }
            for (const auto& entry : sony.parsed_properties()) {
                auto found = previous.find(entry.first);
                if (found == previous.end()) {
                    std::printf("  + 0x%04X = 0x%llX\n", entry.first,
                                static_cast<unsigned long long>(entry.second.value));
                } else if (found->second != entry.second.value) {
                    std::printf("  ~ 0x%04X : 0x%llX -> 0x%llX\n", entry.first,
                                static_cast<unsigned long long>(found->second),
                                static_cast<unsigned long long>(entry.second.value));
                }
                previous[entry.first] = entry.second.value;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(300));
        }
    }

    if (watch) {
        std::printf("\nWatching for shots. Press the shutter. Ctrl-C to stop.\n");
        int saved = 0;
        while (true) {
            std::string poll_error;
            if (!sony.refresh_properties(poll_error)) {
                std::printf("  property poll failed: %s\n", poll_error.c_str());
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
                continue;
            }
            if (!sony.has_pending_image()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(200));
                continue;
            }
            PtpObjectInfo object;
            std::vector<uint8_t> bytes;
            if (!sony.fetch_pending_image(object, bytes, poll_error)) {
                std::printf("  fetch failed: %s\n", poll_error.c_str());
                continue;
            }
            char path[512];
            std::snprintf(path, sizeof(path), "/tmp/ptp_probe_%03d_%s", ++saved,
                          object.filename.empty() ? "shot.jpg" : object.filename.c_str());
            FILE* out = std::fopen(path, "wb");
            if (out) {
                std::fwrite(bytes.data(), 1, bytes.size(), out);
                std::fclose(out);
            }
            std::printf("  got %s  format=0x%04X  %zu bytes -> %s\n", object.filename.c_str(),
                        object.format, bytes.size(), path);
        }
    }

    if (hold) {
        std::printf("\nHolding the interface. Open Photos or run `killall -9 ptpcamerad` and\n");
        std::printf("watch that the session survives. Ctrl-C to stop.\n");
        for (int tick = 0;; ++tick) {
            PtpEvent event;
            std::string poll_error;
            if (ptp.poll_event(event, 500, poll_error) && event.code) {
                std::printf("  event 0x%04X", event.code);
                for (uint32_t param : event.params) {
                    std::printf(" 0x%08X", param);
                }
                std::printf("\n");
            }
            if (tick % 20 == 0) {
                PtpDeviceInfo again;
                std::string check_error;
                std::printf("  [%3d] still ours: %s\n",
                            tick / 20,
                            ptp.get_device_info(again, check_error) ? "yes" : check_error.c_str());
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }

    ptp.close_session();
    device->release();
    std::printf("\ndone\n");
    return 0;
}
