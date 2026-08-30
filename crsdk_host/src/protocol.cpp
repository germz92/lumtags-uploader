#include "protocol.h"

#include <cstdlib>
#include <iostream>
#include <sstream>

void emit_line(const std::string& json) {
    std::cout << json << std::endl;
}

std::string json_escape(const std::string& value) {
    std::string out;
    out.reserve(value.size() + 8);
    for (char c : value) {
        if (c == '\\' || c == '"') {
            out.push_back('\\');
        }
        if (c == '\n') {
            out += "\\n";
        } else {
            out.push_back(c);
        }
    }
    return out;
}

void emit_hello(const std::string& backend) {
    emit_line(std::string("{\"type\":\"hello\",\"backend\":\"") + backend + "\",\"version\":\"1.0\"}");
}

void emit_reply(int id, bool ok, const std::string& extra_json) {
    std::ostringstream oss;
    oss << "{\"type\":\"reply\",\"id\":" << id << ",\"ok\":" << (ok ? "true" : "false");
    if (!extra_json.empty()) {
        oss << "," << extra_json;
    }
    oss << "}";
    emit_line(oss.str());
}

void emit_event(const std::string& name, const std::string& extra_json) {
    std::ostringstream oss;
    oss << "{\"type\":\"event\",\"name\":\"" << name << "\"";
    if (!extra_json.empty()) {
        oss << "," << extra_json;
    }
    oss << "}";
    emit_line(oss.str());
}

bool read_command(std::string& cmd, int& id, std::string& raw) {
    if (!std::getline(std::cin, raw)) {
        return false;
    }
    cmd = json_get_string(raw, "cmd");
    std::string id_key = "\"id\"";
    auto pos = raw.find(id_key);
    id = 0;
    if (pos != std::string::npos) {
        pos = raw.find(':', pos);
        if (pos != std::string::npos) {
            id = std::atoi(raw.c_str() + pos + 1);
        }
    }
    return true;
}

std::string json_get_string(const std::string& raw, const std::string& key) {
    std::string needle = "\"" + key + "\"";
    auto pos = raw.find(needle);
    if (pos == std::string::npos) {
        return "";
    }
    pos = raw.find(':', pos);
    if (pos == std::string::npos) {
        return "";
    }
    pos = raw.find('"', pos);
    if (pos == std::string::npos) {
        return "";
    }
    auto end = raw.find('"', pos + 1);
    if (end == std::string::npos) {
        return "";
    }
    return raw.substr(pos + 1, end - pos - 1);
}
