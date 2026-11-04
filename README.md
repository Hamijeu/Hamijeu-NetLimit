# Hamijeu NetLimit V1.0

A portable Windows x64 bandwidth limiter for your laptop and Windows Mobile Hotspot.

**[Download Hamijeu NetLimit V1.0 for Windows (ZIP)](https://github.com/Hamijeu/Hamijeu-NetLimit/releases/download/v1.0.0/Hamijeu-NetLimit-v1.0.0-windows-x64.zip)**

## Get started

1. Extract the entire ZIP. Keep the executable and its `_internal` directory together.
2. Open `Hamijeu NetLimit V1.0.exe` and approve the Windows administrator prompt so the app can load WinDivert.
3. Choose the Internet adapter (Wi-Fi, Ethernet, or USB tethering), enable **Laptop**, and enter the download and upload limits in Mbps.
4. Click **Start**. The laptop startup check sends synthetic test traffic to Cloudflare over HTTPS. If it times out, the app retries once with a smaller sample.
5. Minimize to the tray. Use **Open** to restore the window or **Exit** to stop the limiter and close it.

**Start with Windows** opens the app at sign-in. Click **Start** in the app to apply the limits.

### Hotspot clients

Enable Windows Mobile Hotspot, confirm its IPv4 subnet, then enable **Hotspot clients**. Connected devices share the configured limits. Test from a connected client to verify the result on your setup. Laptop mode covers IPv4 and the selected adapter's IPv6 addresses at startup. Hotspot mode covers IPv4 and depends on Windows Internet Connection Sharing.

**Normal** is the default download profile. **Low latency** uses adaptive queue management. Latency and throughput depend on the connection and traffic, and a configured rate is a ceiling.

## Build from source

Use Python 3.12 on Windows x64.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest -q
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --onedir --windowed --name "Hamijeu NetLimit V1.0" --icon app.ico --add-data "app.ico:." --collect-binaries pydivert ui.py
```

This project began as a companion prototype in a local checkout of [OpenNetLimit](https://github.com/SysAdminDoc/OpenNetLimit). See the [license](LICENSE) and [third-party notices](THIRD-PARTY-NOTICES.md).
