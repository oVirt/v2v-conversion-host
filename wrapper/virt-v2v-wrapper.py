#!/usr/bin/python2
#
# vim: foldmethod=marker foldlevel=99
#
# Copyright (c) 2018 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import atexit
from contextlib import contextmanager
import copy
import errno
from io import BytesIO
import json
import logging
import os
import pycurl
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import six

if six.PY2:
    from urlparse import urlparse
    DEVNULL = open(os.devnull, 'r+')
else:
    from urllib.parse import urlparse
    xrange = range
    DEVNULL = subprocess.DEVNULL

# Wrapper version
VERSION = "18"

LOG_LEVEL = logging.DEBUG
STATE_DIR = '/tmp'
TIMEOUT = 300
VDSM_MIN_RHV = '4.2.4'  # This has to match VDSM_MIN_VERSION!
VDSM_MIN_VERSION = '4.20.31'  # RC4, final
VIRT_V2V = '/usr/bin/virt-v2v'

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


############################################################################
#
#  Host base interface {{{
#

class BaseHost(object):
    TYPE_UNKNOWN = 'unknown'
    TYPE_OSP = 'osp'
    TYPE_POD = 'pod'
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
            return BaseHost.TYPE_POD
        else:
            return BaseHost.TYPE_UNKNOWN

    @staticmethod
    def factory(host_type):
        if host_type == BaseHost.TYPE_OSP:
            return OSPHost()
        if host_type == BaseHost.TYPE_VDSM:
            return VDSMHost()
        if host_type == BaseHost.TYPE_POD:
            return CNVHost()
        else:
            raise ValueError("Cannot build host of type: %r" % host_type)

    def __init__(self):
        self._tag = '%s-%d' % (time.strftime('%Y%m%dT%H%M%S'), os.getpid())

    # Interface

    def create_runner(self, *args, **kwargs):
        raise NotImplementedError()

    def getLogs(self):
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
#
#  }}}
#
############################################################################
#
#  Kubevirt {{{
#


class CNVHost(BaseHost):
    TYPE = BaseHost.TYPE_VDSM

    def __init__(self):
        super(CNVHost, self).__init__()
        self._k8s = K8SCommunicator()
        self._tag = '123'

    def create_runner(self, *args, **kwargs):
        return SubprocessRunner(self, *args, **kwargs)

    def getLogs(self):
        # TODO: we should either pipe everything to stdout or push to log
        # collector
        return ('/tmp', '/tmp')

    def handle_cleanup(self, data, state):
        """ Handle cleanup after failed conversion """
        # TODO: do we need to clean the PVCs?
        pass

    def handle_finish(self, data, state):
        """ Handle finish after successfull conversion """
        # TODO: update VM definition
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
        body = json.dumps([{
            "op": "add",
            "path": "/metadata/annotations/v2vConversionProgress",
            "value": str(progress)
            }])
        logging.debug('Updating progress in POD annotation')
        self._k8s.patch(body)

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
            'https://{host}:%{port}'
            '/api/v1/namespaces/{ns}/pods/{pod}').format(
                host=self._host,
                port=self._port,
                ns=self._ns,
                pod=self._pod)
        self._headers = [
            'Authorization: Bearer {}'.format(self._token),
            'Accept: application/json',
        ]

    def patch(self, body):
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
#  }}}
#
############################################################################
#
#  Openstack {{{
#


class OSPHost(BaseHost):
    TYPE = BaseHost.TYPE_VDSM

    def create_runner(self, *args, **kwargs):
        return SystemdRunner(self, *args, **kwargs)

    def getLogs(self):
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
        # Move volumes to destination project
        for vol in volumes:
            logging.info('Transfering volume: %s', vol)
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
                port_cmd.extend([
                    '--fixed-ip', 'ip-address=%s' % nic['ip_address'],
                    ])
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
        if self._run_openstack(os_command, data, destination=True) is None:
            error('Create VM failed')
            return False
        else:
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


#
#  }}}
#
############################################################################
#
#  RHV {{{
#


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
        return SystemdRunner(self, *args, **kwargs)

    def getLogs(self):
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
                for disk_id in disk_ids:
                    try:
                        disk_service = disks_service.disk_service(disk_id)
                        disk = disk_service.get()
                        if disk.status != self.sdk.types.DiskStatus.OK:
                            continue
                        logging.info('Removing disk id=%s', disk_id)
                        disk_service.remove()
                        disk_ids.remove(disk_id)
                    except self.sdk.Error:
                        logging.exception('Failed to remove disk id=%s',
                                          disk_id)
                if time.time() > endt:
                    logging.error('Timed out waiting for disks: %r', disk_ids)
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
            '-of', data['output_format'],
            ])
        if 'allocation' in data:
            v2v_args.extend([
                '-oa', data['allocation']
                ])

        if 'rhv_url' in data:
            v2v_args.extend([
                '-o', 'rhv-upload',
                '-oc', data['rhv_url'],
                '-os', data['rhv_storage'],
                '-op', data['rhv_password_file'],
                '-oo', 'rhv-cafile=%s' % data['rhv_cafile'],
                '-oo', 'rhv-cluster=%s' % data['rhv_cluster'],
                '-oo', 'rhv-direct',
                ])
            if data['insecure_connection']:
                v2v_args.extend(['-oo', 'rhv-verifypeer=%s' %
                                ('false' if data['insecure_connection'] else
                                 'true')])

        elif 'export_domain' in data:
            v2v_args.extend([
                '-o', 'rhv',
                '-os', data['export_domain'],
                ])

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
            with open(path, 'r') as f:
                for line in f:
                    if line.rstrip() == 'CLASS=Iso':
                        return True
        except OSError:
            error('Failed to read domain metadata', exception=True)
        except IOError:
            error('Failed to read domain metadata', exception=True)
        return False

#
#  }}}
#
############################################################################
#
#  Routines {{{
#


class State(object):  # {{{
    """ State object (which is a dict inside) implemented as singleton """
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
            self._filename = None

        def __getattr__(self, name):
            return getattr(self._state, name)

        def __str__(self):
            return repr(self._state)

        def get_filename(self):
            return self._filename

        def set_filename(self, name):
            self._filename = name

        def write(self):
            state = self._state.copy()
            del state['internal']
            with open(self._filename, 'w') as f:
                json.dump(state, f)

    instance = None

    def __init__(self):
        if not State.instance:
            State.instance = State.__StateObject()

    def __getattr__(self, name):
        return getattr(self.instance, name)


# }}}

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


def hard_error(msg):
    """
    Function to produce an error and terminate the wrapper.

    WARNING: This can be used only at the early initialization stage! Do NOT
    use this once the password files are written or there are any other
    temporary data that should be removed at exit. This function uses
    sys.exit() which overcomes the code responsible for removing the files.
    """
    logging.error(msg)
    sys.stderr.write(msg)
    sys.exit(1)


def error(short_message, *args, **kwargs):
    """
    Used for error reporting, e.g.:

        error('Failed create port')
        error('sock=%r; pid=%r', sock, pid)
        error(e.args[0],
              'An error occured, finishing state file...',
              exception=True)

    Note that this function is not mean to be used for all errors, only those
    that should be visible to the user. Essentially we want to report only the
    first error we encounter and do that in the form that is easy to understand
    to the user. For example, this function should not be used in
    handle_cleanup() methods. It is not used in _run_openstack() either because
    the error is not fit for user and caller should take care of proper error
    report.
    """
    if 'exception' in kwargs:
        is_exception = bool(kwargs['exception'])
        del kwargs['exception']
    else:
        is_exception = False
    if len(args) == 0:
        args = (short_message,)  # NOTE: tuple!!!
    if is_exception:
        logging.info('have exception: %r %r', args, kwargs)
        logging.exception(*args, **kwargs)
    else:
        logging.info('have error: %r %r', args, kwargs)
        logging.error(*args, **kwargs)
    state = State().instance
    state['last_message'] = {
        'message': short_message,
        'type': 'error'
        }
    state.write()


def daemonize():
    """Properly deamonizes the process and closes file desriptors."""
    sys.stderr.flush()
    sys.stdout.flush()

    pid = os.fork()
    if pid != 0:
        # Nothing more to do for the parent
        sys.exit(0)

    os.setsid()
    pid = os.fork()
    if pid != 0:
        # Nothing more to do for the parent
        sys.exit(0)

    os.umask(0)
    os.chdir('/')

    dev_null = open('/dev/null', 'w')
    os.dup2(dev_null.fileno(), sys.stdin.fileno())
    os.dup2(dev_null.fileno(), sys.stdout.fileno())
    os.dup2(dev_null.fileno(), sys.stderr.fileno())

    # Re-initialize cURL. This is necessary to re-initialze the PKCS #11
    # security tokens in NSS. Otherwise any use of SDK after the fork() would
    # lead to the error:
    #
    #    A PKCS #11 module returned CKR_DEVICE_ERROR, indicating that a
    #    problem has occurred with the token or slot.
    #
    pycurl.global_cleanup()
    pycurl.global_init(pycurl.GLOBAL_ALL)


class OutputParser(object):  # {{{

    COPY_DISK_RE = re.compile(br'.*Copying disk (\d+)/(\d+) to.*')
    DISK_PROGRESS_RE = re.compile(br'\s+\((\d+\.\d+)/100%\)')
    NBDKIT_DISK_PATH_RE = re.compile(
        br'nbdkit: debug: Opening file (.*) \(.*\)')
    OVERLAY_SOURCE_RE = re.compile(
        br' *overlay source qemu URI: json:.*"file\.path": ?"([^"]+)"')
    OVERLAY_SOURCE2_RE = re.compile(
        br'libguestfs: parse_json: qemu-img info JSON output:.*'
        br'"backing-filename".*\\"file\.path\\": ?\\"([^"]+)\\"')
    VMDK_PATH_RE = re.compile(
        br'/vmfs/volumes/(?P<store>[^/]*)/(?P<vm>[^/]*)/'
        br'(?P<disk>.*?)(-flat)?\.vmdk$')
    RHV_DISK_UUID = re.compile(br'disk\.id = \'(?P<uuid>[a-fA-F0-9-]*)\'')
    OSP_VOLUME_ID = re.compile(
            br'openstack .*\'?volume\'? \'?show\'?.* '
            br'\'?(?P<uuid>[a-fA-F0-9-]*)\'?$')
    OSP_VOLUME_PROPS = re.compile(
        br'openstack .*\'?volume\'? \'?set.*'
        br'\'?--property\'?'
        br' \'?virt_v2v_disk_index=(?P<volume>[0-9]+)/[0-9]+.*'
        br' \'?(?P<uuid>[a-fA-F0-9-]*)\'?$')
    SSH_VMX_GUEST_NAME = re.compile(br'^displayName = "(.*)"$')

    def __init__(self, v2v_log):
        # Wait for the log file to appear
        for i in range(10):
            if os.path.exists(v2v_log):
                continue
            time.sleep(1)
        self._log = open(v2v_log, 'rbU')
        self._current_disk = None
        self._current_path = None

    def parse(self, state):
        line = self._log.readline()
        while line != b'':
            logging.debug('%r', line)
            state = self.parse_line(state, line)
            line = self._log.readline()
        return state

    def parse_line(self, state, line):
        m = self.COPY_DISK_RE.match(line)
        if m is not None:
            try:
                self._current_disk = int(m.group(1))-1
                self._current_path = None
                state['disk_count'] = int(m.group(2))
                logging.info('Copying disk %d/%d',
                             self._current_disk+1, state['disk_count'])
                if state['disk_count'] != len(state['disks']):
                    logging.warning(
                        'Number of supplied disk paths (%d) does not match'
                        ' number of disks in VM (%s)',
                        len(state['disks']),
                        state['disk_count'])
            except ValueError:
                error(
                    'Failed to decode disk number',
                    'Failed to decode disk number -- conversion error',
                    exception=True)

        # VDDK
        m = self.NBDKIT_DISK_PATH_RE.match(line)
        if m is not None:
            self._current_path = m.group(1)
            if self._current_disk is not None:
                logging.info('Copying path: %s', self._current_path)
                self._locate_disk(state)

        # SSH (all outputs)
        m = self.SSH_VMX_GUEST_NAME.match(line)
        if m is not None:
            state['internal']['display_name'] = m.group(1)
            logging.info('Set VM display name to: %s',
                         state['internal']['display_name'])

        # SSH + RHV
        m = self.OVERLAY_SOURCE_RE.match(line)
        if m is not None:
            path = m.group(1)
            # Transform path to be raltive to storage
            self._current_path = self.VMDK_PATH_RE.sub(
                br'[\g<store>] \g<vm>/\g<disk>.vmdk', path)
            if self._current_disk is not None:
                logging.info('Copying path: %s', self._current_path)
                self._locate_disk(state)

        # SSH + OpenStack
        m = self.OVERLAY_SOURCE2_RE.match(line)
        if m is not None:
            path = m.group(1)
            # Transform path to be raltive to storage
            self._current_path = self.VMDK_PATH_RE.sub(
                br'[\g<store>] \g<vm>/\g<disk>.vmdk', path)
            if self._current_disk is not None:
                logging.info('Copying path: %s', self._current_path)
                self._locate_disk(state)

        m = self.DISK_PROGRESS_RE.match(line)
        if m is not None:
            if self._current_path is not None and \
                    self._current_disk is not None:
                try:
                    state['disks'][self._current_disk]['progress'] = \
                        float(m.group(1))
                    logging.debug('Updated progress: %s', m.group(1))
                except ValueError:
                    error(
                        'Failed to decode progress'
                        'Failed to decode progress -- conversion error',
                        exception=True)
            else:
                logging.debug('Skipping progress update for unknown disk')

        m = self.RHV_DISK_UUID.match(line)
        if m is not None:
            path = state['disks'][self._current_disk]['path']
            disk_id = m.group('uuid')
            state['internal']['disk_ids'][path] = disk_id
            logging.debug('Path \'%s\' has disk id=\'%s\'', path, disk_id)

        # OpenStack volume UUID
        m = self.OSP_VOLUME_ID.match(line)
        if m is not None:
            volume_id = m.group('uuid').decode('utf-8')
            ids = state['internal']['disk_ids']
            ids[len(ids)+1] = volume_id
            logging.debug('Adding OSP volume %s', volume_id)

        # OpenStack volume index
        m = self.OSP_VOLUME_PROPS.match(line)
        if m is not None:
            volume_id = m.group('uuid').decode('utf-8')
            index = int(m.group('volume'))
            # Just check
            if state['internal']['disk_ids'].get(index) != volume_id:
                logging.debug(
                    'Volume \'%s\' is NOT at index %d', volume_id, index)
        return state

    def close(self):
        self._log.close()

    def _locate_disk(self, state):
        if self._current_disk is None:
            # False alarm, not copying yet
            return

        # NOTE: We assume that _current_disk is monotonic
        for i in xrange(self._current_disk, len(state['disks'])):
            if state['disks'][i]['path'] == self._current_path:
                if i == self._current_disk:
                    # We have correct index
                    logging.debug('Found path at correct index')
                else:
                    # Move item to current index
                    logging.debug('Moving path from index %d to %d', i,
                                  self._current_disk)
                    d = state['disks'].pop(i)
                    state['disks'].insert(self._current_disk, d)
                return

        # Path not found
        logging.debug('Path \'%s\' not found in %r', self._current_path,
                      state['disks'])
        state['disks'].insert(
            self._current_disk,
            {
                'path': self._current_path,
                'progress': 0,
            })


# }}}
class BaseRunner(object):  # {{{

    def __init__(self, host, arguments, environment, log):
        self._arguments = arguments
        self._environment = environment
        self._host = host
        self._log = log
        self._pid = None
        self._return_code = None

    def is_running(self):
        """ Returns True if process is still running """
        raise NotImplementedError()

    def kill(self):
        """ Stop the process """
        raise NotImplementedError()

    @property
    def pid(self):
        """ Get PID of the process """
        return self._pid

    @property
    def return_code(self):
        """ Get return code of the process or None if it is still running """
        return self._return_code

    def run(self):
        """ Start the process """
        raise NotImplementedError()


# }}}
class SubprocessRunner(BaseRunner):  # {{{

    def is_running(self):
        return self._proc.poll() is None

    def kill(self):
        self._proc.kill()

    @property
    def pid(self):
        return self._proc.pid

    @property
    def return_code(self):
        self._proc.poll()
        return self._proc.returncode

    def run(self):
        with open(self._log, 'w') as log:
            self._proc = subprocess.Popen(
                    [VIRT_V2V] + self._arguments,
                    stdin=DEVNULL,
                    stderr=subprocess.STDOUT,
                    stdout=log,
                    env=self._environment,
                    )


# }}}
class SystemdRunner(BaseRunner):  # {{{

    def __init__(self, host, arguments, environment, log):
        super(SystemdRunner, self).__init__(host, arguments, environment, log)
        self._service_name = None
        self._tc = None

    def is_running(self):
        try:
            subprocess.check_call([
                'systemctl', 'is-active', '--quiet', self._service_name])
            return True
        except subprocess.CalledProcessError:
            return False

    @property
    def return_code(self):
        if self._return_code is None:
            if not self.is_running():
                self._return_code = self._systemd_return_code()
        return self._return_code

    def kill(self):
        try:
            subprocess.check_call(['systemctl', 'kill', self._service_name])
        except subprocess.CalledProcessError:
            error('Failed to kill virt-v2v unit', exception=True)

    def run(self):
        net_cls_dir = self._prepare_net_cls()
        unit = [
            'systemd-run',
            '--description=virt-v2v conversion',
            '--uid=%s' % self._host.get_uid(),
            '--gid=%s' % self._host.get_gid(),
            ]
        for k, v in six.iteritems(self._environment):
            unit.append('--setenv=%s=%s' % (k, v))
        unit.extend([
            'cgexec', '-g', 'net_cls:%s' % net_cls_dir,
            '/bin/sh', '-c',
            'exec "%s" "$@" > "%s" 2>&1' % (VIRT_V2V, self._log),
            VIRT_V2V])  # First argument is command name
        logging.info('systemd-run invocation: %r', unit)
        unit.extend(self._arguments)

        proc = subprocess.Popen(
                unit,
                stdin=DEVNULL,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
                )
        run_output = proc.communicate()[0]
        logging.info('systemd-run returned: %s', run_output)
        m = re.search(br'\b(run-[0-9]+\.service)\.', run_output)
        if m is None:
            raise RuntimeError(
                'Failed to find service name in output',
                run_output)
        self._service_name = m.group(1)
        logging.info('Waiting for PID...')
        for i in xrange(5):
            pid = self._systemd_property('ExecMainPID')
            if pid is not None and pid != '':
                break
            time.sleep(5)
        if pid is None or pid == '':
            raise RuntimeError('Failed to get PID for virt-v2v process')
            logging.info('Running with PID: %s', pid)
        try:
            self._pid = int(pid)
        except ValueError:
            error(
                'Invalid PID for virt-v2v process'
                'Invalid PID for virt-v2v process: %s', pid,
                exception=True)

    def _systemd_property(self, property_name):
        try:
            output = subprocess.check_output([
                'systemctl', 'show',
                '--property=%s' % property_name,
                self._service_name])
        except subprocess.CalledProcessError:
            logging.exception(
                'Failed to get "%s" for virt-v2v service from systemd',
                property_name)
            return None
        m = re.match(br'^%s=(.*)$' % property_name, output)
        if m is not None:
            return m.group(1)
        else:
            logging.error(
                'Failed to get systemd property "%s". '
                'Unexpected output: %r',
                property_name, output)
            return None

    def systemd_set_property(self, property_name, value):
        """ Set configuration property on systemd unit """
        if value is None:
            logging.warning(
                'Cannot set systemd property %s to None,'
                ' passing empty string', property_name)
            value = ''
        try:
            subprocess.check_call([
                'systemctl', 'set-property',
                self._service_name, '%s=%s' % (property_name, value)])
            return True
        except subprocess.CalledProcessError:
            logging.exception(
                'Failed to get systemd property "%s"',
                property_name)
            return False

    def set_network_limit(self, limit):
        if self._tc is None:
            return False
        return self._tc.set_limit(limit)

    def _prepare_net_cls(self):
        self._tc = TcController(
            self._host.get_tag(),
            self._host.get_uid(),
            self._host.get_gid())
        return self._tc.cgroup

    def _systemd_return_code(self):
        """ Return code after the unit exited """
        code = self._systemd_property('ExecMainStatus')
        if code is None:
            error('Failed to get virt-v2v return code')
            return -1
        try:
            return int(code)
        except ValueError:
            error('Failed to decode virt-v2v return code', exception=True)
            return -1


# }}}
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
        output = list(map(str.split, output))
        return output


@contextmanager
def log_parser(v2v_log):
    parser = None
    try:
        parser = OutputParser(v2v_log)
        yield parser
    finally:
        if parser is not None:
            parser.close()


def prepare_command(data, v2v_caps, agent_sock=None):
    v2v_args = [
        '-v', '-x',
        data['vm_name'],
        '--bridge', 'ovirtmgmt',
        '--root', 'first'
    ]

    if data['transport_method'] == 'vddk':
        v2v_args.extend([
            '-i', 'libvirt',
            '-ic', data['vmware_uri'],
            '-it', 'vddk',
            '-io', 'vddk-libdir=%s' % '/opt/vmware-vix-disklib-distrib',
            '-io', 'vddk-thumbprint=%s' % data['vmware_fingerprint'],
            '--password-file', data['vmware_password_file'],
            ])
    elif data['transport_method'] == 'ssh':
        v2v_args.extend([
            '-i', 'vmx',
            '-it', 'ssh',
            ])

    if 'network_mappings' in data:
        for mapping in data['network_mappings']:
            if 'mac_address' in mapping and 'mac-option' in v2v_caps:
                v2v_args.extend(['--mac', '%s:bridge:%s' %
                                (mapping['mac_address'],
                                    mapping['destination'])])
            else:
                v2v_args.extend(['--bridge', '%s:%s' %
                                (mapping['source'], mapping['destination'])])

    # Prepare environment
    v2v_env = os.environ.copy()
    v2v_env['LANG'] = 'C'
    if 'backend' in data:
        if data['backend'] == 'direct':
            logging.debug('Using direct backend. Hack, hack...')
        v2v_env['LIBGUESTFS_BACKEND'] = data['backend']
    if 'virtio_win' in data:
        v2v_env['VIRTIO_WIN'] = data['virtio_win']
    if agent_sock is not None:
        v2v_env['SSH_AUTH_SOCK'] = agent_sock

    return (v2v_args, v2v_env)


def throttling_update(runner, initial=None):
    """ Update throttling """
    state = State().instance
    if initial is not None:
        throttling = initial
    else:
        # Read from throttling file
        try:
            with open(state['internal']['throttling_file']) as f:
                throttling = json.load(f)
            # Remove file when finished to prevent spamming logs with repeated
            # messages
            os.remove(state['internal']['throttling_file'])
            logging.info('Fetched updated throttling info from file')
        except IOError as e:
            if e.errno != errno.ENOENT:
                error('Failed to read throttling file', exception=True)
            return
        except ValueError:
            error('Failed to read throttling file', exception=True)
            return

    # Throttling works only when we have (temporary) systemd unit. We do the
    # check here and not at the beginning because we want the throttling file
    # to be removed. We don't want to spam logs with repeated messages.
    if not isinstance(runner, SystemdRunner):
        logging.warn(
            'Not applying throttling because virt-v2v is not in systemd unit')
        return

    processed = {}
    for k, v in six.iteritems(throttling):
        if k == 'cpu':
            if v is None or v == 'unlimited':
                # Treat empty value and 'unlimited' in the same way
                val = 'unlimited'
                set_val = ''
            else:
                m = re.match("([+0-9]+)%?$", v)
                if m is not None:
                    val = r'%s%%' % m.group(1)
                    set_val = val
                else:
                    error(
                        'Failed to parse value for CPU limit',
                        'Failed to parse value for CPU limit: %s', v)
                    continue
            if val != state['throttling']['cpu'] and \
                    runner.systemd_set_property('CPUQuota', set_val):
                processed[k] = val
            else:
                error(
                    'Failed to set CPU limit',
                    'Failed to set CPU limit to %s', val)
        elif k == 'network':
            if v is None or v == 'unlimited':
                # Treat empty value and 'unlimited' in the same way
                val = 'unlimited'
                set_val = 'unlimited'
            else:
                m = re.match("([+0-9]+)$", v)
                if m is not None:
                    val = m.group(1)
                    set_val = val
                else:
                    error(
                        'Failed to parse value for network limit',
                        'Failed to parse value for network limit: %s', v)
                    continue
            if val != state['throttling']['network'] and \
                    runner.set_network_limit(set_val):
                logging.debug(
                    'Changing network throttling to %s (previous: %s)',
                    val, state['throttling']['network'])
                processed[k] = val
            else:
                error(
                    'Failed to set network limit',
                    'Failed to set network limit to %s', val)
        else:
            logging.debug('Ignoring unknown throttling request: %s', k)
    state['throttling'].update(processed)
    logging.info('New throttling setup: %r', state['throttling'])


def wrapper(host, data, state, v2v_log, v2v_caps, agent_sock=None):

    v2v_args, v2v_env = prepare_command(data, v2v_caps, agent_sock)
    v2v_args, v2v_env = host.prepare_command(
        data, v2v_args, v2v_env, v2v_caps)

    logging.info('Starting virt-v2v:')
    log_command_safe(v2v_args, v2v_env)

    runner = host.create_runner(v2v_args, v2v_env, v2v_log)
    try:
        runner.run()
    except RuntimeError as e:
        error('Failed to start virt-v2v', exception=True)
        state['failed'] = True
        state.write()
        return
    state['pid'] = runner.pid
    if 'throttling' in data:
        throttling_update(runner, data['throttling'])

    try:
        state['started'] = True
        state.write()
        with log_parser(v2v_log) as parser:
            while runner.is_running():
                state = parser.parse(state)
                state.write()
                host.update_progress()
                throttling_update(runner)
                time.sleep(5)
            logging.info(
                'virt-v2v terminated with return code %d',
                runner.return_code)
            state = parser.parse(state)
    except Exception:
        state['failed'] = True
        error('Error while monitoring virt-v2v', exception=True)
        logging.info('Killing virt-v2v process')
        runner.kill()

    state['return_code'] = runner.return_code
    state.write()

    if state['return_code'] != 0:
        state['failed'] = True
    state.write()


def write_password(password, password_files, uid, gid):
    pfile = tempfile.mkstemp(suffix='.v2v')
    password_files.append(pfile[1])
    os.fchown(pfile[0], uid, gid)
    os.write(pfile[0], bytes(password.encode('utf-8')))
    os.close(pfile[0])
    return pfile[1]


def spawn_ssh_agent(data):
    try:
        out = subprocess.check_output(['ssh-agent'])
        logging.debug('ssh-agent: %s' % out)
        sock = re.search(br'^SSH_AUTH_SOCK=([^;]+);', out, re.MULTILINE)
        pid = re.search(br'^echo Agent pid ([0-9]+);', out, re.MULTILINE)
        if not sock or not pid:
            error(
                'Incomplete match of ssh-agent output; sock=%r; pid=%r',
                sock, pid)
            return None, None
        agent_sock = sock.group(1)
        agent_pid = int(pid.group(1))
        logging.info('SSH Agent started with PID %d', agent_pid)
    except subprocess.CalledProcessError:
        error('Failed to start ssh-agent', exception=True)
        return None, None
    except ValueError:
        error('Failed to parse ssh-agent output', exception=True)
        return None, None
    env = os.environ.copy()
    env['SSH_AUTH_SOCK'] = agent_sock
    cmd = ['ssh-add']
    if 'ssh_key_file' in data:
        logging.info('Using custom SSH key')
        cmd.append(data['ssh_key_file'])
    else:
        logging.info('Using SSH key(s) from ~/.ssh')
    try:
        out = subprocess.check_output(
            cmd,
            env=env,
            stderr=subprocess.STDOUT,
            stdin=DEVNULL)
    except subprocess.CalledProcessError as e:
        error('Failed to add SSH keys to the agent', exception=True)
        logging.error("ssh-add output: %s", e.output)
        os.kill(agent_pid, signal.SIGTERM)
        return None, None
    return agent_pid, agent_sock


def virt_v2v_capabilities():
    try:
        return subprocess.check_output(['virt-v2v', u'--machine-readable']) \
                         .split('\n')
    except subprocess.CalledProcessError:
        logging.exception('Failed to start virt-v2v')
        return None


def log_command_safe(args, env, log=None):
    args = copy.deepcopy(args)
    env = copy.deepcopy(env)
    # Filter command
    arg_re = re.compile('([^=]*password[^=]*)=(.*)', re.IGNORECASE)
    for i in xrange(1, len(args)):
        m = arg_re.match(args[i])
        if m:
            args[i] = '%s=*****' % m.group(1)
    # Filter environment
    env_re = re.compile('password', re.IGNORECASE)
    for k in env.keys():
        if env_re.search(k):
            env[k] = '*****'
    # Log the result
    if log is None:
        log = logging
    log.info('Executing command: %r, environment: %r', args, env)

#
#  }}}
#
############################################################################
#
#  Checks {{{
#


def check_rhv_guest_tools():
    """
    Make sure there is ISO domain with at least one ISO with windows drivers.
    Preferably RHV Guest Tools ISO.
    """
    host = BaseHost.factory(BaseHost.TYPE_VDSM)
    data = {'install_drivers': True}
    host.check_install_drivers(data)
    return ('virtio_win' in data)


def check_rhv_version():
    import rpmUtils.transaction
    import rpmUtils.miscutils

    ts = rpmUtils.transaction.initReadOnlyTransaction()
    match = ts.dbMatch('name', 'vdsm')
    if len(match) >= 1:
        vdsm = match.next()
        res = rpmUtils.miscutils.compareEVR(
            (vdsm['epoch'], vdsm['version'], None),  # Ignore release number
            rpmUtils.miscutils.stringToVersion(VDSM_MIN_VERSION))
        if res >= 0:
            return True
        print('Version of VDSM on the host: {}{}'.format(
                '' if vdsm['epoch'] is None else '%s:' % vdsm['epoch'],
                vdsm['version']))
    print('Minimal required oVirt/RHV version is %s' % VDSM_MIN_RHV)
    return False


CHECKS = {
    'rhv-guest-tools': check_rhv_guest_tools,
    'rhv-version': check_rhv_version,
}

#  }}}
#
############################################################################
#
#  Main {{{
#


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == '--checks':
            for check in CHECKS.keys():
                print("%s" % check)
            sys.exit(0)
        if sys.argv[1][:8] == '--check-':
            check = CHECKS.get(sys.argv[1][8:])
            if check is not None and check():
                sys.exit(0)
            else:
                sys.exit(1)
        if sys.argv[1] == '--version':
            print('virt-v2v-wrapper %s' % VERSION)
            sys.exit(0)

    # Read and parse input -- hopefully this should be safe to do as root
    data = json.load(sys.stdin)

    # Fill in defaults
    if 'daemonize' not in data:
        data['daemonize'] = True

    host_type = BaseHost.detect(data)
    host = BaseHost.factory(host_type)

    # The logging is delayed after we now which user runs the wrapper.
    # Otherwise we would have two logs.
    log_tag = host.get_tag()
    log_dirs = host.getLogs()
    v2v_log = os.path.join(log_dirs[0], 'v2v-import-%s.log' % log_tag)
    wrapper_log = os.path.join(log_dirs[1],
                               'v2v-import-%s-wrapper.log' % log_tag)
    state = State().instance
    state.set_filename(
        os.path.join(STATE_DIR, 'v2v-import-%s.state' % log_tag))
    throttling_file = os.path.join(STATE_DIR,
                                   'v2v-import-%s.throttle' % log_tag)
    state['internal']['throttling_file'] = throttling_file

    log_format = '%(asctime)s:%(levelname)s:' \
        + ' %(message)s (%(module)s:%(lineno)d)'
    logging.basicConfig(
        level=LOG_LEVEL,
        filename=wrapper_log,
        format=log_format)

    logging.info('Wrapper version %s, uid=%d', VERSION, os.getuid())

    logging.info('Will store virt-v2v log in: %s', v2v_log)
    logging.info('Will store state file in: %s', state.get_filename())
    logging.info('Will read throttling limits from: %s', throttling_file)

    password_files = []

    # Collect virt-v2v capabilities
    virt_v2v_caps = virt_v2v_capabilities()
    if virt_v2v_caps is None:
        hard_error('Could not get virt-v2v capabilities.')
    logging.debug("virt-v2v capabilities: %r" % virt_v2v_caps)

    try:

        # Make sure all the needed keys are in data. This is rather poor
        # validation, but...
        if 'vm_name' not in data:
                hard_error('Missing vm_name')

        # Transports (only VDDK for now)
        if 'transport_method' not in data:
            hard_error('No transport method specified')
        if data['transport_method'] not in ('ssh', 'vddk'):
            hard_error('Unknown transport method: %s',
                       data['transport_method'])

        if data['transport_method'] == 'vddk':
            for k in [
                    'vmware_fingerprint',
                    'vmware_uri',
                    'vmware_password',
                    ]:
                if k not in data:
                    hard_error('Missing argument: %s' % k)

        # Network mappings
        if 'network_mappings' in data:
            if isinstance(data['network_mappings'], list):
                for mapping in data['network_mappings']:
                    if not all(
                            k in mapping for k in ("source", "destination")):
                        hard_error('Both "source" and "destination"'
                                   ' must be provided in network mapping')
            else:
                hard_error('"network_mappings" must be an array')

        # Virtio drivers
        if 'virtio_win' in data:
            # This is for backward compatibility
            data['install_drivers'] = True
        if 'install_drivers' in data:
            host.check_install_drivers(data)
        else:
            data['install_drivers'] = False

        # Method dependent validation
        data = host.validate_data(data)

        #
        # NOTE: don't use hard_error() beyond this point!
        #

        # Store password(s)
        logging.info('Writing password file(s)')
        if 'vmware_password' in data:
            data['vmware_password_file'] = write_password(
                    data['vmware_password'], password_files,
                    host.get_uid(), host.get_gid())
        if 'rhv_password' in data:
            data['rhv_password_file'] = write_password(data['rhv_password'],
                                                       password_files,
                                                       host.get_uid(),
                                                       host.get_gid())
        if 'ssh_key' in data:
            data['ssh_key_file'] = write_password(data['ssh_key'],
                                                  password_files,
                                                  host.get_uid(),
                                                  host.get_gid())

        try:
            if 'source_disks' in data:
                logging.debug('Initializing disk list from %r',
                              data['source_disks'])
                for d in data['source_disks']:
                    # NOTE: We expect names from virt-v2v/VMware to be UTF-8
                    # encoded. Encoding them here is safer than decoding the
                    # virt-v2v output.
                    state['disks'].append({
                        'path': d.encode('utf-8'),
                        'progress': 0})
                logging.debug('Internal disk list: %r', state['disks'])
                state['disk_count'] = len(data['source_disks'])
            # Create state file before dumping the JSON
            state.write()

            # Send some useful info on stdout in JSON
            print(json.dumps({
                'v2v_log': v2v_log,
                'wrapper_log': wrapper_log,
                'state_file': state.get_filename(),
                'throttling_file': throttling_file,
            }))

            # Let's get to work
            if 'daemonize' not in data or data['daemonize']:
                logging.info('Daemonizing')
                daemonize()
            else:
                logging.info('Staying in foreground as requested')
                handler = logging.StreamHandler(sys.stdout)
                handler.setLevel(logging.DEBUG)
                # TODO: drop junk from virt-v2v log
                formatter = logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                handler.setFormatter(formatter)
                logging.getLogger().addHandler(handler)
            agent_pid = None
            agent_sock = None
            if data['transport_method'] == 'ssh':
                agent_pid, agent_sock = spawn_ssh_agent(data)
                if agent_pid is None:
                    raise RuntimeError('Failed to start ssh-agent')
            wrapper(host, data, state, v2v_log, virt_v2v_caps, agent_sock)
            if agent_pid is not None:
                os.kill(agent_pid, signal.SIGTERM)
            if not state.get('failed', False):
                state['failed'] = not host.handle_finish(data, state)
        except Exception as e:
            # No need to log the exception, it will get logged below
            error(e.args[0],
                  'An error occured, finishing state file...',
                  exception=True)
            state['failed'] = True
            state.write()
            raise
        finally:
            if state.get('failed', False):
                # Perform cleanup after failed conversion
                logging.debug('Cleanup phase')
                try:
                    host.handle_cleanup(data, state)
                finally:
                    state['finished'] = True
                    state.write()

        # Remove password files
        logging.info('Removing password files')
        for f in password_files:
            try:
                os.remove(f)
            except OSError:
                error('Error removing password file(s)',
                      'Error removing password file: %s' % f,
                      exception=True)

        state['finished'] = True
        state.write()

    except Exception:
        logging.exception('Wrapper failure')
        # Remove password files
        logging.info('Removing password files')
        for f in password_files:
            try:
                os.remove(f)
            except OSError:
                error('Error removing password file(s)',
                      'Error removing password file: %s' % f,
                      exception=True)
        # Re-raise original error
        raise

    logging.info('Finished')
    if state['failed']:
        sys.exit(2)


# }}}
if __name__ == '__main__':
    main()
