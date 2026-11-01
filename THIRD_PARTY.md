# Third-party components

- The UVC server source in `usbip-source` is adapted from [usbipdcpp](https://github.com/yunsmall/usbipdcpp), pinned to commit `51ec41981024250d068fda25aed88be9140af722`. It and the accompanying upstream patch are provided under LGPL-3.0. See [the license](third-party/LGPL-3.0.txt) and the linked upstream source. The build uses its shared libraries.
- The capture helper uses [windows-capture](https://github.com/NiiightmareXD/windows-capture); see the package and its license for version 2.0.1.
- The USB/IP driver is installed separately from [usbip-win2](https://github.com/vadimgrn/usbip-win2); it is not distributed in this repository.
- Python, Rust and C++ dependencies retain their own licenses. Optional Unity Capture and OBS components are installed separately and are not included here.

No project-wide license for the other original code has been chosen yet.
