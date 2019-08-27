import atexit
import logging
import os
import six
import subprocess


if six.PY3:
    xrange = range


def atexit_command(cmd):
    """
    Run command ignoring any errors. This is supposed to be used with atexit.
    """
    def remove(cmd):
        try:
            logging.info('Running command at exit: %r', cmd)
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logging.warning(
                'Ignoring failed command at exit,'
                'returncode=%d, output=\n%s\n',
                e.returncode, e.output)
    atexit.register(lambda: remove(cmd))


class TcController(object):
    """
    Handles communication with tc (traffic control) and associated net_cls
    cgroup.
    """

    # TC store rates as a 32-bit unsigned integer in bps internally
    MAX_RATE = 0xffffffff

    @staticmethod
    def class_id_to_hex(class_id):
        """
        Convert class ID in the form <major>:<minor> into hex string where
        upper 16b are for major and lower 16b are for minor number.

        e.g.: '1a:2b' -> '0x001a002b'
        """
        parts = class_id.split(':')
        major = int(parts[0], base=16)
        minor = int(parts[1], base=16)
        return '0x{:04x}{:04x}'.format(major, minor)

    def __init__(self, tag, uid, gid):
        self._cgroup = 'v2v-conversion/%s' % tag
        self._class_id = None
        self._interfaces = []
        self._owner = (uid, gid)
        self._prepare()

    @property
    def class_id(self):
        return self._class_id

    @property
    def cgroup(self):
        return self._cgroup

    def set_limit(self, limit):
        if limit is None or limit == 'unlimited':
            limit = TcController.MAX_RATE
        ret = True
        for iface in self._interfaces:
            if self._run_tc([
                    'class', 'change', 'dev', iface,
                    'classid', self._class_id, 'htb',
                    'rate',  '{}bps'.format(limit),
                    ]) is None:
                ret = False
        return ret

    def _prepare(self):
        logging.info('Preparing tc')
        root_handle = self._create_qdiscs()
        if root_handle is None:
            return
        for iface in self._interfaces[:]:
            if not self._create_filter(root_handle, iface) or \
                    not self._create_class(root_handle, iface):
                self._interfaces.remove(iface)
        self._prepare_cgroup()

    def _prepare_cgroup(self):
        logging.info('Preparing net_cls cgroup %s', self._cgroup)
        # Create cgroup -- we do this even when tc is not properly set
        # otherwise cgexec would fail
        cgroup_dir = '/sys/fs/cgroup/net_cls/%s' % self._cgroup
        atexit_command(['/usr/bin/rmdir', '-p', cgroup_dir])
        os.makedirs(cgroup_dir)
        # Change ownership of 'tasks' file so cgexec can write into it
        os.chown(
            os.path.join(cgroup_dir, 'tasks'),
            self._owner[0], self._owner[1])
        # Store class ID
        if self._class_id is not None:
            with open(os.path.join(cgroup_dir, 'net_cls.classid'), 'w') as f:
                f.write(TcController.class_id_to_hex(self._class_id))
        else:
            logging.info(
                'Not assigning class ID to net_cls cgroup'
                ' because of previous errors')

    def _create_qdiscs(self):
        qdiscs = self._run_tc(['qdisc', 'show'])
        if qdiscs is None:
            logging.error('Failed to query existing qdiscs')
            return None
        logging.debug('Found following qdiscs: %r', qdiscs)

        root_handle = 'abc:'
        ifaces = []
        roots = None
        try:
            # (interface, type, root handle)
            roots = [(qdisc[4], qdisc[1], qdisc[2])
                     for qdisc in qdiscs if qdisc[5] == 'root']
        except IndexError:
            logging.exception('Failed to process tc output')
            logging.error('%r', qdiscs)
            return None
        logging.debug('Found following root qdiscs: %r', roots)
        #
        # Here we go through all interfaces and try to set our root handle.
        # For interfaces that already have some configuration this will likely
        # fail, we ignore those (but we give it a try first).
        #
        for qdisc in roots:
            if qdisc[1] == 'htb' and qdisc[2] == root_handle:
                # Already ours
                ifaces.append(qdisc[0])
                continue
            # Try to change the qdisc type
            if self._run_tc([
                        'qdisc', 'add', 'dev', qdisc[0],
                        'root', 'handle', root_handle, 'htb'
                    ]) is None:
                logging.info('Failed to setup HTB qdisc on %s', qdisc[0])
            else:
                ifaces.append(qdisc[0])
        self._interfaces = ifaces
        return root_handle

    def _create_class(self, handle, iface):
        # If there is no class ID assigned yet, try to find first free
        if self._class_id is None:
            # First list existing classes
            classes = self._run_tc([
                'class', 'show', 'dev', iface, 'parent', handle])
            if classes is None:
                logging.error(
                    'Failed to query existing classes for parent %s on %s',
                    handle, iface)
                return False
            logging.debug('Found existing tc classes: %r', classes)
            # Gather IDs and find first free
            ids = [class_[2] for class_ in classes]
            new_id = None
            logging.debug('Existing class IDs on %s: %r', iface, ids)
            for i in xrange(1, 0x10000):
                test_id = '{}{:x}'.format(handle, i)
                if test_id not in ids:
                    new_id = test_id
                    break
            if new_id is None:
                logging.error(
                    'Could not find any free class ID on %s under %s',
                    iface, handle)
                return False
        else:
            # We already chose ID before
            new_id = self._class_id
        # Create new class
        logging.info('Creating new tc class on %s with class ID: %s',
                     iface, new_id)
        if self._run_tc([
                    'class', 'add', 'dev', iface,
                    'parent', handle, 'classid', new_id,
                    'htb', 'rate', '{}bps'.format(TcController.MAX_RATE),
                ]) is None:
            logging.error('Failed to create tc class')
            return False
        atexit_command(['tc', 'class', 'del', 'dev', iface, 'classid', new_id])
        self._class_id = new_id
        return True

    def _create_filter(self, handle, iface):
        # It is OK if same filter already exists. However, if there is already
        # a different filter we're in trouble.
        return self._run_tc([
                'filter', 'add', 'dev', iface, 'parent', handle,
                'protocol', 'ip', 'prio', '10', 'handle', '1:', 'cgroup'
            ]) is not None

    def _run_tc(self, args):
        try:
            output = subprocess.check_output(['tc'] + args)
        except subprocess.CalledProcessError as e:
            logging.exception(
                'tc command failed; return code %d, output:\n%s\n',
                e.returncode, e.output)
            return None
        # Split into words by line
        output = output.splitlines()
        output = list(map(bytes.split, output))
        return output
