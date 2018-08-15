import unittest
try:
    # Python3
    from unittest.mock import patch
except ImportError:
    # Python2
    from mock import patch

import virt_v2v_wrapper as wrapper


class TestRHV(unittest.TestCase):
    """ Test specific to RHV """

    @patch('os.path.isfile', new=lambda _: True)
    def test_tools_iso_ordering(self):
        host = wrapper.VDSMHost()
        self.assertEqual(
                b'virtio-win-123.iso',
                host._filter_iso_names(b'/', [
                    b'a.iso',
                    b'virtio-win-123.iso',
                    b'b.iso',
                    ]))
        # Priority
        self.assertEqual(
                b'RHEV-toolsSetup_123.iso',
                host._filter_iso_names(b'/', [
                    b'RHEV-toolsSetup_123.iso',
                    b'virtio-win-123.iso',
                    ]))
        self.assertEqual(
                b'RHEV-toolsSetup_123.iso',
                host._filter_iso_names(b'/', [
                    b'virtio-win-123.iso',
                    b'RHEV-toolsSetup_123.iso',
                    ]))
        self.assertEqual(
                b'RHEV-toolsSetup_234.iso',
                host._filter_iso_names(b'/', [
                    b'RHEV-toolsSetup_123.iso',
                    b'virtio-win-123.iso',
                    b'RHEV-toolsSetup_234.iso',
                    ]))
        self.assertEqual(
                b'RHEV-toolsSetup_234.iso',
                host._filter_iso_names(b'/', [
                    b'RHEV-toolsSetup_234.iso',
                    b'virtio-win-123.iso',
                    b'RHEV-toolsSetup_123.iso',
                    ]))
        self.assertEqual(
                b'rhv-tools-setup.iso',
                host._filter_iso_names(b'/', [
                    b'rhv-tools-setup.iso',
                    b'virtio-win-123.iso',
                    ]))
        # Version
        self.assertEqual(
                b'RHEV-toolsSetup_4.0_3.iso',
                host._filter_iso_names(b'/', [
                    b'RHEV-toolsSetup_4.0_3.iso',
                    b'RHEV-toolsSetup_4.0_2.iso',
                    ]))

        self.assertEqual(
                b'RHEV-toolsSetup_4.1_3.iso',
                host._filter_iso_names(b'/', [
                    b'RHEV-toolsSetup_4.0_3.iso',
                    b'RHEV-toolsSetup_4.1_3.iso',
                    ]))
