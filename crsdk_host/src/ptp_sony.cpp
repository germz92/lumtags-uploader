#include "ptp_sony.h"

#include <cstdio>

namespace {

// Data type codes shared with standard PTP property descriptors.
constexpr uint16_t kTypeInt8 = 0x0001;
constexpr uint16_t kTypeUInt8 = 0x0002;
constexpr uint16_t kTypeInt16 = 0x0003;
constexpr uint16_t kTypeUInt16 = 0x0004;
constexpr uint16_t kTypeInt32 = 0x0005;
constexpr uint16_t kTypeUInt32 = 0x0006;
constexpr uint16_t kTypeInt64 = 0x0007;
constexpr uint16_t kTypeUInt64 = 0x0008;
constexpr uint16_t kTypeString = 0xFFFF;
constexpr uint16_t kTypeArrayFlag = 0x4000;

constexpr uint8_t kFormNone = 0x00;
constexpr uint8_t kFormRange = 0x01;
constexpr uint8_t kFormEnumeration = 0x02;

size_t scalar_width(uint16_t data_type) {
    switch (data_type) {
        case kTypeInt8:
        case kTypeUInt8:
            return 1;
        case kTypeInt16:
        case kTypeUInt16:
            return 2;
        case kTypeInt32:
        case kTypeUInt32:
            return 4;
        case kTypeInt64:
        case kTypeUInt64:
            return 8;
        default:
            return 0;
    }
}

// Reads one property value. Scalars land in `value`; anything else is skipped
// so the parser can keep walking the list.
bool read_value(const std::vector<uint8_t>& buf, size_t& offset, uint16_t data_type,
                uint64_t& value, bool& scalar) {
    bool ok = true;
    scalar = false;
    const size_t width = scalar_width(data_type);
    if (width) {
        switch (width) {
            case 1:
                value = ptp_read_u8(buf, offset, ok);
                break;
            case 2:
                value = ptp_read_u16(buf, offset, ok);
                break;
            case 4:
                value = ptp_read_u32(buf, offset, ok);
                break;
            default:
                value = ptp_read_u64(buf, offset, ok);
                break;
        }
        scalar = ok;
        return ok;
    }
    if (data_type == kTypeString) {
        ptp_read_string(buf, offset, ok);
        return ok;
    }
    if (data_type & kTypeArrayFlag) {
        const uint16_t element = static_cast<uint16_t>(data_type & ~kTypeArrayFlag);
        const size_t element_width = scalar_width(element);
        const uint32_t count = ptp_read_u32(buf, offset, ok);
        if (!ok || !element_width) {
            return false;
        }
        const size_t bytes = static_cast<size_t>(count) * element_width;
        if (offset + bytes > buf.size()) {
            return false;
        }
        offset += bytes;
        return true;
    }
    return false;
}

bool skip_form_payload(const std::vector<uint8_t>& buf, size_t& offset, uint16_t data_type,
                       uint8_t form) {
    bool ok = true;
    uint64_t ignored = 0;
    bool scalar = false;
    if (form == kFormNone) {
        return true;
    }
    if (form == kFormRange) {
        for (int i = 0; i < 3; ++i) {
            if (!read_value(buf, offset, data_type, ignored, scalar)) {
                return false;
            }
        }
        return true;
    }
    if (form == kFormEnumeration) {
        const uint16_t count = ptp_read_u16(buf, offset, ok);
        if (!ok) {
            return false;
        }
        for (uint16_t i = 0; i < count; ++i) {
            if (!read_value(buf, offset, data_type, ignored, scalar)) {
                return false;
            }
        }
        return true;
    }
    return false;
}

// After the single form-flag byte, Sony writes an enumeration twice: every
// value the property can ever take, then the subset selectable right now (they
// differ, e.g. shutter speed lists shrink in some modes). Ranges appear once.
bool skip_form(const std::vector<uint8_t>& buf, size_t& offset, uint16_t data_type) {
    bool ok = true;
    const uint8_t form = ptp_read_u8(buf, offset, ok);
    if (!ok) {
        return false;
    }
    if (!skip_form_payload(buf, offset, data_type, form)) {
        return false;
    }
    if (form != kFormEnumeration) {
        return true;
    }
    return skip_form_payload(buf, offset, data_type, form);
}

}  // namespace

const char* sony_opcode_name(uint16_t opcode) {
    switch (opcode) {
        case kSonyOpSdioConnect:
            return "SDIOConnect";
        case kSonyOpSdioGetExtDeviceInfo:
            return "SDIOGetExtDeviceInfo";
        case kSonyOpGetDevicePropDesc:
            return "GetDevicePropDesc";
        case kSonyOpGetDevicePropertyValue:
            return "GetDevicePropertyValue";
        case kSonyOpSetControlDeviceA:
            return "SetControlDeviceA";
        case kSonyOpGetControlDeviceDesc:
            return "GetControlDeviceDesc";
        case kSonyOpSetControlDeviceB:
            return "SetControlDeviceB";
        case kSonyOpGetAllDevicePropData:
            return "GetAllDevicePropData";
        default:
            return "vendor";
    }
}

bool SonyCamera::sdio_connect(uint32_t step, std::string& error) {
    std::vector<uint8_t> data;
    PtpResponse response;
    if (!ptp_->transact(kSonyOpSdioConnect, {step, 0, 0}, nullptr, &data, response, error)) {
        return false;
    }
    if (!response.ok()) {
        char buffer[96];
        std::snprintf(buffer, sizeof(buffer), "SDIOConnect %u failed (0x%04X).", step, response.code);
        error = buffer;
        return false;
    }
    return true;
}

bool SonyCamera::read_ext_device_info(std::string& error) {
    // The parameter is the protocol version we ask for, and the camera answers
    // with the one it will actually speak. Newer bodies grant 300 ("Mode 300"),
    // which is the level that exposes the full remote property set.
    static const uint32_t kRequestedVersions[] = {kSonySdioVersion300, kSonySdioVersion200};

    for (uint32_t requested : kRequestedVersions) {
        std::vector<uint8_t> data;
        PtpResponse response;
        std::string attempt_error;
        if (!ptp_->transact(kSonyOpSdioGetExtDeviceInfo, {requested}, nullptr, &data, response,
                            attempt_error)) {
            error = attempt_error;
            return false;
        }
        if (!response.ok()) {
            error = "SDIOGetExtDeviceInfo was refused by the camera.";
            continue;
        }
        size_t offset = 0;
        bool ok = true;
        const uint16_t granted = ptp_read_u16(data, offset, ok);
        std::vector<uint16_t> properties = ptp_read_u16_array(data, offset, ok);
        std::vector<uint16_t> controls = ptp_read_u16_array(data, offset, ok);
        if (!ok) {
            error = "Could not read the Sony remote description.";
            continue;
        }
        sdio_version_ = granted;
        property_codes_.swap(properties);
        control_codes_.swap(controls);
        return true;
    }
    return false;
}

bool SonyCamera::handshake(std::string& error) {
    if (!sdio_connect(1, error)) {
        return false;
    }
    if (!sdio_connect(2, error)) {
        return false;
    }
    if (!read_ext_device_info(error)) {
        return false;
    }
    // Step 3 is what actually puts the body into PC Remote.
    if (!sdio_connect(3, error)) {
        return false;
    }
    return true;
}

bool SonyCamera::refresh_properties(std::string& error) {
    std::vector<uint8_t> data;
    PtpResponse response;
    if (!ptp_->transact(kSonyOpGetAllDevicePropData, {0}, nullptr, &data, response, error)) {
        return false;
    }
    if (!response.ok()) {
        char buffer[96];
        std::snprintf(buffer, sizeof(buffer), "GetAllDevicePropData failed (0x%04X).", response.code);
        error = buffer;
        return false;
    }

    last_property_blob_ = data;
    last_property_stop_offset_ = 0;

    size_t offset = 0;
    bool ok = true;
    const uint32_t count = ptp_read_u32(data, offset, ok);
    ptp_read_u32(data, offset, ok);  // reserved
    if (!ok) {
        error = "Could not read camera settings.";
        return false;
    }
    last_property_count_ = count;

    std::map<uint16_t, SonyProperty> parsed;
    for (uint32_t i = 0; i < count; ++i) {
        SonyProperty prop;
        prop.code = ptp_read_u16(data, offset, ok);
        prop.data_type = ptp_read_u16(data, offset, ok);
        prop.get_set = ptp_read_u8(data, offset, ok);
        prop.enabled = ptp_read_u8(data, offset, ok);
        if (!ok) {
            break;
        }
        uint64_t factory_default = 0;
        bool scalar = false;
        if (!read_value(data, offset, prop.data_type, factory_default, scalar)) {
            break;
        }
        if (!read_value(data, offset, prop.data_type, prop.value, scalar)) {
            break;
        }
        prop.value_readable = scalar;
        if (!skip_form(data, offset, prop.data_type)) {
            break;
        }
        parsed[prop.code] = prop;
        last_property_stop_offset_ = offset;
    }

    if (parsed.empty()) {
        error = "Camera settings were not readable.";
        return false;
    }
    properties_.swap(parsed);
    return true;
}

bool SonyCamera::property(uint16_t code, SonyProperty& out) const {
    auto it = properties_.find(code);
    if (it == properties_.end()) {
        return false;
    }
    out = it->second;
    return true;
}

bool SonyCamera::has_pending_image() const {
    auto it = properties_.find(kSonyPropObjectInMemory);
    if (it == properties_.end() || !it->second.value_readable) {
        return false;
    }
    // Sony raises this above 0x8000 when a frame is buffered for the host.
    return it->second.value > 0x8000;
}

bool SonyCamera::fetch_pending_image(PtpObjectInfo& info, std::vector<uint8_t>& data,
                                     std::string& error) {
    if (!ptp_->get_object_info(kSonyHandleInMemory, info, error)) {
        return false;
    }
    return ptp_->get_object(kSonyHandleInMemory, data, error);
}

bool SonyCamera::set_control_a(uint16_t code, uint16_t data_type, uint64_t value,
                               std::string& error) {
    std::vector<uint8_t> payload;
    const size_t width = scalar_width(data_type);
    if (!width) {
        error = "Unsupported camera setting type.";
        return false;
    }
    for (size_t i = 0; i < width; ++i) {
        payload.push_back(static_cast<uint8_t>((value >> (8 * i)) & 0xFF));
    }
    PtpResponse response;
    if (!ptp_->transact(kSonyOpSetControlDeviceA, {code}, &payload, nullptr, response, error)) {
        return false;
    }
    if (!response.ok()) {
        char buffer[112];
        std::snprintf(buffer, sizeof(buffer), "Setting 0x%04X failed (0x%04X).", code, response.code);
        error = buffer;
        return false;
    }
    return true;
}
