import unittest
import virt_v2v_wrapper as wrapper


class TestOutputParser(unittest.TestCase):

    def test_disk_number(self):
        with wrapper.log_parser('/dev/null') as parser:
            parser._current_disk = 0
            parser._current_path = b'/path1'
            state = {
                'disks': [
                    {'path': b'[store1] path1.vmdk'},
                    {'path': b'[store1] path2.vmdk'},
                    {'path': b'[store1] path3.vmdk'},
                    ],
                }
            state = parser.parse_line(
                state,
                b'Copying disk 2/3 to /some/path')
            self.assertEqual(parser._current_disk, 1)
            self.assertIsNone(parser._current_path)
            self.assertEqual(state['disk_count'], 3)

    def test_locate_disk(self):
        with wrapper.log_parser('/dev/null') as parser:
            parser._current_disk = 0
            parser._current_path = b'[store1] path1.vmdk'
            state = {
                'disks': [
                    {'path': b'[store1] path2.vmdk'},
                    {'path': b'[store1] path1.vmdk'},
                    {'path': b'[store1] path3.vmdk'},
                    ],
                }
            parser._locate_disk(state)
            self.assertEqual(state['disks'][0]['path'], b'[store1] path1.vmdk')
            self.assertEqual(state['disks'][1]['path'], b'[store1] path2.vmdk')
            self.assertEqual(state['disks'][2]['path'], b'[store1] path3.vmdk')

    def test_progress(self):
        with wrapper.log_parser('/dev/null') as parser:
            parser._current_disk = 0
            parser._current_path = b'/path1'
            state = {
                'disks': [{
                    'path': b'/path1',
                    'progress': 0.0,
                    }],
                }
            state = parser.parse_line(
                state,
                b'  (10.42/100%)')
            self.assertEqual(state['disks'][0]['progress'], 10.42)

    # TODO
    # def test_rhv_disk_path_ssh(self):
    #     with wrapper.log_parser('/dev/null') as parser:
    #         state = {}
    #         state = parser.parse_line(
    #             state,
    #             b'  overlay source qemu URI: nbd:unix:/var/tmp/vddk.Iwg7XW/nbdkit1.sock:exportname=/')  # NOQA
    #         self.assertEqual(parser._current_path, '[store1] /path1.vmdk')

    def test_rhv_disk_path_vddk(self):
        with wrapper.log_parser('/dev/null') as parser:
            state = {}
            state = parser.parse_line(
                state,
                b'nbdkit: debug: Opening file [store1] /path1.vmdk (ha-nfcssl://[store1] path1.vmdk@1.2.3.4:902)')  # NOQA
            self.assertEqual(parser._current_path, b'[store1] /path1.vmdk')

    def test_rhv_disk_uuid(self):
        with wrapper.log_parser('/dev/null') as parser:
            parser._current_disk = 0
            path = b'/path1'
            state = {
                'disks': [{
                    'path': path,
                    }],
                'internal': {
                    'disk_ids': {
                        }
                    }
                }
            state = parser.parse_line(
                state,
                b'disk.id = \'11111111-1111-1111-1111-111111111111\'')
            self.assertIn(path, state['internal']['disk_ids'])
            self.assertEqual(
                state['internal']['disk_ids'][path],
                b'11111111-1111-1111-1111-111111111111')
