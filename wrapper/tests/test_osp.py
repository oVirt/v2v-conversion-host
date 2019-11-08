import unittest
from wrapper import hosts


class TestOSP(unittest.TestCase):
    """ Tests specific to OpenStack """

    def test_disk_naming(self):
        host = hosts.OSPHost()
        with self.assertRaises(ValueError):
            host._get_disk_name(0)
        self.assertEqual('vda', host._get_disk_name(1))
        self.assertEqual('vdb', host._get_disk_name(2))
        self.assertEqual('vdz', host._get_disk_name(26))
        self.assertEqual('vdaa', host._get_disk_name(27))
        self.assertEqual('vdab', host._get_disk_name(28))
        self.assertEqual('vdaz', host._get_disk_name(52))
        self.assertEqual('vdba', host._get_disk_name(53))
        self.assertEqual('vdzy', host._get_disk_name(701))
        self.assertEqual('vdzz', host._get_disk_name(702))
        self.assertEqual(
            '11000000101010000000000000101010',
            host._ip_to_binary('192.168.0.42')
        )
        self.assertEqual(
            '110000001010100000000000',
            host._get_prefix_bin('192.168.0.42', 24)
        )
        self.assertEqual(
            True,
            host._check_ip_in_network(
                '192.168.0.42',
                '192.168.0.0/24'
            )
        )
        self.assertEqual(
            False,
            host._check_ip_in_network(
                '192.168.1.42',
                '192.168.0.0/42'
            )
        )
