"""Pure helpers for choosing the best serial port for ESP devices."""
from __future__ import annotations

from typing import Iterable, Protocol


class PortInfo(Protocol):
    device: str
    description: str | None
    manufacturer: str | None
    hwid: str | None
    vid: int | None
    pid: int | None


def score_serial_port(port: PortInfo, current_port: str | None = None) -> int:
    """Score a serial port by how likely it is to be an ESP32 USB device."""
    vid = port.vid or 0
    desc = (port.description or "").lower()
    manu = (port.manufacturer or "").lower()
    hwid = (port.hwid or "").lower()
    score = 0

    if vid == 0x303A:
        score += 5
    if vid == 0x1A86:
        score += 4
    if "silicon labs" in manu or "cp210" in desc or "cp210" in hwid:
        score += 2
    if "wch" in manu or "ch340" in desc or "ch34" in hwid:
        score += 2
    if "ftdi" in manu or "ft232" in desc or "ftdi" in hwid:
        score += 2
    if "usb jtag" in desc or "jtag" in desc:
        score += 3
    if "esp" in desc or "espressif" in manu:
        score += 3
    if current_port and port.device == current_port:
        score += 1
    return score


def pick_best_serial_port(ports: Iterable[PortInfo], current_port: str | None = None) -> str | None:
    """Return the best matching serial device from an iterable of port infos."""
    ports = list(ports)
    if not ports:
        return None

    candidates: list[tuple[int, str]] = []
    for port in ports:
        score = score_serial_port(port, current_port=current_port)
        if score > 0:
            candidates.append((score, port.device))

    if candidates:
        candidates.sort(reverse=True)
        best = candidates[0][1]
        if best.startswith("/dev/ttyS"):
            usb_candidates = [
                device
                for _score, device in candidates
                if device.startswith("/dev/ttyUSB") or device.startswith("/dev/ttyACM")
            ]
            if usb_candidates:
                return usb_candidates[0]
        return best

    acm_usb = [
        port.device
        for port in ports
        if port.device.startswith("/dev/ttyACM") or port.device.startswith("/dev/ttyUSB")
    ]
    if acm_usb:
        return acm_usb[0]
    return ports[0].device
