import json
import tempfile
import unittest
from wrapper import virt_v2v_wrapper as wrapper


class TestState(unittest.TestCase):
    """ Tests state object, make sure it it singleton """

    def setUp(self):
        # Destroy any previous state
        wrapper.State.instance = None

    def test_dict(self):
        """ Make sure the access to internal dictionary works """
        state = wrapper.State().instance
        self.assertEqual(state['disks'], [])
        self.assertEqual(state['internal']['disk_ids'], {})
        # check -- change -- check
        self.assertEqual(state['failed'], False)
        state['failed'] = True
        self.assertEqual(state['failed'], True)

    # def test_property(self):
    #     state = wrapper.State().instance
    #     self.assertEqual(state.state_file, None)
    #     value = '/some/path'
    #     state.state_file = value
    #     self.assertEqual(state.state_file, value)
    #  FIXME: property attributes (property()/@property) don't work properly
    #  with the __StateObject
    #     self.assertEqual(state._filename, value)

    def test_singleton(self):
        state1 = wrapper.State().instance
        state2 = wrapper.State().instance
        # Internal dictionary
        key = 'abcdef'
        value = '123456'
        with self.assertRaises(KeyError):
            state1[key]
        with self.assertRaises(KeyError):
            state2[key]
        state1[key] = value
        self.assertEqual(state1[key], value)
        self.assertEqual(state2[key], value)
        # Property
        state1.state_file = None
        state2.state_file = None
        self.assertEqual(state1.state_file, None)
        self.assertEqual(state2.state_file, None)
        value = '/some/path'
        state1.state_file = value
        self.assertEqual(state2.state_file, value)

    def test_write(self):
        state = wrapper.State().instance
        self.assertEqual(state.state_file, None)
        state.state_file = tempfile.mkstemp(prefix='vchtest')[1]
        state.write()
        with open(state.state_file, 'rb') as f:
            json.loads(f.read())
