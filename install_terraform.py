"""Install pinned Terraform into a local directory after HTTPS checksum verification."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import ssl
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.9.8"


def download(url: str) -> bytes:
    # Python.org macOS installs can lack configured system CA paths. Setup has
    # already installed our pinned certifi dependency; retain HTTPS verification.
    try:
        import certifi

        context = ssl.create_default_context()
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=120, context=context) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=Path(".tools"))
    args = parser.parse_args()
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    arch = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine()
    )
    if not system or not arch or (system == "windows" and arch == "arm64"):
        raise SystemExit("Unsupported platform. Install Terraform 1.9.8 from HashiCorp manually.")
    filename = f"terraform_{VERSION}_{system}_{arch}.zip"
    base = f"https://releases.hashicorp.com/terraform/{VERSION}"
    checksums = download(f"{base}/terraform_{VERSION}_SHA256SUMS").decode()
    expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1] == filename)
    data = download(f"{base}/{filename}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit("Checksum mismatch. Nothing installed.")
    name = "terraform.exe" if system == "windows" else "terraform"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        binary = archive.read(name)
    args.dest.mkdir(parents=True, exist_ok=True)
    target = args.dest / name
    if target.exists() and target.read_bytes() != binary:
        raise SystemExit(f"A different executable already exists at {target}. Use a new --dest.")
    if not target.exists():
        target.write_bytes(binary)
        target.chmod(target.stat().st_mode | 0o111)
    receipt = {
        "version": VERSION,
        "platform": f"{system}_{arch}",
        "archive_sha256": actual,
        "binary_sha256": hashlib.sha256(binary).hexdigest(),
        "source": f"{base}/{filename}",
    }
    (args.dest / "terraform-install-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Installed verified Terraform {VERSION}: {target.resolve()}")
    print(f"Set terraform_bin in config.json to this path (or add {args.dest.resolve()} to PATH).")


if __name__ == "__main__":
    main()
