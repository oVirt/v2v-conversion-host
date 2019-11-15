import json
import logging
import os
import pycurl
import re
import six
import subprocess
import sys
import time
import uuid

from contextlib import contextmanager
from io import BytesIO
from six.moves.urllib.parse import urlparse

from .common import error, hard_error, log_command_safe
from .runners import SubprocessRunner, SystemdRunner
from .singleton import State


TIMEOUT = 300
#
# Tweaks
#
# We cannot use the libvirt backend in virt-v2v and have to use direct backend
# for several reasons:
# - it is necessary on oVirt host when running as root; and we need to run as
#   root when using export domain as target (we use vdsm user for other
#   targets)
# - SSH transport method cannot be used with libvirt because it does not pass
#   SSH_AUTH_SOCK env. variable to the QEMU process
# - OpenStack mode has to run as root so we need direct backend there too
DIRECT_BACKEND = True


if six.PY3:
    xrange = range


class BaseHost(object):
    TYPE_UNKNOWN = 'unknown'
    TYPE_OSP = 'osp'
    TYPE_CNV = 'cnv'
    TYPE_VDSM = 'vdsm'
    TYPE = TYPE_UNKNOWN

    # NOTE: This in reality binds output method (rhv-upload, openstack) to the
    #       host type (VDSM, EL) we run on. This is not ideal as we should be
    #       able to use any (or at least some) combinations (e.g. rhv-upload
    #       from EL system). But nobody asked for this feature yet.
    @staticmethod
    def detect(data):
        if 'export_domain' in data or \
                'rhv_url' in data:
            return BaseHost.TYPE_VDSM
        elif 'osp_environment' in data:
            return BaseHost.TYPE_OSP
        elif not data['daemonize']:
            return BaseHost.TYPE_CNV
        else:
            return BaseHost.TYPE_UNKNOWN

    @staticmethod
    def factory(host_type):
        if host_type == BaseHost.TYPE_OSP:
            return OSPHost()
        if host_type == BaseHost.TYPE_VDSM:
            return VDSMHost()
        if host_type == BaseHost.TYPE_CNV:
            return CNVHost()
        else:
            raise ValueError("Cannot build host of type: %r" % host_type)

    def __init__(self):
        self._tag = '%s-%d' % (time.strftime('%Y%m%dT%H%M%S'), os.getpid())

    # Interface

    def create_runner(self, *args, **kwargs):
        raise NotImplementedError()

    def get_logs(self):
        return ('/tmp', '/tmp')

    def get_tag(self):
        return self._tag

    def handle_cleanup(self, data, state):
        """ Handle cleanup after failed conversion """
        pass

    def handle_finish(self, data, state):
        """ Handle finish after successfull conversion """
        return True

    def check_install_drivers(self, data):
        hard_error('cannot check_install_drivers for unknown host type')

    def prepare_command(self, data, v2v_args, v2v_env, v2v_caps):
        """ Prepare virt-v2v command parts that are method dependent """
        return v2v_args, v2v_env

    def get_uid(self):
        """ Tell under which user to run virt-v2v """
        return os.geteuid()

    def get_gid(self):
        """ Tell under which group to run virt-v2v """
        return os.getegid()

    def update_progress(self):
        """ Called to do tasks on progress update """
        pass

    def validate_data(self, data):
        """ Validate input data, fill in defaults, etc """
        hard_error("Cannot validate data for uknown host type")


class CNVHost(BaseHost):
    TYPE = BaseHost.TYPE_CNV

    def __init__(self):
        super(CNVHost, self).__init__()
        self._k8s = K8SCommunicator()
        self._tag = '123'

    def create_runner(self, *args, **kwargs):
        return SubprocessRunner(self, *args, **kwargs)

    def get_logs(self):
        # TODO: we should either pipe everything to stdout or push to log
        # collector
        return ('/tmp', '/tmp')

    def handle_finish(self, data, state):
        """ Handle finish after successfull conversion """
        # Store JSON into annotation
        with open('/data/vm/{}.json'.format(data['vm_name']), 'rb') as f:
            vm_data = f.read().decode('utf-8')
            patch = [{
                "op": "add",
                "path": "/metadata/annotations/v2vConversionMetadata",
                "value": vm_data,
            }]
            self._k8s.patch(json.dumps(patch))
        return True

    def check_install_drivers(self, data):
        # Nothing to do for Kubevirt
        pass

    def prepare_command(self, data, v2v_args, v2v_env, v2v_caps):
        """ Prepare virt-v2v command parts that are method dependent """
        v2v_args.extend([
            '-o', 'json',
            '-os', '/data/vm',
            '-oo', 'json-disks-pattern=disk%{DiskNo}/disk.img',
        ])
        return v2v_args, v2v_env

    def update_progress(self):
        """ Called to do tasks on progress update """
        # Update POD annotation with progress
        # Just an average now, maybe later we can weight it by disk size
        state = State().instance
        disks = [d['progress'] for d in state['disks']]
        if len(disks) > 0:
            progress = sum(disks)/len(disks)
        else:
            progress = 0

        # First make sure /metada/annotations exists then set progress
        patch = []
        pod = json.loads(self._k8s.get())
        if 'metadata' not in pod:
            patch.append({
                "op": "add",
                "path": "/metadata",
                "value": {},
            })
            pod['metadata'] = {}
            logging.debug('Creating /metadata in POD description')
        if 'annotations' not in pod['metadata']:
            patch.append({
                "op": "add",
                "path": "/metadata/annotations",
                "value": {},
            })
            pod['metadata']['annotations'] = {}
            logging.debug('Creating /metadata/annotations in POD description')
            patch.append({
                "op": "add",
                "path": "/metadata/annotations/v2vConversionProgress",
                "value": str(progress)
            })
            logging.debug('Updating progress in POD annotation')
            self._k8s.patch(json.dumps(patch))

    def validate_data(self, data):
        """ Validate input data, fill in defaults, etc """
        # No libvirt inside the POD, enforce direct backend
        data['backend'] = 'direct'
        return data


class K8SCommunicator(object):

    def __init__(self):
        self._host = os.environ['KUBERNETES_SERVICE_HOST']
        self._port = os.environ['KUBERNETES_SERVICE_PORT']
        self._pod = os.environ['HOSTNAME']

        account_dir = '/var/run/secrets/kubernetes.io/serviceaccount'
        self._cert = os.path.join(account_dir, 'ca.crt')
        with open(os.path.join(account_dir, 'namespace')) as f:
            self._ns = f.read()
        with open(os.path.join(account_dir, 'token')) as f:
            self._token = f.read()

        self._url = (
            'https://{host}:{port}'
            '/api/v1/namespaces/{ns}/pods/{pod}').format(
                host=self._host,
                port=self._port,
                ns=self._ns,
                pod=self._pod)
        # too early for logging
        # logging.info('Accessing Kubernetes on: %s', self._url)
        self._headers = [
            'Authorization: Bearer {}'.format(self._token),
            'Accept: application/json',
        ]

    def get(self):
        logging.debug('Accessing Kubernetes on: %s', self._url)
        response = BytesIO()
        c = pycurl.Curl()
        # c.setopt(pycurl.VERBOSE, 1)
        c.setopt(pycurl.URL, self._url)
        c.setopt(pycurl.HTTPHEADER, self._headers)
        c.setopt(pycurl.CAINFO, self._cert)
        c.setopt(pycurl.WRITEFUNCTION, response.write)
        c.perform()
        ret = c.getinfo(c.RESPONSE_CODE)
        logging.debug('HTTP response code %d', ret)
        if ret >= 300:
            logging.debug('response output: %s', response.getvalue())
            c.close()
        return response.getvalue()

    def patch(self, body):
        logging.debug('Accessing Kubernetes on: %s', self._url)
        data = BytesIO(body.encode('utf-8'))
        response = BytesIO()
        c = pycurl.Curl()
        # c.setopt(pycurl.VERBOSE, 1)
        c.setopt(pycurl.URL, self._url)
        c.setopt(pycurl.UPLOAD, 1)
        c.setopt(pycurl.CUSTOMREQUEST, 'PATCH')
        c.setopt(pycurl.HTTPHEADER, self._headers +
                 ['Content-Type: application/json-patch+json'])
        c.setopt(pycurl.CAINFO, self._cert)
        c.setopt(pycurl.READFUNCTION, data.read)
        c.setopt(pycurl.WRITEFUNCTION, response.write)
        c.perform()
        ret = c.getinfo(c.RESPONSE_CODE)
        logging.debug('HTTP response code %d', ret)
        if ret >= 300:
            logging.debug('response output: %s', response.getvalue())
            c.close()


class OSPHost(BaseHost):
    TYPE = BaseHost.TYPE_OSP

    def create_runner(self, *args, **kwargs):
        state = State().instance
        if state.daemonize:
            return SystemdRunner(self, *args, **kwargs)
        else:
            return SubprocessRunner(self, *args, **kwargs)

    def get_logs(self):
        log_dir = '/var/log/virt-v2v'
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        return (log_dir, log_dir)

    def handle_cleanup(self, data, state):
        """ Handle cleanup after failed conversion """
        volumes = state['internal']['disk_ids'].values()
        ports = state['internal']['ports']
        # Remove attached volumes
        for v in volumes:
            rm_args = [
                'server', 'remove', 'volume',
                data['osp_server_id'],
                v
            ]
            self._run_openstack(rm_args, data)
        # Cancel transfers
        transfers = self._run_openstack([
            'volume', 'transfer', 'request', 'list',
            '--format', 'json',
            ], data)
        if transfers is None:
            logging.error('Failed to remove transfer(s)')
        else:
            transfers = json.loads(transfers)
            # Strangely, the keys are not lowercase
            transfers = [t['ID'] for t in transfers if t['Volume'] in volumes]
            if len(transfers) > 0:
                trans_cmd = ['volume', 'transfer', 'request', 'delete']
                trans_cmd.extend(transfers)
                if self._run_openstack(trans_cmd, data) is None:
                    logging.error('Failed to remove transfer(s)')
        # Remove created ports
        if len(ports) > 0:
            logging.info('Removing ports: %r', ports)
            port_cmd = ['port', 'delete']
            port_cmd.extend(ports)
            if self._run_openstack(port_cmd, data, destination=True) is None:
                logging.error('Failed to remove port(s)')
        # Remove volumes
        if len(volumes) > 0:
            # We don't know in which project the volumes are and figuring that
            # out using openstack command can be impractical in large
            # environments. Let's just try to remove them from both.
            logging.info('Removing volume(s): %r', volumes)
            vol_cmd = ['volume', 'delete']
            vol_cmd.extend(volumes)
            if self._run_openstack(vol_cmd, data) is None:
                logging.error(
                    'Failed to remove volumes(s) from current project')
            if self._run_openstack(vol_cmd, data, destination=True) is None:
                logging.error(
                    'Failed to remove volumes(s) from destination project')

    def handle_finish(self, data, state):
        """
        Handle finish after successfull conversion

        For OpenStack this entails creating a VM instance.
        """
        vm_name = data['vm_name']
        if state['internal']['display_name'] is not None:
            vm_name = state['internal']['display_name']

        # Init keystone
        if self._run_openstack(['token', 'issue'], data) is None:
            error('Create VM failed')
            return False
        volumes = []
        # Build volume list
        for k in sorted(state['internal']['disk_ids'].keys()):
            volumes.append(state['internal']['disk_ids'][k])
        if len(volumes) == 0:
            error('No volumes found!')
            return False
        if len(volumes) != len(state['internal']['disk_ids']):
            error('Detected duplicate indices of Cinder volumes')
            logging.debug('Source volume map: %r',
                          state['internal']['disk_ids'])
            logging.debug('Assumed volume list: %r', volumes)
            return False
        for vol in volumes:
            logging.info('Transferring volume: %s', vol)
            # Checking if volume is in available state
            is_available = False
            start_at = time.time()
            while start_at + TIMEOUT > time.time():
                volume_state = self._run_openstack([
                        'volume', 'show', '-f', 'value', '-c', 'status', vol,
                        ], data)
                if volume_state is None:
                    error('Unable to get volume state, quitting.')
                    return False
                volume_state = volume_state.rstrip()
                logging.info('Current volume state: %s.', volume_state)
                if volume_state == 'available':
                    logging.info(
                        'Volume detached in %s second(s), trasferring.',
                        int(time.time() - start_at))
                    is_available = True
                    break
                time.sleep(20)
            if not is_available:
                error(
                    'Volume did not get ready (available) '
                    'for transfer within %s seconds.',
                    TIMEOUT)
                return False
            # Move volumes to the destination project
            transfer = self._run_openstack([
                'volume', 'transfer', 'request', 'create',
                '--format', 'json',
                vol,
                ], data)
            if transfer is None:
                error('Failed to transfer volume')
                return False
            transfer = json.loads(transfer)
            self._run_openstack([
                'volume', 'transfer', 'request', 'accept',
                '--auth-key', transfer['auth_key'],
                transfer['id']
                ], data, destination=True)
        # Create ports
        ports = []
        for nic in data['network_mappings']:
            port_cmd = [
                'port', 'create',
                '--format', 'json',
                '--network', nic['destination'],
                '--mac-address', nic['mac_address'],
                '--enable',
                '%s_port_%s' % (vm_name, len(ports)),
                ]
            if 'ip_address' in nic:
                ipaddr = nic['ip_address']
                subnets_cmd = [
                    'subnet', 'list',
                    '--network', nic['destination'],
                    '-f', 'json'
                ]
                subnets_json = self._run_openstack(subnets_cmd, data)
                if subnets_json is not None:
                    subnets = json.loads(subnets_json)
                    for subnet in subnets:
                        network = subnet["Subnet"]
                        if self._check_ip_in_network(ipaddr, network):
                            port_cmd.extend([
                                '--fixed-ip',
                                'ip-address=%s' % ipaddr,
                            ])
                            break
            for grp in data['osp_security_groups_ids']:
                port_cmd.extend(['--security-group', grp])
            port = self._run_openstack(port_cmd, data, destination=True)
            if port is None:
                error('Failed to create port')
                return False
            port = json.loads(port)
            logging.info('Created port id=%s', port['id'])
            ports.append(port['id'])
        state['internal']['ports'] = ports
        # Create instance
        os_command = [
            'server', 'create',
            '--format', 'json',
            '--flavor', data['osp_flavor_id'],
            ]
        for grp in data['osp_security_groups_ids']:
            os_command.extend(['--security-group', grp])
        os_command.extend(['--volume', volumes[0]])
        for i in xrange(1, len(volumes)):
            os_command.extend([
                '--block-device-mapping',
                '%s=%s' % (self._get_disk_name(i+1), volumes[i]),
                ])
        for port in ports:
            os_command.extend(['--nic', 'port-id=%s' % port])
        os_command.append(vm_name)
        # Let's get rolling...
        vm = self._run_openstack(os_command, data, destination=True)
        if vm is None:
            error('Create VM failed')
            return False
        else:
            vm = json.loads(vm)
            state['vm_id'] = str(vm.get('id'))
            logging.info('Created OSP instance with id=%s', state['vm_id'])
            return True

    def check_install_drivers(self, data):
        # Nothing to do for OSP
        pass

    def prepare_command(self, data, v2v_args, v2v_env, v2v_caps):
        """ Prepare virt-v2v command parts that are method dependent """
        v2v_args.extend([
            '-o', 'openstack',
            '-oo', 'server-id=%s' % data['osp_server_id'],
            '-oo', 'guest-id=%s' % data['osp_guest_id'],
            ])
        # Convert to arguments of the form os-something
        for k, v in six.iteritems(data['osp_environment']):
            v2v_args.extend([
                '-oo',
                '%s=%s' % (k.lower().replace('_', '-'), v)])
        if 'osp_volume_type_id' in data:
            v2v_args.extend([
                '-os', data['osp_volume_type_id'],
                ])
        if data['insecure_connection']:
            v2v_args.extend([
                '-oo', 'verify-server-certificate=false'
                ])
        return v2v_args, v2v_env

    def set_user(self, data):
        """ Possibly switch to different user """
        # Check we are running as root
        uid = os.geteuid()
        if uid != 0:
            sys.stderr.write('Need to run as root!\n')
            sys.exit(1)

    def validate_data(self, data):
        """ Validate input data, fill in defaults, etc """
        # Enforce direct backend
        data['backend'] = 'direct'
        # Check necessary keys
        for k in [
                'osp_destination_project_id',
                'osp_environment',
                'osp_flavor_id',
                'osp_security_groups_ids',
                'osp_server_id',
                ]:
            if k not in data:
                hard_error('Missing argument: %s' % k)
        if 'insecure_connection' not in data:
            data['insecure_connection'] = False
        if data.get('insecure_connection', False):
            logging.info(
                'SSL verification is disabled for OpenStack connections')
        osp_arg_re = re.compile('os[-_]', re.IGNORECASE)
        for k in data['osp_environment'].keys():
            if not osp_arg_re.match(k[:3]):
                hard_error('found invalid key in OSP environment: %s' % k)
        if 'osp_guest_id' not in data:
            data['osp_guest_id'] = uuid.uuid4()
        if not isinstance(data['osp_security_groups_ids'], list):
            hard_error('osp_security_groups_ids must be a list')
        for mapping in data['network_mappings']:
            if 'mac_address' not in mapping:
                hard_error('Missing mac address in one of network mappings')
        return data

    def _check_ip_in_network(self, ipaddr, network):
        [netaddr, netsize] = network.split('/')
        netsize = int(netsize)
        ip_prefix_bin = self._get_prefix_bin(ipaddr, netsize)
        net_prefix_bin = self._get_prefix_bin(netaddr, netsize)
        if ip_prefix_bin == net_prefix_bin:
            return True
        return False

    def _ip_to_binary(self, ipaddr):
        octet_list_int = ipaddr.split('.')
        octet_list_bin = [format(int(i), '08b') for i in octet_list_int]
        return ('').join(octet_list_bin)

    def _get_prefix_bin(self, ipaddr, netsize):
        ipaddr_bin = self._ip_to_binary(ipaddr)
        return ipaddr_bin[0:32-(32-netsize)]

    def _get_disk_name(self, index):
        if index < 1:
            raise ValueError('Index less then 1', index)
        if index > 702:
            raise ValueError('Index too large', index)
        index = index - 1
        one = index // 26
        two = index % 26
        enumid = (lambda i: chr(ord('a') + i))
        return 'vd%s%s' % ('' if one == 0 else enumid(one-1), enumid(two))

    def _run_openstack(self, cmd, data, destination=False):
        """
        Run the openstack commands with necessary arguments. When @destination
        is True the command is run in destination project. Otherwise it is run
        in current project.
        """
        command = ['openstack']
        if data.get('insecure_connection', False):
            command.append('--insecure')
        # Convert to arguments of the form os-something
        for k, v in six.iteritems(data['osp_environment']):
            command.append('--%s=%s' % (k.lower().replace('_', '-'), v))
        if destination:
            # It doesn't matter if there already is --os-project-name or
            # --os-project-id. The last argument takes precedence.
            command.append('--os-project-id=%s' %
                           data['osp_destination_project_id'])
        command.extend(cmd)
        log_command_safe(command, {})
        try:
            return subprocess.check_output(command, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            # NOTE: Do NOT use logging.exception() here as it leaks passwords
            # into the log!
            logging.error(
                'Command exited with non-zero return code %d, output:\n%s\n',
                e.returncode, e.output)
            return None


class VDSMHost(BaseHost):
    """ Encapsulates data and methods specific to oVirt/RHV environment """
    TYPE = BaseHost.TYPE_VDSM

    TOOLS_PATTERNS = [
        (7, br'RHV-toolsSetup_([0-9._]+)\.iso'),
        (6, br'rhv-tools-setup\.iso'),
        (5, br'RHEV-toolsSetup_([0-9._]+)\.iso'),
        (4, br'rhev-tools-setup\.iso'),
        (3, br'oVirt-toolsSetup_([a-z0-9._-]+)\.iso'),
        (2, br'ovirt-tools-setup\.iso'),
        (1, br'virtio-win-([0-9.]+).iso'),
        (0, br'virtio-win\.iso'),
        ]
    VDSM_LOG_DIR = '/var/log/vdsm/import'
    VDSM_MOUNTS = '/rhev/data-center/mnt'
    VDSM_CA = '/etc/pki/vdsm/certs/cacert.pem'
    VDSM_UID = 36  # vdsm
    VDSM_GID = 36  # kvm

    def __init__(self):
        super(VDSMHost, self).__init__()
        import ovirtsdk4 as sdk
        self.sdk = sdk
        # For now there are limited possibilities in how we can select
        # allocation type and format. The best thing we can do now is to base
        # the allocation on type of target storage domain.
        self.PREALLOCATED_STORAGE_TYPES = (
            self.sdk.types.StorageType.CINDER,
            self.sdk.types.StorageType.FCP,
            self.sdk.types.StorageType.GLUSTERFS,
            self.sdk.types.StorageType.ISCSI,
            self.sdk.types.StorageType.POSIXFS,
            )
        self._export_domain = False

    @contextmanager
    def sdk_connection(self, data):
        connection = None
        url = urlparse(data['rhv_url'])
        username = url.username if url.username is not None \
            else 'admin@internal'
        try:
            insecure = data['insecure_connection']
            connection = self.sdk.Connection(
                url=str(data['rhv_url']),
                username=str(username),
                password=str(data['rhv_password']),
                ca_file=str(data['rhv_cafile']),
                log=logging.getLogger(),
                insecure=insecure,
            )
            yield connection
        finally:
            if connection is not None:
                connection.close()

    def create_runner(self, *args, **kwargs):
        state = State().instance
        if state.daemonize:
            return SystemdRunner(self, *args, **kwargs)
        else:
            return SubprocessRunner(self, *args, **kwargs)

    def get_logs(self):
        """ Returns tuple with directory for virt-v2v log and wrapper log """
        return (self.VDSM_LOG_DIR, self.VDSM_LOG_DIR)

    def handle_cleanup(self, data, state):
        with self.sdk_connection(data) as conn:
            disks_service = conn.system_service().disks_service()
            transfers_service = conn.system_service().image_transfers_service()
            disk_ids = state['internal']['disk_ids'].values()
            # First stop all active transfers...
            try:
                transfers = transfers_service.list()
                transfers = [t for t in transfers if t.image.id in disk_ids]
                if len(transfers) == 0:
                    logging.debug('No active transfers to cancel')
                for transfer in transfers:
                    logging.info('Canceling transfer id=%s for disk=%s',
                                 transfer.id, transfer.image.id)
                    transfer_service = \
                        transfers_service.image_transfer_service(
                            transfer.id)
                    transfer_service.cancel()
                    # The incomplete disk will be removed automatically
                    disk_ids.remove(transfer.image.id)
            except self.sdk.Error:
                logging.exception('Failed to cancel transfers')

            # ... then delete the uploaded disks
            logging.info('Removing disks: %r', disk_ids)
            endt = time.time() + TIMEOUT
            while len(disk_ids) > 0:
                for disk_id in disk_ids[:]:
                    try:
                        disk_service = disks_service.disk_service(disk_id)
                        disk = disk_service.get()
                        if disk.status != self.sdk.types.DiskStatus.OK:
                            continue
                        logging.info('Removing disk id=%s', disk_id)
                        disk_service.remove()
                        disk_ids.remove(disk_id)
                    except self.sdk.NotFoundError:
                        logging.info('Disk id=%s does not exist (already ' +
                                     'removed?), skipping it',
                                     disk_id)
                        disk_ids.remove(disk_id)
                    except self.sdk.Error:
                        logging.exception('Failed to remove disk id=%s',
                                          disk_id)
                # Avoid checking timeouts, and waiting, if there are no
                # more disks to remove
                if len(disk_ids) > 0:
                    if time.time() > endt:
                        logging.error('Timed out waiting for disks: %r',
                                      disk_ids)
                        break
                    time.sleep(1)

    def check_install_drivers(self, data):
        """ Validate and/or find ISO with guest tools and drivers """
        if 'virtio_win' in data and os.path.isabs(data['virtio_win']):
            full_path = data['virtio_win']
        else:
            iso_domain = self._find_iso_domain()

            iso_name = data.get('virtio_win')
            if iso_name is not None:
                if iso_domain is None:
                    hard_error('ISO domain not found')
            else:
                if iso_domain is None:
                    # This is not an error
                    logging.warning('ISO domain not found' +
                                    ' (but install_drivers is true).')
                    data['install_drivers'] = False
                    return

                best_name = self._filter_iso_names(
                        iso_domain, os.listdir(iso_domain))
                if best_name is None:
                    # Nothing found, this is not an error
                    logging.warn('Could not find any ISO with drivers' +
                                 ' (but install_drivers is true).')
                    data['install_drivers'] = False
                    return
                iso_name = best_name

            full_path = os.path.join(iso_domain, iso_name)

        if not os.path.isfile(full_path):
            hard_error('"virtio_win" must be a path or file name of image in '
                       'ISO domain')
        data['virtio_win'] = full_path
        logging.info("virtio_win (re)defined as: %s", data['virtio_win'])

    def prepare_command(self, data, v2v_args, v2v_env, v2v_caps):
        v2v_args.extend([
            '--bridge', 'ovirtmgmt',
            '-of', data['output_format'],
            ])
        if 'allocation' in data:
            v2v_args.extend([
                '-oa', data['allocation']
                ])

        if 'rhv_url' in data:
            verifypeer = str(not data['insecure_connection']).lower()
            v2v_args.extend([
                '-o', 'rhv-upload',
                '-oc', data['rhv_url'],
                '-os', data['rhv_storage'],
                '-op', data['rhv_password_file'],
                '-oo', 'rhv-cluster=%s' % data['rhv_cluster'],
                '-oo', 'rhv-direct',
                '-oo', 'rhv-verifypeer=%s' % verifypeer,
                ])
            if not data['insecure_connection']:
                v2v_args.extend(['-oo', 'rhv-cafile=%s' % data['rhv_cafile']])
        elif 'export_domain' in data:
            v2v_args.extend([
                '-o', 'rhv',
                '-os', data['export_domain'],
                ])
        if 'XDG_RUNTIME_DIR' in v2v_env and self.get_uid() != 0:
            # Drop XDG_RUNTIME_DIR from environment. Otherwise it would "leak"
            # throuh our su/sudo call and would cause permissions error for
            # virt-v2v.
            #
            # https://bugzilla.redhat.com/show_bug.cgi?id=967509
            logging.info('Dropping XDG_RUNTIME_DIR from environment.')
            del v2v_env['XDG_RUNTIME_DIR']

        return v2v_args, v2v_env

    def get_uid(self):
        """ Tell under which user to run virt-v2v """
        if self._export_domain:
            # Need to be root to mount NFS share
            return 0
        return VDSMHost.VDSM_UID

    def get_gid(self):
        """ Tell under which group to run virt-v2v """
        return VDSMHost.VDSM_GID

    def validate_data(self, data):
        """ Validate input data, fill in defaults, etc """
        # Determine whether direct backend is required
        direct_backend = DIRECT_BACKEND
        if 'export_domain' in data:
            # Cannot use libvirt backend as root on VDSM host due to
            # permissions
            direct_backend = True
            self._export_domain = True
        if direct_backend:
            data['backend'] = 'direct'

        # Output file format (raw or qcow2)
        if 'output_format' in data:
            if data['output_format'] not in ('raw', 'qcow2'):
                hard_error('Invalid output format %r, expected raw or qcow2' %
                           data['output_format'])
        else:
            data['output_format'] = 'raw'

        # Targets (only export domain for now)
        if 'rhv_url' in data:
            for k in [
                    'rhv_cluster',
                    'rhv_password',
                    'rhv_storage',
                    ]:
                if k not in data:
                    hard_error('Missing argument: %s' % k)
            if 'rhv_cafile' not in data:
                logging.info('Path to CA certificate not specified')
                data['rhv_cafile'] = VDSMHost.VDSM_CA
                logging.info('... trying VDSM default: %s',
                             data['rhv_cafile'])
        elif 'export_domain' in data:
            pass
        else:
            hard_error('No target specified')

        # Insecure connection
        if 'insecure_connection' not in data:
            data['insecure_connection'] = False
        if data['insecure_connection']:
            logging.info(
                'SSL verification is disabled for oVirt SDK connections')

        if 'allocation' not in data:
            # Check storage domain type and decide on suitable allocation type
            # Note: This is only temporary. We should get the info from the
            # caller in the future.
            domain_type = None
            with self.sdk_connection(data) as c:
                service = c.system_service().storage_domains_service()
                domains = service.list(search='name="%s"' %
                                       str(data['rhv_storage']))
                if len(domains) != 1:
                    hard_error('Found %d domains matching "%s"!' %
                               (len(domains), data['rhv_storage']))
                domain_type = domains[0].storage.type
            logging.info('Storage domain "%s" is of type %r',
                         data['rhv_storage'], domain_type)
            data['allocation'] = 'sparse'
            if domain_type in self.PREALLOCATED_STORAGE_TYPES:
                data['allocation'] = 'preallocated'
            logging.info('... selected allocation type is %s',
                         data['allocation'])

        return data

    def _filter_iso_names(self, iso_domain, isos):
        """ @isos is a list of file names or an iterator """
        # (priority, pattern)
        patterns = [(p[0], re.compile(p[1], re.IGNORECASE))
                    for p in self.TOOLS_PATTERNS]
        best_name = None
        best_version = None
        best_priority = -1

        for fname in isos:
            if not os.path.isfile(os.path.join(iso_domain, fname)):
                continue
            for priority, pat in patterns:
                m = pat.match(fname)
                if not m:
                    continue
                if len(m.groups()) == 0:
                    version = b''
                else:
                    version = m.group(1)
                logging.debug('Matched ISO %r (priority %d)', fname, priority)
                if best_version is None or \
                        best_priority < priority or \
                        (best_version < version and best_priority == priority):
                    best_name = fname
                    best_version = version
                    best_priority = priority

        return best_name

    def _find_iso_domain(self):
        """
        Find path to the ISO domain from available domains mounted on host
        """
        if not os.path.isdir(self.VDSM_MOUNTS):
            logging.error('Cannot find RHV domains')
            return None
        for sub in os.walk(self.VDSM_MOUNTS):

            if 'dom_md' in sub[1]:
                # This looks like a domain so focus on metadata only
                try:
                    del sub[1][sub[1].index('master')]
                except ValueError:
                    pass
                try:
                    del sub[1][sub[1].index('images')]
                except ValueError:
                    pass
                continue

            if 'blockSD' in sub[1]:
                # Skip block storage domains, we don't support ISOs there
                del sub[1][sub[1].index('blockSD')]

            if 'metadata' in sub[2] and \
                    os.path.basename(sub[0]) == 'dom_md' and \
                    self._is_iso_domain(os.path.join(sub[0], 'metadata')):
                return os.path.join(
                    os.path.dirname(sub[0]),
                    'images',
                    '11111111-1111-1111-1111-111111111111')
        return None

    def _is_iso_domain(self, path):
        """
        Check if domain is ISO domain. @path is path to domain metadata file
        """
        try:
            logging.debug('_is_iso_domain check for %s', path)
            with open(path, 'rb') as f:
                for line in f:
                    if line.rstrip() == b'CLASS=Iso':
                        return True
        except OSError:
            error('Failed to read domain metadata', exception=True)
        except IOError:
            error('Failed to read domain metadata', exception=True)
        return False
