#pragma once

// Sony's vendor extension to PTP, the protocol Sony publishes as
// "Camera Remote Command". Bodies from the A7 IV on report SDIO version 300,
// which is the "Mode 300" other tethering apps talk about.

#include "ptp.h"

#include <map>
#include <string>
#include <vector>

constexpr uint16_t kSonyOpSdioConnect = 0x9201;
constexpr uint16_t kSonyOpSdioGetExtDeviceInfo = 0x9202;
constexpr uint16_t kSonyOpGetDevicePropDesc = 0x9203;
constexpr uint16_t kSonyOpGetDevicePropertyValue = 0x9204;
constexpr uint16_t kSonyOpSetControlDeviceA = 0x9205;
constexpr uint16_t kSonyOpGetControlDeviceDesc = 0x9206;
constexpr uint16_t kSonyOpSetControlDeviceB = 0x9207;
constexpr uint16_t kSonyOpGetAllDevicePropData = 0x9209;

constexpr uint16_t kSonyEventObjectAdded = 0xC201;
constexpr uint16_t kSonyEventObjectRemoved = 0xC202;
constexpr uint16_t kSonyEventPropertyChanged = 0xC203;

// Protocol levels we negotiate in SDIOGetExtDeviceInfo.
constexpr uint32_t kSonySdioVersion200 = 0x00C8;
constexpr uint32_t kSonySdioVersion300 = 0x012C;

// Non-zero once the body is holding a freshly shot frame for the host.
constexpr uint16_t kSonyPropObjectInMemory = 0xD215;

// The pseudo handle Sony uses for the image waiting in camera memory.
constexpr uint32_t kSonyHandleInMemory = 0xFFFFC001;

struct SonyProperty {
    uint16_t code = 0;
    uint16_t data_type = 0;
    uint8_t get_set = 0;
    uint8_t enabled = 0;
    uint64_t value = 0;
    bool value_readable = false;
};

const char* sony_opcode_name(uint16_t opcode);

class SonyCamera {
public:
    explicit SonyCamera(PtpDevice* ptp) : ptp_(ptp) {}

    // SDIOConnect 1, SDIOConnect 2, GetExtDeviceInfo, SDIOConnect 3.
    // This is the sequence Sony's own software uses to enter PC Remote.
    bool handshake(std::string& error);

    uint16_t sdio_version() const { return sdio_version_; }
    const std::vector<uint16_t>& properties() const { return property_codes_; }
    const std::vector<uint16_t>& controls() const { return control_codes_; }

    bool refresh_properties(std::string& error);
    bool property(uint16_t code, SonyProperty& out) const;
    const std::map<uint16_t, SonyProperty>& parsed_properties() const { return properties_; }
    // How many descriptors the camera claimed vs how many we could decode.
    uint32_t last_property_count() const { return last_property_count_; }
    // Raw GetAllDevicePropData payload, kept for decoding the descriptor layout.
    const std::vector<uint8_t>& last_property_blob() const { return last_property_blob_; }
    size_t last_property_stop_offset() const { return last_property_stop_offset_; }

    // True when the body has a frame waiting in memory for us to pull.
    bool has_pending_image() const;

    bool fetch_pending_image(PtpObjectInfo& info, std::vector<uint8_t>& data, std::string& error);

    bool set_control_a(uint16_t code, uint16_t data_type, uint64_t value, std::string& error);

private:
    bool sdio_connect(uint32_t step, std::string& error);
    bool read_ext_device_info(std::string& error);

    PtpDevice* ptp_ = nullptr;
    uint16_t sdio_version_ = 0;
    uint32_t last_property_count_ = 0;
    std::vector<uint8_t> last_property_blob_;
    size_t last_property_stop_offset_ = 0;
    std::vector<uint16_t> property_codes_;
    std::vector<uint16_t> control_codes_;
    std::map<uint16_t, SonyProperty> properties_;
};
