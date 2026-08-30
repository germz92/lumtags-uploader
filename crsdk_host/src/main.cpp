#include "backend.h"
#include "protocol.h"

#include <memory>
#include <sstream>
#include <string>

int main(int argc, char** argv) {
    bool want_simulator = false;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--simulator") {
            want_simulator = true;
        }
    }

    std::unique_ptr<ICameraBackend> backend;
#ifdef CRSDK_AVAILABLE
    if (want_simulator) {
        backend.reset(create_simulator_backend());
    } else {
        backend.reset(create_crsdk_backend());
    }
#else
    (void)want_simulator;
    backend.reset(create_simulator_backend());
#endif

    emit_hello(backend->name());

    std::string cmd;
    int id = 0;
    std::string raw;
    while (read_command(cmd, id, raw)) {
        if (cmd == "ping") {
            emit_reply(id, true, std::string("\"data\":{\"backend\":\"") + backend->name() + "\"}");
        } else if (cmd == "enumerate") {
            auto cameras = backend->enumerate();
            std::ostringstream data;
            data << "\"data\":{\"cameras\":[";
            for (size_t i = 0; i < cameras.size(); ++i) {
                if (i) {
                    data << ",";
                }
                data << "{\"id\":\"" << json_escape(cameras[i].id)
                     << "\",\"model\":\"" << json_escape(cameras[i].model)
                     << "\",\"name\":\"" << json_escape(cameras[i].name)
                     << "\",\"serial\":\"" << json_escape(cameras[i].serial) << "\"}";
            }
            data << "]}";
            emit_reply(id, true, data.str());
        } else if (cmd == "connect") {
            std::string error;
            bool ok = backend->connect(
                json_get_string(raw, "device_id"),
                json_get_string(raw, "save_dir"),
                error);
            if (ok) {
                auto cam = backend->current_camera();
                std::ostringstream data;
                data << "\"data\":{\"camera\":{\"id\":\"" << json_escape(cam.id)
                     << "\",\"model\":\"" << json_escape(cam.model)
                     << "\",\"name\":\"" << json_escape(cam.name)
                     << "\",\"serial\":\"" << json_escape(cam.serial) << "\"}}";
                emit_reply(id, true, data.str());
            } else {
                emit_reply(id, false, std::string("\"error\":\"") + json_escape(error) + "\"");
            }
        } else if (cmd == "disconnect") {
            backend->disconnect();
            emit_reply(id, true);
        } else if (cmd == "simulate_shot") {
            std::string path;
            std::string error;
            bool ok = backend->simulate_shot(path, error);
            if (ok) {
                emit_reply(id, true, std::string("\"data\":{\"path\":\"") + json_escape(path) + "\"}");
            } else {
                emit_reply(id, false, std::string("\"error\":\"") + json_escape(error) + "\"");
            }
        } else if (cmd == "shutdown") {
            backend->disconnect();
            emit_reply(id, true);
            break;
        } else if (cmd.empty()) {
            continue;
        } else {
            emit_reply(id, false, "\"error\":\"Unknown command\"");
        }
    }
    return 0;
}
