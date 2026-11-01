#pragma once
#include <asio.hpp>
#include <atomic>
#include <mutex>
#include <thread>
#include <vector>
#include "usbipdcpp/virtual_device/video_sources/VideoSource.h"

class LiveSource : public usbipdcpp::VideoSource {
public:
    LiveSource(int width, int height, int fps, int port, const std::string& fallback);
    ~LiveSource() override;
    std::vector<usbipdcpp::VideoFormatInfo> supported_formats() const override;
    usbipdcpp::VideoFormatInfo current_format() const override;
    bool set_format(std::uint32_t fourcc, std::uint16_t width, std::uint16_t height, std::uint32_t interval) override;
    bool get_frame(usbipdcpp::VideoFrame& frame) override;
    std::size_t max_frame_size() const override;
    std::uint32_t frame_interval() const override;
private:
    void read_frames();
    int width_, height_;
    std::uint32_t interval_;
    std::atomic<bool> stop_{false};
    std::mutex mutex_;
    std::vector<std::uint8_t> fallback_, latest_, current_;
    asio::io_context io_;
    asio::ip::tcp::acceptor acceptor_;
    std::thread thread_;
};
