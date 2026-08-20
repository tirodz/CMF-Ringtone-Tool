"""fw_registry.py - firmware compatibility registry.

Every version-dependent value for the BLE/SDFS replacement path lives here.
Only versions with evidence-backed constants are listed; anything else raises
UnsupportedFirmwareError with the exact, documented refusal message.

Evidence basis for 1.0.0.73: static analysis of original.bin (reported in
REPORT.md): SDFS layout with ring1.act at 0x5c80 (15556 bytes), sdfs entry
index 8, checksum word locations, boot-info/partition-table layout, and the
debug-shell command set (mdw/mww/snand*/sdfs).
"""

SUPPORTED_VERSIONS = ('1.0.0.73',)

REFUSAL_MESSAGE = 'Unsupported firmware version — no write performed.'


class UnsupportedFirmwareError(Exception):
    """Raised before any write when the firmware version is not in the registry."""

    def __init__(self, version):
        self.version = version
        super().__init__(REFUSAL_MESSAGE)


class FirmwareLayout:
    def __init__(self, d):
        self.__dict__.update(d)


# ---- 1.0.0.73 (primary target) ----------------------------------------------
LAYOUT_1_0_0_73 = FirmwareLayout(dict(
    version='1.0.0.73',
    # SDFS partition layout (bytes relative to the sdfs_k partition base PBASE)
    ring1_name='ring1.act',
    ring1_off=0x5c80,
    ring1_size=15556,          # 0x3cc4 bytes, on-flash XOR form
    sdfs_entry_index=8,        # ring1 is the 8th sdfs file entry
    tbl_f4_off=0x18,           # entry-0 checksum word: table sum
    tbl_f5_off=0x1c,           # entry-0 checksum word: data-segment sum
    sector=512,                # FTL sector alignment for snandw
    stage_buffer=0x380027bd,   # snandw staging RAM buffer
    boot_info_addr=0x1000000,  # mdw target for boot info / partition table ptr
    # safe non-critical test target for a harmless first write: the small
    # 'sdfs.txt' file (content "1234567890" in stock firmware)
    test_file_name='sdfs.txt',
    test_file_stock=b'1234567890',
))

_REGISTRY = {LAYOUT_1_0_0_73.version: LAYOUT_1_0_0_73}


def lookup(version):
    """Return the FirmwareLayout for a version string; raise on unknown.

    Callers must treat UnsupportedFirmwareError as a hard abort for any write.
    """
    if version in _REGISTRY:
        return _REGISTRY[version]
    raise UnsupportedFirmwareError(version)


def is_supported(version):
    return version in _REGISTRY


def ring1_window(layout):
    """512-sector-aligned staging window covering ring1 entirely."""
    win_start = layout.ring1_off & ~(layout.sector - 1)
    win_end = (layout.ring1_off + layout.ring1_size + layout.sector - 1) & \
        ~(layout.sector - 1)
    return win_start, win_end
