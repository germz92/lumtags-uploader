#include "backend.h"

#include <cstdio>
#include <fstream>

namespace {

// Minimal JPEG (1x1) plus COM padding so the file is a valid still.
const unsigned char kTinyJpeg[] = {
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
    0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
    0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
    0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
    0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
    0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
    0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
    0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x14, 0x00, 0x01,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x03, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00,
    0x3F, 0x00, 0x7F, 0xFF, 0xD9
};

class SimulatorBackend : public ICameraBackend {
public:
    std::string name() const override { return "simulator"; }

    std::vector<CameraInfo> enumerate() override {
        return {{"sim-ilce-7m4", "ILCE-7M4", "Sony ILCE-7M4 (simulator)", "SIM000000"}};
    }

    bool connect(const std::string& device_id, const std::string& save_dir, std::string& error) override {
        if (!device_id.empty() && device_id != "sim-ilce-7m4") {
            error = "Unknown camera.";
            return false;
        }
        if (save_dir.empty()) {
            error = "Missing tether folder.";
            return false;
        }
        save_dir_ = save_dir;
        connected_ = true;
        emit_event("connected",
                   "\"id\":\"sim-ilce-7m4\",\"model\":\"ILCE-7M4\",\"name\":\"Sony ILCE-7M4 (simulator)\",\"serial\":\"SIM000000\"");
        return true;
    }

    void disconnect() override {
        if (connected_) {
            connected_ = false;
            emit_event("disconnected", "\"reason\":\"Host disconnect requested\"");
        }
    }

    CameraInfo current_camera() const override {
        return {"sim-ilce-7m4", "ILCE-7M4", "Sony ILCE-7M4 (simulator)", "SIM000000"};
    }

    bool simulate_shot(std::string& path, std::string& error) override {
        if (!connected_) {
            error = "Camera is not connected.";
            return false;
        }
        ++shot_;
        char filename[32];
        std::snprintf(filename, sizeof(filename), "DSC%05d.JPG", shot_);
        path = save_dir_ + "/" + filename;
        std::ofstream out(path, std::ios::binary);
        if (!out) {
            error = "Could not write JPEG.";
            return false;
        }
        out.write(reinterpret_cast<const char*>(kTinyJpeg), sizeof(kTinyJpeg));
        // Pad so Python gallery floor can still be tested via the Pillow simulator.
        std::string pad(64, 0);
        out.write(pad.data(), static_cast<std::streamsize>(pad.size()));
        out.close();
        std::string extra = std::string("\"path\":\"") + json_escape(path) + "\",\"filename\":\"" + filename + "\"";
        emit_event("image_ready", extra);
        return true;
    }

private:
    bool connected_ = false;
    std::string save_dir_;
    int shot_ = 0;
};

}  // namespace

ICameraBackend* create_simulator_backend() {
    return new SimulatorBackend();
}
