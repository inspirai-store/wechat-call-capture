#include <iostream>
#include <system_error>

#include "../example_utils.h"
#include "usbipdcpp/Device.h"
#include "usbipdcpp/Server.h"
#include "usbipdcpp/usbipdcpp_core.h"
#include "usbipdcpp/virtual_device/UvcConstants.h"
#include "usbipdcpp/virtual_device/UvcVirtualInterfaceHandler.h"
#include "LiveSource.h"

using namespace usbipdcpp;

int main(int argc, char **argv) {
    auto opts = make_example_options("wechat_uvc", "USB/IP live video camera");
    opts.add_options()
        ("width", "Video width", cxxopts::value<int>()->default_value("320"))
        ("height", "Video height", cxxopts::value<int>()->default_value("240"))
        ("frame-port", "Loopback frame input port", cxxopts::value<int>()->default_value("53242"))
        ("fallback", "Initial JPEG image", cxxopts::value<std::string>()->default_value("fallback.jpg"))
        ("fps", "Frame rate", cxxopts::value<int>()->default_value("15"))
        ("product-id", "USB product ID", cxxopts::value<int>()->default_value("22146"))
        ("device-name", "ASCII interface name", cxxopts::value<std::string>()->default_value("WeChat USBIP UVC"));
    auto result = parse_example_args(opts, argc, argv);
    auto port = result["port"].as<std::uint16_t>();
    auto busid = result["busid"].as<std::string>();
    auto width = result["width"].as<int>();
    auto height = result["height"].as<int>();
    auto fps = result["fps"].as<int>();
    auto frame_port = result["frame-port"].as<int>();
    auto fallback = result["fallback"].as<std::string>();
    auto product_id = result["product-id"].as<int>();
    auto device_name = result["device-name"].as<std::string>();
    if (product_id < 1 || product_id > 65535 || device_name.empty()) return 1;

    spdlog::set_level(spdlog::level::info);

    StringPool string_pool;

    // UVC 1.5 Table 3-2/3-13: VC/VS interface protocol must be PC_PROTOCOL_15
    std::vector<UsbInterface> interfaces = {
            // Interface 0: VideoControl（含中断端点用于状态通知）
            UsbInterface{
                    .interface_class = CC_VIDEO,
                    .interface_subclass = SC_VIDEOCONTROL,
                    .interface_protocol = PC_PROTOCOL_15,
                    .endpoints = {{UsbEndpoint{
                            .address = 0x87, // IN, endpoint 7 — interrupt for status
                            .attributes = 0x03, // Interrupt
                            .max_packet_size = 16,
                            .interval = 8,
                    }}},
            },
            // Interface 1: VideoStreaming（alt 0 空端点，alt 1 ISO IN 端点）
            UsbInterface{
                    .interface_class = CC_VIDEO,
                    .interface_subclass = SC_VIDEOSTREAMING,
                    .interface_protocol = PC_PROTOCOL_15,
                    .endpoints = {{}, // alt 0: zero bandwidth
                                  {UsbEndpoint{
                                          .address = 0x81, // IN, endpoint 1
                                          .attributes = static_cast<std::uint8_t>(EndpointAttributes::Isochronous) |
                                                        static_cast<std::uint8_t>(IsoSyncType::Async),
                                          .max_packet_size = 512,
                                          .interval = 1,
                                  }}},
            },
    };

    auto device = std::make_shared<UsbDevice>(UsbDevice{
            .path = "/usbipdcpp/wechat_uvc",
            .busid = busid,
            .bus_num = 1,
            .dev_num = 1,
            .speed = static_cast<std::uint32_t>(UsbSpeed::High),
            .vendor_id = 0x1234,
            .product_id = static_cast<std::uint16_t>(product_id),
            .device_bcd = 0x0100,
            .device_class = 0xEF, // Miscellaneous (IAD)
            .device_subclass = 0x02, // Common Class
            .device_protocol = 0x01, // Interface Association Descriptor
            .configuration_value = 1,
            .num_configurations = 1,
            .interfaces = interfaces,
            .ep0_in = UsbEndpoint::get_ep0_in(UsbSpeed::High),
            .ep0_out = UsbEndpoint::get_ep0_out(UsbSpeed::High),
    });

    // UvcDeviceHelper 创建 VC/VS handler 并注册 + 设置描述符
    auto source = std::make_unique<LiveSource>(width, height, fps, frame_port, fallback);
    // 接口从 0 连续编号：按下标依次填 interface_number（bInterfaceNumber/IAD 依赖它）
    device->assign_interface_numbers();
    if (auto ec = UvcDeviceHelper::setup(device, 0, string_pool, std::move(source)); ec) {
        SPDLOG_ERROR("UVC 功能装配失败：{}", ec.message());
        return 1;
    }

    device->interfaces[0].handler->change_string_interface(std::wstring(device_name.begin(), device_name.end()));

    Server server;
    server.add_device(std::move(device));

    asio::ip::tcp::endpoint endpoint{asio::ip::address_v4::loopback(), port};

    if (auto ec = server.start(endpoint); ec) {
        SPDLOG_ERROR("服务器启动失败：{}", ec.message());
        return 1;
    }

    SPDLOG_INFO("Mock UVC camera started on port {}, busid {}, {}x{}@{}fps", port, busid, width, height, fps);
    SPDLOG_INFO("Connect: usbip attach -r <host> -b {}", busid);
    SPDLOG_INFO("Running... (Ctrl+C / SIGTERM / Enter to stop)");

    wait_for_exit();

    server.stop();
    return 0;
}
