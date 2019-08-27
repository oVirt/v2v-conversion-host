import tempfile
import os
import json


class State(object):
    """
    State object (which is a dict inside) implemented as singleton

    This is not just the contain of state file, but it contains all the
    internal configuration.
    """
    class __StateObject:
        def __init__(self):
            # For now keep content as dict. Idealy this should be changed
            # later too.
            self._state = {
                'disks': [],
                'internal': {
                    'disk_ids': {},
                    'display_name': None,
                    'ports': [],
                    'throttling_file': None,
                    },
                'failed': False,
                'throttling': {
                    'cpu': None,
                    'network': None,
                    }
                }
            self.daemonize = True
            self.state_file = None
            self.v2v_log = None
            self.machine_readable_log = None

        def __getattr__(self, name):
            return getattr(self._state, name)

        def __getitem__(self, key):
            return self._state[key]

        def __setitem__(self, key, value):
            self._state[key] = value

        def __str__(self):
            return repr(self._state)

        # def get_filename(self):
        #     return self._filename

        # def set_filename(self, name):
        #     self._filename = name

        # FIXME: property attributes (property()/@property) don't work properly
        # filename = property(get_filename, set_filename)

        def write(self):
            state = self._state.copy()
            del state['internal']
            tmp_state = tempfile.mkstemp(
                suffix='.v2v.state',
                dir=os.path.dirname(self.state_file))
            with os.fdopen(tmp_state[0], 'w') as f:
                json.dump(state, f)
            os.rename(tmp_state[1], self.state_file)

    instance = None

    def __init__(self):
        if not State.instance:
            State.instance = State.__StateObject()

    def __getattr__(self, name):
        return getattr(self.instance, name)
