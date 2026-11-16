#!/usr/bin/env python3
"""Offline invariants derived from the exact Scribe 479432 kernel sources.

These tests model API ownership and layout constraints. They open no device and
do not claim that a corrected decoder has been exercised on hardware.
Run through ssh test-env with Python isolated mode.
"""
from __future__ import annotations

import unittest


class ContractError(ValueError):
    pass


class CaptureContract:
    """Track the header-dependent capture layout independently of allocations."""

    def __init__(self):
        self.header_seen = False
        self.layout = ()
        self.allocated_layout = ()
        self.buffers = ()
        self.inflight = False

    def parsed_header(self, plane_sizes):
        self.header_seen = True
        self.layout = tuple(plane_sizes)

    def negotiated_format(self, plane_sizes):
        if not self.header_seen or self.buffers or self.inflight:
            raise ContractError("Negotiate after the header and before capture allocation")
        self.layout = tuple(plane_sizes)

    def allocate(self, capacities):
        if not self.header_seen or not self.layout or self.inflight:
            raise ContractError("Capture allocation requires the parsed layout")
        if len(capacities) != len(self.layout) or any(a < b for a, b in zip(capacities, self.layout)):
            raise ContractError("Every negotiated plane needs sufficient backing memory")
        self.allocated_layout = self.layout
        self.buffers = tuple(capacities)

    def queue(self):
        if not self.buffers or self.layout != self.allocated_layout:
            raise ContractError("A header change invalidated the capture allocation")
        self.inflight = True

    def completed(self):
        self.inflight = False

    def release(self):
        if self.inflight:
            raise ContractError("A timeout alone does not retire DMA ownership")
        self.buffers = ()
        self.allocated_layout = ()


def validate_plane(width, height, stride, capacity, payload, saved_bytes, alignment=16):
    if min(width, height, stride, capacity) <= 0 or stride < width or stride % alignment:
        raise ContractError("Invalid visible extent or hardware pitch")
    if stride * height > capacity or payload > capacity or saved_bytes < payload:
        raise ContractError("The complete returned payload must fit and be retained")


def validate_mtk_request(waveform, flags, width, height, direction=0, steps=0, clearing=False):
    y4, mono, swipe = bool(flags & 0x4000), bool(flags & 2), bool(flags & 0x10000)
    if flags & ~0x14002 or min(width, height) <= 1:
        raise ContractError("The request contains unreviewed flags or an invalid area")
    if y4 and (mono or waveform not in (2, 3, 4, 8, 9, 257)):
        raise ContractError("Y4 needs a compatible grayscale path")
    if swipe and (waveform != 257 or not 1 <= steps <= 60 or min(width, height) < steps or direction not in range(4)):
        raise ContractError("Invalid native animation contract")
    if clearing and (swipe or waveform not in (2, 8)):
        raise ContractError("Clearing must remain independent from animation")


class Contracts(unittest.TestCase):
    def test_old_capture_before_header_order_is_rejected(self):
        contract = CaptureContract()
        with self.assertRaises(ContractError):
            contract.allocate((12288,))

    def test_two_plane_header_cannot_use_one_plane_buffer(self):
        contract = CaptureContract()
        contract.parsed_header((12288, 6144))
        with self.assertRaises(ContractError):
            contract.allocate((12288,))
        contract.allocate((12288, 6144))
        contract.queue()
        with self.assertRaises(ContractError):
            contract.release()
        contract.completed()
        contract.release()

    def test_grey_reselection_is_after_header_and_before_allocation(self):
        contract = CaptureContract()
        contract.parsed_header((12288, 6144))
        contract.negotiated_format((12288,))
        contract.allocate((16384,))
        contract.queue()
        with self.assertRaises(ContractError):
            contract.negotiated_format((49152,))

    def test_later_header_invalidates_the_old_capture_layout(self):
        contract = CaptureContract()
        contract.parsed_header((12288,))
        contract.allocate((12288,))
        contract.parsed_header((12288, 6144))
        with self.assertRaises(ContractError):
            contract.queue()

    def test_scribe_visible_width_is_distinct_from_hardware_pitch(self):
        with self.assertRaises(ContractError):
            validate_plane(1860, 2480, 1860, 4642560, 4642560, 4612800)
        validate_plane(1860, 2480, 1872, 4642560, 4642560, 4642560)

    def test_y4_animation_and_night_wire_modes_have_separate_roles(self):
        validate_mtk_request(8, 0x4000, 1860, 2480, clearing=True)
        validate_mtk_request(9, 0x4000, 1860, 2480)
        validate_mtk_request(257, 0x14000, 1860, 2480, 3, 12)
        validate_mtk_request(12, 2, 1860, 2480)
        for arguments in [(12, 0x4000, 1860, 2480), (8, 0x8000, 1860, 2480), (257, 0x14000, 32, 8, 2, 12)]:
            with self.assertRaises(ContractError):
                validate_mtk_request(*arguments)
        with self.assertRaises(ContractError):
            validate_mtk_request(257, 0x14000, 1860, 2480, 2, 12, clearing=True)


if __name__ == "__main__":
    unittest.main()
