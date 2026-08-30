#pragma once

#include <string>
#include <vector>

struct CameraInfo {
    std::string id;
    std::string model;
    std::string name;
    std::string serial;
};

void emit_line(const std::string& json);
void emit_hello(const std::string& backend);
void emit_reply(int id, bool ok, const std::string& extra_json = "");
void emit_event(const std::string& name, const std::string& extra_json = "");
bool read_command(std::string& cmd, int& id, std::string& raw);
std::string json_get_string(const std::string& raw, const std::string& key);
std::string json_escape(const std::string& value);
