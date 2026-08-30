# crsdk_host

Native camera host process. Python talks to it over stdin/stdout JSON lines
(see `camera_protocol.py`). Do **not** commit Sony Camera Remote SDK headers
or libraries.

Works on **Windows 11 x64** and **macOS** (Apple silicon or Intel). The same
protocol is used on both. If this binary is missing, the app launches
`crsdk_simulator.py`.

## Without the Sony SDK

```
cmake -S . -B build
cmake --build build --config Release
```

Windows output: `build/Release/crsdk_host.exe`  
macOS output: `build/crsdk_host`

## With the Sony Camera Remote SDK

1. Register and download the SDK from Sony:
   https://support.d-imaging.sony.co.jp/app/sdk/en/index.html
2. Accept Sony’s license. Keep the SDK off git.
3. Download the **Windows 64-bit** or **Mac** zip for your machine.
4. Set `CRSDK_ROOT` to the unpacked SDK directory.
5. Camera USB mode: **Remote Shoot (PC Remote)**. Quit Imaging Edge / Remote.
   - Windows: install **libusbK**.
   - macOS: no libusbK. Use a data cable; allow USB accessories if prompted.

Windows:

```
cmake -S . -B build -DCRSDK_ROOT="%CRSDK_ROOT%"
cmake --build build --config Release
```

macOS:

```
cmake -S . -B build -DCRSDK_ROOT="$CRSDK_ROOT"
cmake --build build --config Release
```

`backend_crsdk.cpp` follows the public CRSDK connect / save / callback pattern.
If your SDK version renamed a symbol, adjust that file only.

## Protocol

One JSON object per line. Commands: `ping`, `enumerate`, `connect`,
`disconnect`, `shutdown`. Events: `hello`, `reply`, `connected`,
`disconnected`, `reconnecting`, `image_ready`, `error`.
