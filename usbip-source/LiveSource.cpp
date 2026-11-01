#include "LiveSource.h"
#include <chrono>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include "usbipdcpp/virtual_device/UvcConstants.h"
using namespace std::chrono_literals;
using asio::ip::tcp;
static constexpr std::size_t MAX_JPEG = 4 * 1024 * 1024;

LiveSource::LiveSource(int width, int height, int fps, int port, const std::string& fallback)
    : width_(width), height_(height), interval_(10000000u / fps),
      acceptor_(io_, tcp::endpoint(asio::ip::address_v4::loopback(), static_cast<unsigned short>(port))) {
    std::ifstream input(fallback, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot open initial JPEG");
    latest_ = std::vector<std::uint8_t>(std::istreambuf_iterator<char>(input), {});
    if (latest_.empty() || latest_.size() > MAX_JPEG) throw std::runtime_error("Invalid initial JPEG");
    fallback_ = latest_;
    acceptor_.non_blocking(true);
    thread_ = std::thread([this] { read_frames(); });
}
LiveSource::~LiveSource() { stop_.store(true); if (thread_.joinable()) thread_.join(); }
std::vector<usbipdcpp::VideoFormatInfo> LiveSource::supported_formats() const {
    return {{usbipdcpp::UvcFourCC::MJPEG, static_cast<std::uint16_t>(width_), static_cast<std::uint16_t>(height_),
             static_cast<std::uint32_t>(MAX_JPEG), interval_, interval_, interval_ * 10, 0}};
}
usbipdcpp::VideoFormatInfo LiveSource::current_format() const { return supported_formats().front(); }
bool LiveSource::set_format(std::uint32_t fourcc, std::uint16_t width, std::uint16_t height, std::uint32_t interval) {
    if (fourcc != usbipdcpp::UvcFourCC::MJPEG || width != width_ || height != height_) return false;
    interval_ = interval; return true;
}
bool LiveSource::get_frame(usbipdcpp::VideoFrame& frame) {
    { std::lock_guard<std::mutex> lock(mutex_); current_ = latest_; }
    frame.data = current_.data(); frame.size = current_.size(); frame.is_keyframe = true;
    return !current_.empty();
}
std::size_t LiveSource::max_frame_size() const { return MAX_JPEG; }
std::uint32_t LiveSource::frame_interval() const { return interval_; }
void LiveSource::read_frames() {
    while (!stop_.load()) {
        tcp::socket socket(io_);
        asio::error_code ec;
        acceptor_.accept(socket, ec);
        if (ec == asio::error::would_block || ec == asio::error::try_again) { std::this_thread::sleep_for(10ms); continue; }
        if (ec) { std::this_thread::sleep_for(20ms); continue; }
        socket.non_blocking(true, ec);
        auto read_exact = [&](std::uint8_t* buf, std::size_t size) {
            std::size_t pos = 0;
            while (pos < size && !stop_.load()) {
                auto n = socket.read_some(asio::buffer(buf + pos, size - pos), ec);
                if (ec == asio::error::would_block || ec == asio::error::try_again) { std::this_thread::sleep_for(2ms); continue; }
                if (ec || !n) return false;
                pos += n;
            }
            return pos == size;
        };
        while (!stop_.load()) {
            std::uint8_t header[4];
            if (!read_exact(header, sizeof(header))) break;
            std::uint32_t size = std::uint32_t(header[0]) | (std::uint32_t(header[1]) << 8) | (std::uint32_t(header[2]) << 16) | (std::uint32_t(header[3]) << 24);
            if (!size || size > MAX_JPEG) break;
            std::vector<std::uint8_t> data(size);
            if (!read_exact(data.data(), data.size())) break;
            if (size < 4 || data[0] != 0xff || data[1] != 0xd8 || data[size - 2] != 0xff || data[size - 1] != 0xd9) break;
            { std::lock_guard<std::mutex> lock(mutex_); latest_ = std::move(data); }
        }
        { std::lock_guard<std::mutex> lock(mutex_); latest_ = fallback_; }
    }
}
