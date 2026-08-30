#pragma once

#include "protocol.h"

#include <string>
#include <vector>

class ICameraBackend {
public:
    virtual ~ICameraBackend() = default;
    virtual std::string name() const = 0;
    virtual std::vector<CameraInfo> enumerate() = 0;
    virtual bool connect(const std::string& device_id, const std::string& save_dir, std::string& error) = 0;
    virtual void disconnect() = 0;
    virtual bool simulate_shot(std::string& path, std::string& error) = 0;
    virtual CameraInfo current_camera() const { return {}; }
};

ICameraBackend* create_simulator_backend();

#ifdef CRSDK_AVAILABLE
ICameraBackend* create_crsdk_backend();
#endif
