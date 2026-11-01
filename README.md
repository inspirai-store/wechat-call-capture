# 微信通话画面采集原型

Windows 桌面原型：通过系统选择器采集一个窗口，在本机裁剪、缩放或镜像，并输出到一个或两个 USB/IP UVC 摄像头。默认不采集声音、不录制、不上传。

默认双路输出为 720 × 1280、15 fps。`Hi Camara` 和 `Pure Camara` 输出同一画面，可分别在微信与直播伴侣中选择。已在本机验证两路同时显示；这是通过 USB/IP 和 Windows UVC 驱动实现的软件设备，并非实体摄像头，也不保证所有应用或设备验证流程接受它。

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

使用临时 HTTPS 入口时另运行 `./install-cloudflared.ps1`，它将官方 cloudflared 下载到本项目的 `tools` 目录并校验 SHA-256，不安装系统服务。固定入口不需要 cloudflared。

## 使用手机摄像头（跨网络原型）

1. 在电脑的助手中将“画面来源”设为“手机 WebRTC”。选择 USB/IP 双路时，先结束其他程序正在使用的本项目双路会话。
2. 配置了短期凭据签发器后，程序会自动获取临时 TURN 凭据，手机和电脑无需手动输入 TURN 信息。没有签发器时，仍可在“高级设置”中手动填写已有 TURN 地址及凭据；手动凭据会通过 HTTPS 信令发给手机，不应当视为隐藏的密钥。
3. 点击“开始采集”，用 iPhone 相机扫码直接配对，或将房间网页地址发给手机、手动输入 8 位房间码。在 Safari 选择前置或后置镜头并允许摄像头权限。网页只采集视频，不采集声音。保持网页在前台，关闭页面或点击停止会结束手机采集。
4. 手机画面会显示在电脑预览中，继续通过 `Hi Camara` 和 `Pure Camara` 输出。断线或长时间收不到帧时输出黑画面。房间码 10 分钟有效、连续输错会暂时锁定；二维码包含本次会话凭据，不能公开分享。

短期凭据签发器通过本机 SSH 免密连接获取 12 小时有效的临时凭据。使用此方式需在电脑项目目录创建不纳入 Git 的 `.phone-turn.json`，内容形如 `{"issuer_ssh":"root@服务器地址","public_base":"https://服务器地址"}`，并预先验证服务器 SSH 主机密钥及免密登录。`public_base` 可选；配置后程序自动通过 SSH 反向隧道连接服务器的回环端口 18765，固定 HTTPS 页面即使电脑离线也可打开，房间功能则需要电脑启动采集。`server/install_ephemeral_turn.py` 可由服务器管理员安装独立 coturn 实例与签发器，默认监听 TCP/UDP 3479，使用 UDP 49224–49287 作为中继端口；这些端口需要在云安全组与主机防火墙开放。它不会覆盖已有的 coturn 服务。长时间会话需要在临时凭据到期前重新开始采集。服务器签名密钥不发送给手机，短期凭据会发送给已配对的手机以建立 WebRTC 连接。

服务器需预先安装 coturn、Python 3 和 systemd，并具备 `coturn` 系统账户。首次部署可在服务器上以 root 执行 `python3 install_ephemeral_turn.py 公网IPv4`；脚本拒绝覆盖同名服务或配置，并在启动失败时回滚自身创建的文件。仅向可信的配对对象提供房间码：持有者可在有效期内取得临时中继凭据。

未配置固定地址时使用 Cloudflare Quick Tunnel：Cloudflare 会处理网页和信令请求，WebRTC 视频通过直连或配置的 TURN 中继传输。新地址可能需要一段时间生效，程序会检查入口后显示二维码；临时服务不保证可用性，跨网可用性需要在目标手机网络实测。链接通过二维码/复制按钮提供，不写入日志。请仅在信任的网络环境中使用，且仅分享给对应手机。

如已有自己的 HTTPS 根域名/公网 IP 证书和反向代理，可在“自有 HTTPS 入口”填写 `https://你的地址`；程序只在本机 `127.0.0.1:8765` 监听。需要自行将外部 HTTPS 请求安全转发至该端口，例如通过 SSH 反向隧道连接服务器的回环端口，再由 HTTPS 反向代理转发。该地址不能包含路径或查询参数。不要将本机 HTTP 端口直接暴露到公网。

## 使用

1. 打开微信视频通话，保持通话窗口非最小化。
2. 双击 `start_gui.bat`，选择“USB/IP 双路”、需要的分辨率和帧率，点击“开始采集”。
3. 在 Windows 系统选择器中选择微信通话窗口。预览出现后可框选输出区域，调整缩放或水平镜像；两个摄像头共用这些设置。
4. 在微信和直播伴侣分别选择 `Hi Camara` 与 `Pure Camara`。如果接收端提供分辨率设置，请选择与工具一致的尺寸。
5. 在工具中点击“停止”或关闭窗口，断开本次创建的设备。

可选择单路 `USB/IP UVC` 或“仅预览”。其他 OBS/Unity 选项需要自行安装并注册相应组件，未经此源码包重新验证。若微信把回传画面放在被采集的通话窗口里，可框选避开本地预览，减少画面递归。接收端横向预览框的留白不一定属于竖屏输出；直播画布内可能还需调整素材尺寸。

如果窗口无画面，请确认选中了通话窗口、窗口未最小化且应用允许捕获；本工具不会绕过受保护画面。若程序异常退出导致设备残留，先检查 `usbip.exe port`，仅分离本工具的 `127.0.0.1:53241/1-2` 和 `127.0.0.1:53243/1-3`，不要使用 `detach --all`。安装的 USB/IP 驱动不会随停止采集卸载。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tests\gui_fixture.py
.\.venv\Scripts\python.exe tests\usbip_pair_real_check.py
.\.venv\Scripts\python.exe tests\phone_dual_real_check.py
```

最后一项需要已安装 USB/IP 驱动及 FFmpeg，会临时创建两个独立测试设备并通过 DirectShow 读取。真实微信与直播伴侣的兼容性仍需在目标机器上验证。

第三方来源与许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。
