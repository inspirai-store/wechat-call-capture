# 微信通话画面采集原型

Windows 桌面原型：通过系统选择器采集一个窗口，在本机裁剪、缩放或镜像，并输出到一个或两个 USB/IP UVC 摄像头。默认不采集声音、不录制、不上传。

默认双路输出为 720 × 1280、15 fps。`WeChat USBIP UVC` 和 `WeChat USBIP UVC 2` 输出同一画面，可分别在微信与直播伴侣中选择。已在本机验证两路同时显示；这是通过 USB/IP 和 Windows UVC 驱动实现的软件设备，并非实体摄像头，也不保证所有应用或设备验证流程接受它。

## 环境

- Windows 11，Python 3.13（其他受依赖支持的版本未经完整验证）。
- [usbip-win2](https://github.com/vadimgrn/usbip-win2) 驱动及命令行工具；本机测试版本 0.9.8.1，需按其官方说明安装。驱动安装可能要求管理员权限和重启。
- 构建时需 Git、Rust MSVC 工具链、Visual Studio 2022 C++ 桌面开发组件、Windows SDK、CMake 和 vcpkg。
- vcpkg 的 `x64-windows` 依赖：`asio`、`spdlog`、`cxxopts`。

本仓库不包含预编译二进制或驱动安装包。它会从 GitHub 获取固定版本的 usbipdcpp；Python 与 vcpkg 包的具体版本取决于安装时的环境。

## 安装和构建

在 PowerShell 中进入仓库根目录，将下面的 vcpkg 路径替换为自己的安装路径：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
C:\vcpkg\vcpkg.exe install asio:x64-windows spdlog:x64-windows cxxopts:x64-windows
.\build-native.ps1 -VcpkgRoot C:\vcpkg
```

若 PowerShell 执行策略禁止运行脚本，可使用 `powershell -ExecutionPolicy Bypass -File .\build-native.ps1 -VcpkgRoot C:\vcpkg`。构建脚本不会安装 USB/IP 驱动。默认 USB/IP 程序路径为 `C:\Program Files\USBip\usbip.exe`。

## 使用

1. 打开微信视频通话，保持通话窗口非最小化。
2. 双击 `start_gui.bat`，选择“USB/IP 双路”、需要的分辨率和帧率，点击“开始采集”。
3. 在 Windows 系统选择器中选择微信通话窗口。预览出现后可框选输出区域，调整缩放或水平镜像；两个摄像头共用这些设置。
4. 在微信和直播伴侣分别选择 `WeChat USBIP UVC` 与 `WeChat USBIP UVC 2`。如果接收端提供分辨率设置，请选择与工具一致的尺寸。
5. 在工具中点击“停止”或关闭窗口，断开本次创建的设备。

可选择单路 `USB/IP UVC` 或“仅预览”。其他 OBS/Unity 选项需要自行安装并注册相应组件，未经此源码包重新验证。若微信把回传画面放在被采集的通话窗口里，可框选避开本地预览，减少画面递归。接收端横向预览框的留白不一定属于竖屏输出；直播画布内可能还需调整素材尺寸。

如果窗口无画面，请确认选中了通话窗口、窗口未最小化且应用允许捕获；本工具不会绕过受保护画面。若程序异常退出导致设备残留，先检查 `usbip.exe port`，仅分离本工具的 `127.0.0.1:53241/1-2` 和 `127.0.0.1:53243/1-3`，不要使用 `detach --all`。安装的 USB/IP 驱动不会随停止采集卸载。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tests\gui_fixture.py
.\.venv\Scripts\python.exe tests\usbip_pair_real_check.py
```

最后一项需要已安装 USB/IP 驱动及 FFmpeg，会临时创建两个独立测试设备并通过 DirectShow 读取。真实微信与直播伴侣的兼容性仍需在目标机器上验证。

第三方来源与许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。
