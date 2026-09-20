#include "ptp.h"

#include <algorithm>
#include <cstring>

namespace {

constexpr size_t kContainerHeaderSize = 12;
constexpr size_t kReadChunk = 256 * 1024;

void append_u16(std::vector<uint8_t>& buf, uint16_t value) {
    buf.push_back(static_cast<uint8_t>(value & 0xFF));
    buf.push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
}

void append_u32(std::vector<uint8_t>& buf, uint32_t value) {
    buf.push_back(static_cast<uint8_t>(value & 0xFF));
    buf.push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
    buf.push_back(static_cast<uint8_t>((value >> 16) & 0xFF));
    buf.push_back(static_cast<uint8_t>((value >> 24) & 0xFF));
}

std::string utf16le_to_utf8(const std::vector<uint16_t>& units) {
    std::string out;
    out.reserve(units.size());
    for (size_t i = 0; i < units.size(); ++i) {
        uint32_t code = units[i];
        if (code == 0) {
            break;
        }
        if (code >= 0xD800 && code <= 0xDBFF && i + 1 < units.size()) {
            const uint32_t low = units[i + 1];
            if (low >= 0xDC00 && low <= 0xDFFF) {
                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                ++i;
            }
        }
        if (code < 0x80) {
            out.push_back(static_cast<char>(code));
        } else if (code < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (code >> 6)));
            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
        } else if (code < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (code >> 12)));
            out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (code >> 18)));
            out.push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
        }
    }
    return out;
}

}  // namespace

uint8_t ptp_read_u8(const std::vector<uint8_t>& buf, size_t& offset, bool& ok) {
    if (offset + 1 > buf.size()) {
        ok = false;
        return 0;
    }
    return buf[offset++];
}

uint16_t ptp_read_u16(const std::vector<uint8_t>& buf, size_t& offset, bool& ok) {
    if (offset + 2 > buf.size()) {
        ok = false;
        return 0;
    }
    const uint16_t value = static_cast<uint16_t>(buf[offset] | (buf[offset + 1] << 8));
    offset += 2;
    return value;
}

uint32_t ptp_read_u32(const std::vector<uint8_t>& buf, size_t& offset, bool& ok) {
    if (offset + 4 > buf.size()) {
        ok = false;
        return 0;
    }
    const uint32_t value = static_cast<uint32_t>(buf[offset]) |
                           (static_cast<uint32_t>(buf[offset + 1]) << 8) |
                           (static_cast<uint32_t>(buf[offset + 2]) << 16) |
                           (static_cast<uint32_t>(buf[offset + 3]) << 24);
    offset += 4;
    return value;
}

uint64_t ptp_read_u64(const std::vector<uint8_t>& buf, size_t& offset, bool& ok) {
    const uint32_t low = ptp_read_u32(buf, offset, ok);
    const uint32_t high = ptp_read_u32(buf, offset, ok);
    return (static_cast<uint64_t>(high) << 32) | low;
}

std::string ptp_read_string(const std::vector<uint8_t>& buf, size_t& offset, bool& ok) {
    const uint8_t count = ptp_read_u8(buf, offset, ok);
    if (!ok || count == 0) {
        return "";
    }
    std::vector<uint16_t> units;
    units.reserve(count);
    for (uint8_t i = 0; i < count; ++i) {
        units.push_back(ptp_read_u16(buf, offset, ok));
        if (!ok) {
            return "";
        }
    }
    return utf16le_to_utf8(units);
}

std::vector<uint16_t> ptp_read_u16_array(const std::vector<uint8_t>& buf, size_t& offset, bool& ok) {
    std::vector<uint16_t> values;
    const uint32_t count = ptp_read_u32(buf, offset, ok);
    if (!ok || count > 0xFFFF) {
        ok = false;
        return values;
    }
    values.reserve(count);
    for (uint32_t i = 0; i < count; ++i) {
        values.push_back(ptp_read_u16(buf, offset, ok));
        if (!ok) {
            break;
        }
    }
    return values;
}

bool PtpDeviceInfo::supports(uint16_t opcode) const {
    return std::find(operations.begin(), operations.end(), opcode) != operations.end();
}

bool PtpDevice::write_container(uint16_t type, uint16_t code, uint32_t transaction_id,
                                const std::vector<uint32_t>& params, const uint8_t* payload,
                                size_t payload_len, unsigned timeout_ms, std::string& error) {
    std::vector<uint8_t> buffer;
    const size_t length = kContainerHeaderSize + params.size() * 4 + payload_len;
    buffer.reserve(length);
    append_u32(buffer, static_cast<uint32_t>(length));
    append_u16(buffer, type);
    append_u16(buffer, code);
    append_u32(buffer, transaction_id);
    for (uint32_t param : params) {
        append_u32(buffer, param);
    }
    if (payload && payload_len) {
        buffer.insert(buffer.end(), payload, payload + payload_len);
    }
    return usb_->bulk_out(buffer.data(), buffer.size(), timeout_ms, error);
}

bool PtpDevice::ensure_bytes(size_t count, unsigned timeout_ms, std::string& error) {
    std::vector<uint8_t> chunk(kReadChunk);
    while (rx_.size() < count) {
        const int read = usb_->bulk_in(chunk.data(), chunk.size(), timeout_ms, error);
        if (read < 0) {
            return false;
        }
        if (read == 0) {
            error = "The camera stopped responding.";
            return false;
        }
        rx_.insert(rx_.end(), chunk.begin(), chunk.begin() + read);
    }
    return true;
}

bool PtpDevice::read_container(uint16_t& type, uint16_t& code, uint32_t& transaction_id,
                               std::vector<uint8_t>& payload, unsigned timeout_ms,
                               std::string& error) {
    if (!ensure_bytes(kContainerHeaderSize, timeout_ms, error)) {
        return false;
    }
    size_t offset = 0;
    bool ok = true;
    const uint32_t length = ptp_read_u32(rx_, offset, ok);
    type = ptp_read_u16(rx_, offset, ok);
    code = ptp_read_u16(rx_, offset, ok);
    transaction_id = ptp_read_u32(rx_, offset, ok);
    if (!ok || length < kContainerHeaderSize) {
        error = "Bad PTP container from the camera.";
        rx_.clear();
        return false;
    }
    if (!ensure_bytes(length, timeout_ms, error)) {
        return false;
    }
    payload.assign(rx_.begin() + kContainerHeaderSize, rx_.begin() + length);
    rx_.erase(rx_.begin(), rx_.begin() + length);
    return true;
}

bool PtpDevice::transact(uint16_t opcode, const std::vector<uint32_t>& params,
                         const std::vector<uint8_t>* data_out, std::vector<uint8_t>* data_in,
                         PtpResponse& response, std::string& error, unsigned timeout_ms) {
    if (!usb_ || !usb_->is_open()) {
        error = "Camera USB is not open.";
        return false;
    }
    const uint32_t transaction_id = ++transaction_id_;

    if (!write_container(kPtpTypeCommand, opcode, transaction_id, params, nullptr, 0, timeout_ms,
                         error)) {
        return false;
    }
    if (data_out) {
        if (!write_container(kPtpTypeData, opcode, transaction_id, {}, data_out->data(),
                             data_out->size(), timeout_ms, error)) {
            return false;
        }
    }

    // Expect an optional data block, then a response block.
    for (int block = 0; block < 4; ++block) {
        uint16_t type = 0;
        uint16_t code = 0;
        uint32_t tid = 0;
        std::vector<uint8_t> payload;
        if (!read_container(type, code, tid, payload, timeout_ms, error)) {
            return false;
        }
        if (type == kPtpTypeData) {
            if (data_in) {
                *data_in = std::move(payload);
            }
            continue;
        }
        if (type == kPtpTypeResponse) {
            response.code = code;
            response.params.clear();
            size_t offset = 0;
            bool ok = true;
            while (offset + 4 <= payload.size()) {
                response.params.push_back(ptp_read_u32(payload, offset, ok));
                if (!ok) {
                    break;
                }
            }
            return true;
        }
        // Events can arrive on the bulk pipe on some bodies. Ignore and keep reading.
    }
    error = "The camera did not finish the PTP transaction.";
    return false;
}

bool PtpDevice::open_session(std::string& error) {
    session_id_ = 1;
    transaction_id_ = 0;
    PtpResponse response;
    if (!transact(kPtpOpOpenSession, {session_id_}, nullptr, nullptr, response, error)) {
        return false;
    }
    if (!response.ok() && response.code != kPtpResponseSessionAlreadyOpen) {
        char buffer[96];
        std::snprintf(buffer, sizeof(buffer), "OpenSession failed (0x%04X).", response.code);
        error = buffer;
        return false;
    }
    session_open_ = true;
    return true;
}

void PtpDevice::close_session() {
    if (!session_open_) {
        return;
    }
    PtpResponse response;
    std::string error;
    transact(kPtpOpCloseSession, {}, nullptr, nullptr, response, error, 2000);
    session_open_ = false;
}

bool PtpDevice::get_device_info(PtpDeviceInfo& info, std::string& error) {
    std::vector<uint8_t> data;
    PtpResponse response;
    if (!transact(kPtpOpGetDeviceInfo, {}, nullptr, &data, response, error)) {
        return false;
    }
    if (!response.ok()) {
        char buffer[96];
        std::snprintf(buffer, sizeof(buffer), "GetDeviceInfo failed (0x%04X).", response.code);
        error = buffer;
        return false;
    }
    size_t offset = 0;
    bool ok = true;
    info.standard_version = ptp_read_u16(data, offset, ok);
    info.vendor_extension_id = ptp_read_u32(data, offset, ok);
    info.vendor_extension_version = ptp_read_u16(data, offset, ok);
    info.vendor_extension_desc = ptp_read_string(data, offset, ok);
    ptp_read_u16(data, offset, ok);  // functional mode
    info.operations = ptp_read_u16_array(data, offset, ok);
    info.events = ptp_read_u16_array(data, offset, ok);
    info.device_properties = ptp_read_u16_array(data, offset, ok);
    ptp_read_u16_array(data, offset, ok);  // capture formats
    ptp_read_u16_array(data, offset, ok);  // image formats
    info.manufacturer = ptp_read_string(data, offset, ok);
    info.model = ptp_read_string(data, offset, ok);
    info.device_version = ptp_read_string(data, offset, ok);
    info.serial_number = ptp_read_string(data, offset, ok);
    if (!ok) {
        error = "Could not read the camera description.";
        return false;
    }
    return true;
}

bool PtpDevice::get_object_info(uint32_t handle, PtpObjectInfo& info, std::string& error) {
    std::vector<uint8_t> data;
    PtpResponse response;
    if (!transact(kPtpOpGetObjectInfo, {handle}, nullptr, &data, response, error)) {
        return false;
    }
    if (!response.ok()) {
        char buffer[96];
        std::snprintf(buffer, sizeof(buffer), "GetObjectInfo failed (0x%04X).", response.code);
        error = buffer;
        return false;
    }
    size_t offset = 0;
    bool ok = true;
    info.storage_id = ptp_read_u32(data, offset, ok);
    info.format = ptp_read_u16(data, offset, ok);
    ptp_read_u16(data, offset, ok);  // protection status
    info.compressed_size = ptp_read_u32(data, offset, ok);
    info.thumb_format = ptp_read_u16(data, offset, ok);
    info.thumb_size = ptp_read_u32(data, offset, ok);
    ptp_read_u32(data, offset, ok);  // thumb width
    ptp_read_u32(data, offset, ok);  // thumb height
    ptp_read_u32(data, offset, ok);  // image width
    ptp_read_u32(data, offset, ok);  // image height
    ptp_read_u32(data, offset, ok);  // bit depth
    info.parent_object = ptp_read_u32(data, offset, ok);
    ptp_read_u16(data, offset, ok);  // association type
    ptp_read_u32(data, offset, ok);  // association desc
    ptp_read_u32(data, offset, ok);  // sequence number
    info.filename = ptp_read_string(data, offset, ok);
    info.capture_date = ptp_read_string(data, offset, ok);
    if (!ok) {
        error = "Could not read the photo description.";
        return false;
    }
    return true;
}

bool PtpDevice::get_object(uint32_t handle, std::vector<uint8_t>& data, std::string& error) {
    PtpResponse response;
    // Full-size JPEGs take a while over USB 2 pipes.
    if (!transact(kPtpOpGetObject, {handle}, nullptr, &data, response, error, 30000)) {
        return false;
    }
    if (!response.ok()) {
        char buffer[96];
        std::snprintf(buffer, sizeof(buffer), "GetObject failed (0x%04X).", response.code);
        error = buffer;
        return false;
    }
    return true;
}

bool PtpDevice::poll_event(PtpEvent& event, unsigned timeout_ms, std::string& error) {
    event.code = 0;
    event.params.clear();
    uint8_t buffer[64] = {0};
    const int read = usb_->interrupt_in(buffer, sizeof(buffer), timeout_ms, error);
    if (read <= 0) {
        // 0 is a normal idle poll; -1 already set error.
        return read == 0;
    }
    if (static_cast<size_t>(read) < kContainerHeaderSize) {
        return true;
    }
    std::vector<uint8_t> payload(buffer, buffer + read);
    size_t offset = 0;
    bool ok = true;
    const uint32_t length = ptp_read_u32(payload, offset, ok);
    const uint16_t type = ptp_read_u16(payload, offset, ok);
    event.code = ptp_read_u16(payload, offset, ok);
    event.transaction_id = ptp_read_u32(payload, offset, ok);
    if (!ok || type != kPtpTypeEvent) {
        event.code = 0;
        return true;
    }
    const size_t limit = std::min(static_cast<size_t>(length), payload.size());
    while (offset + 4 <= limit) {
        event.params.push_back(ptp_read_u32(payload, offset, ok));
        if (!ok) {
            break;
        }
    }
    return true;
}

void PtpDevice::reset() {
    rx_.clear();
    transaction_id_ = 0;
    session_open_ = false;
}
