#ifdef PTP_BACKEND_AVAILABLE

#include "backend.h"
#include "ptp.h"
#include "ptp_sony.h"
#include "usb_device.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr int kPollIntervalMs = 200;
constexpr int kEventDrainMs = 60;
constexpr int kReconnectDelayMs = 1000;

std::string extension_for_format(uint16_t format) {
    switch (format) {
        case kPtpFormatSonyRaw:
            return ".ARW";
        case kPtpFormatExifJpeg:
        default:
            return ".JPG";
    }
}

std::filesystem::path unique_path(const std::filesystem::path& dir, const std::string& filename,
                                  uint16_t format) {
    std::string base = filename;
    if (base.empty()) {
        base = "capture" + extension_for_format(format);
    }
    std::filesystem::path candidate = dir / base;
    if (!std::filesystem::exists(candidate)) {
        return candidate;
    }
    const std::filesystem::path stem = std::filesystem::path(base).stem();
    const std::filesystem::path ext = std::filesystem::path(base).extension();
    for (int suffix = 1; suffix < 10000; ++suffix) {
        char tail[32];
        std::snprintf(tail, sizeof(tail), "_%d", suffix);
        candidate = dir / (stem.string() + tail + ext.string());
        if (!std::filesystem::exists(candidate)) {
            return candidate;
        }
    }
    return dir / base;
}

class PtpBackend : public ICameraBackend {
public:
    PtpBackend() {
        const char* debug = std::getenv("LUMTAGS_USB_DEBUG");
        usb_set_verbose(debug && *debug && std::string(debug) != "0");
    }

    ~PtpBackend() override { disconnect(); }

    std::string name() const override { return "ptp"; }

    std::vector<CameraInfo> enumerate() override {
        // Read-only: this must never claim the interface, or a plain scan would
        // knock ptpcamerad over for no reason.
        std::vector<CameraInfo> cameras;
        int index = 0;
        for (const auto& device : usb_list_devices(kSonyVendorId)) {
            bool has_ptp = false;
            for (const auto& iface : device.interfaces) {
                if (iface.interface_class == kStillImageClass) {
                    has_ptp = true;
                }
            }
            if (!has_ptp) {
                continue;
            }
            CameraInfo info;
            info.id = std::to_string(index++);
            info.model = device.product.empty() ? "Sony camera" : device.product;
            info.name = info.model;
            info.serial = device.serial;
            cameras.push_back(info);
        }
        return cameras;
    }

    CameraInfo current_camera() const override {
        std::lock_guard<std::mutex> lock(state_mu_);
        return camera_;
    }

    bool connect(const std::string& device_id, const std::string& save_dir,
                 std::string& error) override {
        disconnect();

        save_dir_ = save_dir;
        std::error_code ec;
        std::filesystem::create_directories(save_dir_, ec);

        requested_disconnect_ = false;
        if (!open_camera(error)) {
            close_camera();
            return false;
        }

        running_ = true;
        worker_ = std::thread([this] { worker(); });
        return true;
    }

    void disconnect() override {
        requested_disconnect_ = true;
        running_ = false;
        if (worker_.joinable()) {
            worker_.join();
        }
        close_camera();
    }

    bool simulate_shot(std::string& /*path*/, std::string& error) override {
        error = "simulate_shot is only available on the simulator backend.";
        return false;
    }

private:
    bool open_camera(std::string& error) {
        usb_ = usb_open(0, error);
        if (!usb_) {
            return false;
        }
        if (!usb_->claim(error)) {
            usb_.reset();
            return false;
        }

        const auto& info = usb_->info();
        {
            std::lock_guard<std::mutex> lock(state_mu_);
            camera_.id = "0";
            camera_.model = info.product.empty() ? "Sony camera" : info.product;
            camera_.name = camera_.model;
            camera_.serial = info.serial;
        }
        emit_event("camera_found", camera_json());

        ptp_.reset(new PtpDevice(usb_.get()));
        if (!ptp_->open_session(error)) {
            return false;
        }

        PtpDeviceInfo device_info;
        if (ptp_->get_device_info(device_info, error)) {
            std::lock_guard<std::mutex> lock(state_mu_);
            if (!device_info.model.empty()) {
                camera_.model = device_info.model;
                camera_.name = device_info.model;
            }
            if (!device_info.serial_number.empty()) {
                camera_.serial = device_info.serial_number;
            }
        }

        sony_.reset(new SonyCamera(ptp_.get()));
        if (!sony_->handshake(error)) {
            return false;
        }
        std::fprintf(stderr, "ptp: %s ready, Mode %u\n", camera_.model.c_str(),
                     sony_->sdio_version());

        std::string prop_error;
        sony_->refresh_properties(prop_error);

        emit_event("connected", camera_json());
        return true;
    }

    void close_camera() {
        if (ptp_) {
            ptp_->close_session();
            ptp_.reset();
        }
        sony_.reset();
        if (usb_) {
            usb_->release();
            usb_.reset();
        }
    }

    // No "name" key here: the envelope already uses "name" for the event, and a
    // duplicate would shadow it when the app parses the line.
    std::string camera_json() const {
        std::lock_guard<std::mutex> lock(state_mu_);
        return std::string("\"id\":\"") + json_escape(camera_.id) + "\",\"model\":\"" +
               json_escape(camera_.model) + "\",\"serial\":\"" + json_escape(camera_.serial) + "\"";
    }

    void worker() {
        while (running_) {
            // recover() tears the session down, so never assume it is still here.
            if (!ptp_ || !sony_) {
                return;
            }
            std::string error;

            // Keep the event pipe drained so the camera's queue never backs up.
            PtpEvent event;
            ptp_->poll_event(event, kEventDrainMs, error);

            if (!sony_->refresh_properties(error)) {
                if (!running_) {
                    return;
                }
                std::fprintf(stderr, "ptp: lost the camera (%s)\n", error.c_str());
                if (!recover()) {
                    return;
                }
                continue;
            }

            bool drained_any = false;
            while (running_ && sony_->has_pending_image()) {
                if (!pull_one_image()) {
                    break;
                }
                drained_any = true;
                std::string refresh_error;
                if (!sony_->refresh_properties(refresh_error)) {
                    break;
                }
            }

            if (!drained_any) {
                std::this_thread::sleep_for(std::chrono::milliseconds(kPollIntervalMs));
            }
        }
    }

    bool pull_one_image() {
        PtpObjectInfo object;
        std::vector<uint8_t> bytes;
        std::string error;
        if (!sony_->fetch_pending_image(object, bytes, error)) {
            std::fprintf(stderr, "ptp: could not pull the photo (%s)\n", error.c_str());
            return false;
        }
        if (bytes.size() < 1024) {
            return false;
        }

        const std::filesystem::path path =
            unique_path(save_dir_, object.filename, object.format);
        std::ofstream out(path, std::ios::binary);
        if (!out) {
            emit_event("error", "\"message\":\"Could not write the photo to disk.\"");
            return false;
        }
        out.write(reinterpret_cast<const char*>(bytes.data()),
                  static_cast<std::streamsize>(bytes.size()));
        out.close();

        std::fprintf(stderr, "ptp: saved %s (%zu bytes)\n", path.string().c_str(), bytes.size());
        const std::string extra = std::string("\"path\":\"") + json_escape(path.string()) +
                                  "\",\"filename\":\"" +
                                  json_escape(path.filename().string()) + "\"";
        emit_event("image_ready", extra);
        return true;
    }

    void emit_reconnecting(int attempt) {
        emit_event("reconnecting",
                   "\"reason\":\"Camera disconnected\",\"attempt\":" +
                       std::to_string(attempt > 0 ? attempt : 1));
    }

    // The camera went away (power off, cable, sleep). Stay in this process and
    // wait for it: a fresh host is what the SDK needed, not us.
    // Returns false when we gave up because the app asked us to stop.
    bool recover() {
        emit_reconnecting(0);
        close_camera();

        int attempt = 0;
        while (running_ && !requested_disconnect_) {
            std::this_thread::sleep_for(std::chrono::milliseconds(kReconnectDelayMs));
            if (!running_ || requested_disconnect_) {
                break;
            }
            ++attempt;

            if (enumerate().empty()) {
                // Keep the app's status honest while we wait, without flooding it.
                if (attempt % 10 == 0) {
                    emit_reconnecting(attempt);
                    std::fprintf(stderr, "ptp: still waiting for the camera\n");
                }
                continue;
            }
            emit_reconnecting(attempt);

            std::string error;
            if (open_camera(error)) {
                std::fprintf(stderr, "ptp: camera is back after %d tries\n", attempt);
                return true;
            }
            close_camera();
            if (attempt % 15 == 0) {
                std::fprintf(stderr, "ptp: reconnect attempt %d failed (%s)\n", attempt,
                             error.c_str());
            }
        }
        return false;
    }

    std::unique_ptr<UsbDevice> usb_;
    std::unique_ptr<PtpDevice> ptp_;
    std::unique_ptr<SonyCamera> sony_;
    std::filesystem::path save_dir_;
    mutable std::mutex state_mu_;
    CameraInfo camera_;
    std::thread worker_;
    std::atomic<bool> running_{false};
    std::atomic<bool> requested_disconnect_{false};
};

}  // namespace

ICameraBackend* create_ptp_backend() { return new PtpBackend(); }

#endif  // PTP_BACKEND_AVAILABLE
