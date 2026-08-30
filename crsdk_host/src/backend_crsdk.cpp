#ifdef CRSDK_AVAILABLE

#include "backend.h"

// Sony Camera Remote SDK v2.x — do not commit the SDK itself.
// Unpack RemoteCli.zip and point CRSDK_ROOT at that folder.

#if defined(__has_include)
#  if __has_include("CameraRemote_SDK.h")
#    include "CameraRemote_SDK.h"
#    include "IDeviceCallback.h"
#  elif __has_include("CRSDK/CameraRemote_SDK.h")
#    include "CRSDK/CameraRemote_SDK.h"
#    include "CRSDK/IDeviceCallback.h"
#  else
#    include "CameraRemote_SDK.h"
#    include "IDeviceCallback.h"
#  endif
#else
#  include "CameraRemote_SDK.h"
#  include "IDeviceCallback.h"
#endif

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cctype>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

namespace SDK = SCRSDK;

namespace {

#ifdef _WIN32
std::string cr_to_utf8(const CrChar* text) {
    if (!text) {
        return "";
    }
    int count = WideCharToMultiByte(CP_UTF8, 0, text, -1, nullptr, 0, nullptr, nullptr);
    if (count <= 1) {
        return "";
    }
    std::string out(static_cast<size_t>(count - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, text, -1, out.data(), count, nullptr, nullptr);
    return out;
}

std::wstring utf8_to_wide(const std::string& text) {
    if (text.empty()) {
        return L"";
    }
    int count = MultiByteToWideChar(CP_UTF8, 0, text.c_str(), -1, nullptr, 0);
    if (count <= 1) {
        return L"";
    }
    std::wstring out(static_cast<size_t>(count - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.c_str(), -1, out.data(), count);
    return out;
}
#else
std::string cr_to_utf8(const CrChar* text) {
    return text ? std::string(text) : "";
}
#endif

class CrSdkBackend;

class CrCallback : public SDK::IDeviceCallback {
public:
    explicit CrCallback(CrSdkBackend* owner) : owner_(owner) {}

    void OnConnected(SDK::DeviceConnectionVersioin version) override;
    void OnDisconnected(CrInt32u error) override;
    void OnPropertyChanged() override;
    void OnCompleteDownload(CrChar* filename, CrInt32u type) override;
    void OnNotifyPostViewImage(CrChar* filename, CrInt32u size) override;
    void OnNotifyRemoteTransferResult(CrInt32u notify, CrInt32u per, CrChar* filename) override;
    void OnError(CrInt32u error) override;
    void OnWarning(CrInt32u warning) override;

private:
    CrSdkBackend* owner_;
};

class CrSdkBackend : public ICameraBackend {
public:
    CrSdkBackend() {
        if (!SDK::Init()) {
            std::fprintf(stderr, "crsdk: Init failed\n");
        }
    }

    ~CrSdkBackend() override {
        disconnect();
        SDK::Release();
    }

    std::string name() const override { return "crsdk"; }

    std::vector<CameraInfo> enumerate() override {
        std::vector<CameraInfo> cameras;
        SDK::ICrEnumCameraObjectInfo* objects = wait_enum_objects();
        if (!objects) {
            return cameras;
        }
        fill_cameras(objects, cameras);
        objects->Release();
        return cameras;
    }

    CameraInfo current_camera() const override {
        return {camera_id_, camera_model_, camera_model_, ""};
    }

    bool connect(const std::string& device_id, const std::string& save_dir, std::string& error) override {
        disconnect();
        unsigned index = 0;
        if (!device_id.empty()) {
            try {
                index = static_cast<unsigned>(std::stoul(device_id));
            } catch (...) {
                index = 0;
            }
        }
        save_dir_ = save_dir;
        try {
            std::filesystem::create_directories(save_dir_);
        } catch (...) {
        }

        requested_disconnect_ = false;
        session_conflict_ = false;
        session_ready_ = false;
        lost_emitted_ = false;
        dest_applied_ = false;
        dest_hinted_ = false;
        transfer_applied_ = false;
        settings_retry_started_ = false;
        saveinfo_logged_ = false;

        if (!open_remote_session(index, error)) {
            return false;
        }
        start_settings_retry();
        return true;
    }

    void disconnect() override {
        requested_disconnect_ = true;
        cv_.notify_all();
        if (settings_thread_.joinable() && settings_thread_.get_id() != std::this_thread::get_id()) {
            settings_thread_.join();
        }
        if (recover_thread_.joinable() && recover_thread_.get_id() != std::this_thread::get_id()) {
            recover_thread_.join();
        }
        if (handle_ != 0) {
            SDK::Disconnect(handle_);
            SDK::ReleaseDevice(handle_);
            handle_ = 0;
        }
        callback_.reset();
        session_ready_ = false;
        dest_applied_ = false;
        dest_hinted_ = false;
        transfer_applied_ = false;
        settings_retry_started_ = false;
    }

    bool simulate_shot(std::string& /*path*/, std::string& error) override {
        error = "simulate_shot is only available on the simulator backend.";
        return false;
    }

    void handle_connected() {
        session_ready_ = true;
        cv_.notify_all();
        if (session_conflict_) {
            std::fprintf(stderr, "crsdk: OnConnected ignored, leftover session needs reset\n");
            return;
        }
        lost_emitted_ = false;
        std::string extra = std::string("\"id\":\"") + json_escape(camera_id_) +
                            "\",\"model\":\"" + json_escape(camera_model_) +
                            "\",\"name\":\"" + json_escape(camera_model_) + "\"";
        emit_event("connected", extra);
    }

    void handle_disconnected(CrInt32u error) {
        std::fprintf(stderr, "crsdk: OnDisconnected 0x%X\n", static_cast<unsigned>(error));
        session_ready_ = false;
        dest_applied_ = false;
        transfer_applied_ = false;
        if (!requested_disconnect_) {
            emit_lost_once("USB disconnected");
        }
    }

    void handle_property_changed() {
        if (handle_ != 0 && (!dest_applied_ || !transfer_applied_)) {
            apply_save_settings();
        }
    }

    void handle_file(const CrChar* filename) {
        if (!filename) {
            std::fprintf(stderr, "crsdk: download callback with empty path\n");
            return;
        }
        std::string path = cr_to_utf8(filename);
        std::fprintf(stderr, "crsdk: file ready %s\n", path.c_str());
        if (!session_ready_) {
            emit_reconnected();
        }
        emit_saved_path(path);
    }

    void handle_postview(CrChar* filename, CrInt32u size) {
        std::fprintf(stderr, "crsdk: postview size=%u has_path=%d\n",
                     static_cast<unsigned>(size), filename ? 1 : 0);
        if (filename) {
            handle_file(filename);
            return;
        }
        if (size > 0) {
            pull_postview(size);
        }
    }

    void handle_error(CrInt32u error) {
        std::fprintf(stderr, "crsdk: OnError 0x%X\n", static_cast<unsigned>(error));
        if (requested_disconnect_) {
            return;
        }
        if (error == SDK::CrError_Connect_SessionAlreadyOpened) {
            std::fprintf(stderr, "crsdk: leftover session, resetting\n");
            session_conflict_ = true;
            cv_.notify_all();
            return;
        }
        if (error == SDK::CrError_Connect_Disconnected || error == SDK::CrError_Connect_TimeOut) {
            handle_link_lost();
        }
    }

    void handle_warning(CrInt32u warning) {
        std::fprintf(stderr, "crsdk: OnWarning 0x%X\n", static_cast<unsigned>(warning));
        if (warning == SDK::CrWarning_Connect_Reconnecting) {
            handle_link_lost();
        } else if (warning == SDK::CrWarning_Connect_Reconnected) {
            std::fprintf(stderr, "crsdk: SDK reconnected\n");
            dest_applied_ = false;
            transfer_applied_ = false;
            emit_reconnected();
            start_settings_retry();
        } else if (warning == SDK::CrNotify_Captured_Event) {
            std::fprintf(stderr, "crsdk: shutter fired, waiting for the JPEG\n");
            std::thread([this] { collect_after_capture(); }).detach();
        }
    }

private:
    void handle_link_lost() {
        session_ready_ = false;
        dest_applied_ = false;
        transfer_applied_ = false;
        emit_event("reconnecting", "\"reason\":\"USB disconnected\"");
        bool expected = false;
        if (!recover_watch_started_.compare_exchange_strong(expected, true)) {
            return;
        }
        if (recover_thread_.joinable() && recover_thread_.get_id() != std::this_thread::get_id()) {
            recover_thread_.join();
        }
        recover_thread_ = std::thread([this] { recover_watch(); });
    }

    void recover_watch() {
        {
            std::unique_lock<std::mutex> lock(mu_);
            const bool recovered = cv_.wait_for(lock, std::chrono::seconds(12), [&] {
                return requested_disconnect_.load() || session_ready_.load();
            });
            recover_watch_started_ = false;
            if (recovered) {
                return;
            }
        }
        if (!requested_disconnect_ && !session_ready_) {
            emit_lost_once("USB disconnected");
        }
    }

    void emit_reconnected() {
        session_ready_ = true;
        lost_emitted_ = false;
        cv_.notify_all();
        std::string extra = std::string("\"id\":\"") + json_escape(camera_id_) +
                            "\",\"model\":\"" + json_escape(camera_model_) +
                            "\",\"name\":\"" + json_escape(camera_model_) + "\"";
        emit_event("connected", extra);
    }

    void emit_lost_once(const char* reason) {
        bool expected = false;
        if (!lost_emitted_.compare_exchange_strong(expected, true)) {
            return;
        }
        std::string extra = std::string("\"reason\":\"") + json_escape(reason) + "\"";
        emit_event("disconnected", extra);
    }

    void close_handle() {
        if (handle_ != 0) {
            SDK::Disconnect(handle_);
            SDK::ReleaseDevice(handle_);
            handle_ = 0;
        }
    }

    void recycle_sdk() {
        close_handle();
        callback_.reset();
        SDK::Release();
        std::this_thread::sleep_for(std::chrono::milliseconds(800));
        SDK::Init();
        callback_ = std::make_unique<CrCallback>(this);
    }

    SDK::ICrEnumCameraObjectInfo* enum_objects_once() {
        SDK::ICrEnumCameraObjectInfo* objects = nullptr;
        auto err = SDK::EnumCameraObjects(&objects, 3);
        if (err != SDK::CrError_None) {
            std::fprintf(stderr, "crsdk: EnumCameraObjects failed 0x%X\n", static_cast<unsigned>(err));
            if (objects) {
                objects->Release();
            }
            return nullptr;
        }
        if (!objects || objects->GetCount() == 0) {
            if (objects) {
                objects->Release();
            }
            return nullptr;
        }
        return objects;
    }

    SDK::ICrEnumCameraObjectInfo* wait_enum_objects() {
        for (int attempt = 1; attempt <= 5; ++attempt) {
            SDK::ICrEnumCameraObjectInfo* objects = enum_objects_once();
            if (objects) {
                if (attempt > 1) {
                    std::fprintf(stderr, "crsdk: camera found on enum try %d\n", attempt);
                }
                return objects;
            }
            std::fprintf(stderr, "crsdk: no camera on enum try %d\n", attempt);
            if (attempt == 2) {
                std::fprintf(stderr, "crsdk: recycling SDK after empty enum\n");
                recycle_sdk();
            } else if (attempt < 5) {
                std::this_thread::sleep_for(std::chrono::milliseconds(600));
            }
        }
        return nullptr;
    }

    static void fill_cameras(SDK::ICrEnumCameraObjectInfo* objects, std::vector<CameraInfo>& cameras) {
        const auto count = objects->GetCount();
        for (CrInt32u i = 0; i < count; ++i) {
            const auto* info = objects->GetCameraObjectInfo(i);
            if (!info) {
                continue;
            }
            CameraInfo cam;
            cam.id = std::to_string(i);
            cam.model = cr_to_utf8(info->GetModel());
            cam.name = cam.model.empty() ? cr_to_utf8(info->GetName()) : cam.model;
            if (cam.name.empty()) {
                cam.name = "Sony camera";
            }
            cameras.push_back(cam);
        }
    }

    bool connect_index(unsigned index, std::string& error) {
        SDK::ICrEnumCameraObjectInfo* objects = wait_enum_objects();
        if (!objects) {
            error = "No Sony camera found.";
            return false;
        }
        if (index >= objects->GetCount()) {
            index = 0;
        }
        const auto* info = objects->GetCameraObjectInfo(index);
        camera_id_ = std::to_string(index);
        camera_model_ = cr_to_utf8(info->GetModel());
        if (camera_model_.empty()) {
            camera_model_ = cr_to_utf8(info->GetName());
        }
        if (camera_model_.empty()) {
            camera_model_ = "Sony camera";
        }
        std::string found = std::string("\"id\":\"") + json_escape(camera_id_) +
                            "\",\"model\":\"" + json_escape(camera_model_) +
                            "\",\"name\":\"" + json_escape(camera_model_) + "\"";
        emit_event("camera_found", found);
        if (!callback_) {
            callback_ = std::make_unique<CrCallback>(this);
        }
        session_ready_ = false;
        session_conflict_ = false;
        auto err = SDK::Connect(
            const_cast<SDK::ICrCameraObjectInfo*>(info),
            callback_.get(),
            &handle_,
            SDK::CrSdkControlMode_Remote,
            SDK::CrReconnecting_ON);
        objects->Release();
        if (err != SDK::CrError_None || handle_ == 0) {
            std::fprintf(stderr, "crsdk: Connect failed 0x%X\n", static_cast<unsigned>(err));
            error = "Connect failed. Set USB mode to Remote Shoot (PC Remote).";
            handle_ = 0;
            return false;
        }
        std::unique_lock<std::mutex> lock(mu_);
        cv_.wait_for(lock, std::chrono::seconds(10), [&] {
            return session_ready_.load() || session_conflict_.load();
        });
        return true;
    }

    bool open_remote_session(unsigned index, std::string& error) {
        if (!connect_index(index, error)) {
            return false;
        }
        if (session_conflict_) {
            std::fprintf(stderr, "crsdk: recycling SDK after leftover session\n");
            recycle_sdk();
            requested_disconnect_ = false;
            if (!connect_index(index, error)) {
                return false;
            }
        }
        if (session_conflict_ || !session_ready_) {
            close_handle();
            error = "Camera still has an old PC Remote session. Turn the camera off and on, then Scan again.";
            return false;
        }
        return true;
    }

    void start_settings_retry() {
        bool expected = false;
        if (!settings_retry_started_.compare_exchange_strong(expected, true)) {
            return;
        }
        if (settings_thread_.joinable() && settings_thread_.get_id() != std::this_thread::get_id()) {
            settings_thread_.join();
        }
        settings_thread_ = std::thread([this] {
            for (int attempt = 0; attempt < 8 && !requested_disconnect_ && handle_ != 0; ++attempt) {
                if (attempt > 0) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(400));
                }
                apply_save_settings();
                if (dest_applied_ && transfer_applied_) {
                    break;
                }
            }
            settings_retry_started_ = false;
        });
    }

    void apply_save_settings() {
        std::lock_guard<std::mutex> lock(mu_);
        if (handle_ == 0 || save_dir_.empty()) {
            return;
        }
#ifdef _WIN32
        std::wstring wide_dir = utf8_to_wide(save_dir_);
        CrChar prefix[] = L"";
        auto save_status = SDK::SetSaveInfo(handle_, wide_dir.data(), prefix, -1);
#else
        CrChar prefix[] = "";
        auto save_status = SDK::SetSaveInfo(handle_, const_cast<CrChar*>(save_dir_.c_str()), prefix, -1);
#endif
        if (save_status != SDK::CrError_None) {
            std::fprintf(stderr, "crsdk: SetSaveInfo failed 0x%X dir=%s\n",
                         static_cast<unsigned>(save_status), save_dir_.c_str());
        } else if (!saveinfo_logged_) {
            saveinfo_logged_ = true;
            std::fprintf(stderr, "crsdk: SetSaveInfo ok %s\n", save_dir_.c_str());
        }

        SDK::SetDeviceSetting(handle_, SDK::Setting_Key_EnablePostView, SDK::CrDeviceSetting_Enable);
        SDK::SetDeviceSetting(
            handle_,
            SDK::Setting_Key_PostViewTransferringType,
            SDK::CrPostViewTransferring_UserSelect_File);

        CrInt32u codes[] = {
            SDK::CrDeviceProperty_StillImageStoreDestination,
            SDK::CrDeviceProperty_PriorityKeySettings,
            SDK::CrDeviceProperty_RAW_J_PC_Save_Image,
            SDK::CrDeviceProperty_Still_Image_Trans_Size,
            SDK::CrDeviceProperty_RemoteSaveImageSize,
        };
        SDK::CrDeviceProperty* props = nullptr;
        CrInt32 nprop = 0;
        auto get_status = SDK::GetSelectDeviceProperties(
            handle_, static_cast<CrInt32u>(sizeof(codes) / sizeof(codes[0])), codes, &props, &nprop);
        if (get_status != SDK::CrError_None || !props) {
            get_status = SDK::GetDeviceProperties(handle_, &props, &nprop);
        }
        if (get_status != SDK::CrError_None || !props) {
            std::fprintf(stderr, "crsdk: properties not ready 0x%X\n", static_cast<unsigned>(get_status));
            return;
        }
        std::fprintf(stderr, "crsdk: loaded %d properties\n", static_cast<int>(nprop));

        for (CrInt32 i = 0; i < nprop; ++i) {
            auto& prop = props[i];
            if (prop.GetCode() == SDK::CrDeviceProperty_StillImageStoreDestination) {
                const auto current = static_cast<SDK::CrStillImageStoreDestination>(prop.GetCurrentValue());
                std::fprintf(stderr, "crsdk: save dest now %u writable %d\n",
                             static_cast<unsigned>(current),
                             prop.IsSetEnableCurrentValue() ? 1 : 0);
                if (current == SDK::CrStillImageStoreDestination_HostPC ||
                    current == SDK::CrStillImageStoreDestination_HostPCAndMemoryCard) {
                    dest_applied_ = true;
                } else if (prop.IsSetEnableCurrentValue()) {
                    SDK::CrDeviceProperty dest = prop;
                    dest.SetCurrentValue(SDK::CrStillImageStoreDestination_HostPCAndMemoryCard);
                    auto dest_status = SDK::SetDeviceProperty(handle_, &dest);
                    if (dest_status == SDK::CrError_None) {
                        dest_applied_ = true;
                    } else {
                        std::fprintf(stderr, "crsdk: StillImageStoreDestination failed 0x%X\n",
                                     static_cast<unsigned>(dest_status));
                    }
                } else if (!dest_hinted_) {
                    dest_hinted_ = true;
                    emit_event(
                        "error",
                        "\"message\":\"Set the camera Still Image Save Destination to Computer+Memory card\"");
                }
            } else if (prop.GetCode() == SDK::CrDeviceProperty_PriorityKeySettings &&
                       prop.IsSetEnableCurrentValue()) {
                SDK::CrDeviceProperty priority = prop;
                priority.SetCurrentValue(SDK::CrPriorityKey_PCRemote);
                SDK::SetDeviceProperty(handle_, &priority);
            } else if (prop.GetCode() == SDK::CrDeviceProperty_RAW_J_PC_Save_Image) {
                std::fprintf(stderr, "crsdk: RAW+J PC save now %llu writable %d\n",
                             static_cast<unsigned long long>(prop.GetCurrentValue()),
                             prop.IsSetEnableCurrentValue() ? 1 : 0);
                if (prop.IsSetEnableCurrentValue() &&
                    prop.GetCurrentValue() != SDK::CrPropertyRAWJPCSaveImage_JPEGOnly &&
                    prop.GetCurrentValue() != SDK::CrPropertyRAWJPCSaveImage_RAWAndJPEG) {
                    SDK::CrDeviceProperty rawj = prop;
                    rawj.SetCurrentValue(SDK::CrPropertyRAWJPCSaveImage_JPEGOnly);
                    auto rawj_status = SDK::SetDeviceProperty(handle_, &rawj);
                    std::fprintf(stderr, "crsdk: RAW+J PC save JPEG-only 0x%X\n",
                                 static_cast<unsigned>(rawj_status));
                }
                transfer_applied_ = true;
            } else if (prop.GetCode() == SDK::CrDeviceProperty_Still_Image_Trans_Size &&
                       prop.IsSetEnableCurrentValue()) {
                SDK::CrDeviceProperty size = prop;
                size.SetCurrentValue(SDK::CrPropertyStillImageTransSize_Original);
                SDK::SetDeviceProperty(handle_, &size);
            } else if (prop.GetCode() == SDK::CrDeviceProperty_RemoteSaveImageSize &&
                       prop.IsSetEnableCurrentValue()) {
                SDK::CrDeviceProperty size = prop;
                size.SetCurrentValue(SDK::CrRemoteSaveImageSize_LargeSize);
                SDK::SetDeviceProperty(handle_, &size);
            }
        }
        SDK::ReleaseDeviceProperties(handle_, props);
    }

    void emit_saved_path(const std::string& path) {
        if (path.empty()) {
            return;
        }
        {
            std::lock_guard<std::mutex> lock(files_mu_);
            if (!emitted_files_.insert(path).second) {
                return;
            }
        }
        std::string extra = std::string("\"path\":\"") + json_escape(path) + "\",\"filename\":\"" +
                            json_escape(path.substr(path.find_last_of("/\\") + 1)) + "\"";
        emit_event("image_ready", extra);
    }

    bool scan_new_jpegs() {
        bool found = false;
        std::error_code ec;
        for (const auto& entry : std::filesystem::directory_iterator(save_dir_, ec)) {
            if (!entry.is_regular_file(ec)) {
                continue;
            }
            auto ext = entry.path().extension().string();
            for (char& c : ext) {
                c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            }
            if (ext != ".jpg" && ext != ".jpeg") {
                continue;
            }
            if (entry.file_size(ec) < 1024) {
                continue;
            }
            emit_saved_path(entry.path().string());
            found = true;
        }
        return found;
    }

    void pull_postview(CrInt32u size) {
        if (handle_ == 0 || size == 0 || size > 80 * 1024 * 1024) {
            return;
        }
        std::vector<CrInt8u> buffer(size);
        auto err = SDK::PullPostViewImage(handle_, buffer.data(), size);
        if (err != SDK::CrError_None) {
            std::fprintf(stderr, "crsdk: PullPostViewImage failed 0x%X size=%u\n",
                         static_cast<unsigned>(err), static_cast<unsigned>(size));
            return;
        }
        write_pulled_jpeg(buffer.data(), buffer.size());
    }

    void write_pulled_jpeg(const CrInt8u* data, size_t size) {
        if (!data || size < 1024) {
            return;
        }
        const int index = ++shot_index_;
        char name[32];
        std::snprintf(name, sizeof(name), "DSC%05d.JPG", index);
        std::filesystem::path path = std::filesystem::path(save_dir_) / name;
        std::ofstream out(path, std::ios::binary);
        if (!out) {
            std::fprintf(stderr, "crsdk: could not write %s\n", path.string().c_str());
            return;
        }
        out.write(reinterpret_cast<const char*>(data), static_cast<std::streamsize>(size));
        out.close();
        std::fprintf(stderr, "crsdk: wrote pulled JPEG %s\n", path.string().c_str());
        emit_saved_path(path.string());
    }

    void collect_after_capture() {
        for (int attempt = 0; attempt < 12 && !requested_disconnect_; ++attempt) {
            std::this_thread::sleep_for(std::chrono::milliseconds(400));
            if (scan_new_jpegs()) {
                return;
            }
            CrInt32u codes[] = {SDK::CrDeviceProperty_PullPostViewImageStatus};
            SDK::CrDeviceProperty* props = nullptr;
            CrInt32 nprop = 0;
            if (SDK::GetSelectDeviceProperties(handle_, 1, codes, &props, &nprop) == SDK::CrError_None &&
                props && nprop > 0 &&
                props[0].GetCurrentValue() == SDK::CrPullPostViewImageStatus_Exists) {
                SDK::ReleaseDeviceProperties(handle_, props);
                pull_postview(12 * 1024 * 1024);
                return;
            }
            if (props) {
                SDK::ReleaseDeviceProperties(handle_, props);
            }
        }
        std::fprintf(stderr, "crsdk: no JPEG arrived after shutter\n");
    }

    SDK::CrDeviceHandle handle_ = 0;
    std::unique_ptr<CrCallback> callback_;
    std::string save_dir_;
    std::string camera_id_;
    std::string camera_model_;
    std::mutex mu_;
    std::condition_variable cv_;
    std::atomic<bool> session_ready_{false};
    std::atomic<bool> session_conflict_{false};
    std::atomic<bool> requested_disconnect_{false};
    std::atomic<bool> saveinfo_logged_{false};
    std::atomic<bool> lost_emitted_{false};
    std::atomic<bool> dest_applied_{false};
    std::atomic<bool> dest_hinted_{false};
    std::atomic<bool> transfer_applied_{false};
    std::atomic<bool> recover_watch_started_{false};
    std::atomic<bool> settings_retry_started_{false};
    std::thread recover_thread_;
    std::thread settings_thread_;
    std::mutex files_mu_;
    std::unordered_set<std::string> emitted_files_;
    std::atomic<int> shot_index_{0};
};

void CrCallback::OnConnected(SDK::DeviceConnectionVersioin /*version*/) {
    owner_->handle_connected();
}

void CrCallback::OnDisconnected(CrInt32u error) {
    owner_->handle_disconnected(error);
}

void CrCallback::OnPropertyChanged() {
    owner_->handle_property_changed();
}

void CrCallback::OnCompleteDownload(CrChar* filename, CrInt32u /*type*/) {
    owner_->handle_file(filename);
}

void CrCallback::OnNotifyPostViewImage(CrChar* filename, CrInt32u size) {
    owner_->handle_postview(filename, size);
}

void CrCallback::OnNotifyRemoteTransferResult(CrInt32u notify, CrInt32u /*per*/, CrChar* filename) {
    if (notify == SDK::CrNotify_RemoteTransfer_Result_OK && filename) {
        owner_->handle_file(filename);
    }
}

void CrCallback::OnError(CrInt32u error) {
    owner_->handle_error(error);
}

void CrCallback::OnWarning(CrInt32u warning) {
    owner_->handle_warning(warning);
}

}  // namespace

ICameraBackend* create_crsdk_backend() {
    return new CrSdkBackend();
}

#endif
