#pragma once

// Minimal PTP (Picture Transfer Protocol) over USB bulk transport.
// Enough of ISO 15740 to run a tethered session, plus room for Sony's
// vendor extension, which lives in ptp_sony.h.

#include "usb_device.h"

#include <cstdint>
#include <string>
#include <vector>

constexpr uint16_t kPtpTypeCommand = 1;
constexpr uint16_t kPtpTypeData = 2;
constexpr uint16_t kPtpTypeResponse = 3;
constexpr uint16_t kPtpTypeEvent = 4;

constexpr uint16_t kPtpOpGetDeviceInfo = 0x1001;
constexpr uint16_t kPtpOpOpenSession = 0x1002;
constexpr uint16_t kPtpOpCloseSession = 0x1003;
constexpr uint16_t kPtpOpGetStorageIds = 0x1004;
constexpr uint16_t kPtpOpGetObjectInfo = 0x1008;
constexpr uint16_t kPtpOpGetObject = 0x1009;
constexpr uint16_t kPtpOpGetThumb = 0x100A;
constexpr uint16_t kPtpOpDeleteObject = 0x100B;
constexpr uint16_t kPtpOpGetPartialObject = 0x101B;

constexpr uint16_t kPtpResponseOk = 0x2001;
constexpr uint16_t kPtpResponseSessionAlreadyOpen = 0x201E;
constexpr uint16_t kPtpResponseDeviceBusy = 0x2019;

constexpr uint16_t kPtpEventObjectAdded = 0x4002;

struct PtpResponse {
    uint16_t code = 0;
    std::vector<uint32_t> params;
    bool ok() const { return code == kPtpResponseOk; }
};

struct PtpEvent {
    uint16_t code = 0;
    uint32_t transaction_id = 0;
    std::vector<uint32_t> params;
};

struct PtpDeviceInfo {
    uint16_t standard_version = 0;
    uint32_t vendor_extension_id = 0;
    uint16_t vendor_extension_version = 0;
    std::string vendor_extension_desc;
    std::vector<uint16_t> operations;
    std::vector<uint16_t> events;
    std::vector<uint16_t> device_properties;
    std::string manufacturer;
    std::string model;
    std::string device_version;
    std::string serial_number;

    bool supports(uint16_t opcode) const;
};

struct PtpObjectInfo {
    uint32_t storage_id = 0;
    uint16_t format = 0;
    uint32_t compressed_size = 0;
    uint16_t thumb_format = 0;
    uint32_t thumb_size = 0;
    uint32_t parent_object = 0;
    std::string filename;
    std::string capture_date;
};

// Still image formats we care about.
constexpr uint16_t kPtpFormatExifJpeg = 0x3801;
constexpr uint16_t kPtpFormatSonyRaw = 0xB101;

class PtpDevice {
public:
    explicit PtpDevice(UsbDevice* usb) : usb_(usb) {}

    // One PTP transaction: command block, optional data phase, response block.
    bool transact(uint16_t opcode,
                  const std::vector<uint32_t>& params,
                  const std::vector<uint8_t>* data_out,
                  std::vector<uint8_t>* data_in,
                  PtpResponse& response,
                  std::string& error,
                  unsigned timeout_ms = 8000);

    bool open_session(std::string& error);
    void close_session();
    bool session_open() const { return session_open_; }

    bool get_device_info(PtpDeviceInfo& info, std::string& error);
    bool get_object_info(uint32_t handle, PtpObjectInfo& info, std::string& error);
    bool get_object(uint32_t handle, std::vector<uint8_t>& data, std::string& error);

    // Reads the interrupt endpoint. Returns false with an empty code on timeout.
    bool poll_event(PtpEvent& event, unsigned timeout_ms, std::string& error);

    void reset();

    uint32_t session_id() const { return session_id_; }

private:
    bool write_container(uint16_t type, uint16_t code, uint32_t transaction_id,
                         const std::vector<uint32_t>& params, const uint8_t* payload,
                         size_t payload_len, unsigned timeout_ms, std::string& error);
    bool read_container(uint16_t& type, uint16_t& code, uint32_t& transaction_id,
                        std::vector<uint8_t>& payload, unsigned timeout_ms, std::string& error);
    bool ensure_bytes(size_t count, unsigned timeout_ms, std::string& error);

    UsbDevice* usb_ = nullptr;
    std::vector<uint8_t> rx_;
    uint32_t transaction_id_ = 0;
    uint32_t session_id_ = 0;
    bool session_open_ = false;
};

// Little-endian readers for PTP payloads.
uint8_t ptp_read_u8(const std::vector<uint8_t>& buf, size_t& offset, bool& ok);
uint16_t ptp_read_u16(const std::vector<uint8_t>& buf, size_t& offset, bool& ok);
uint32_t ptp_read_u32(const std::vector<uint8_t>& buf, size_t& offset, bool& ok);
uint64_t ptp_read_u64(const std::vector<uint8_t>& buf, size_t& offset, bool& ok);
std::string ptp_read_string(const std::vector<uint8_t>& buf, size_t& offset, bool& ok);
std::vector<uint16_t> ptp_read_u16_array(const std::vector<uint8_t>& buf, size_t& offset, bool& ok);
