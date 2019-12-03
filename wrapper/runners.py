import logging
import os
import re
import six
import subprocess
import time

from .common import atexit_command, error
from .tc import TcController


VIRT_V2V = '/usr/bin/virt-v2v'

if six.PY2:
    DEVNULL = open(os.devnull, 'r+')
else:
    xrange = range
    DEVNULL = subprocess.DEVNULL


class BaseRunner(object):

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


class SubprocessRunner(BaseRunner):

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


class SystemdRunner(BaseRunner):

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
        m = re.search(br'\b(run-r?[0-9a-f]+\.service)', run_output)
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
        m = re.match(br'^%s=(.*)$' % property_name.encode('utf-8'), output)
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
            code = int(code)
        except ValueError:
            error('Failed to decode virt-v2v return code', exception=True)
            return -1
        if code != 0:
            # Schedule cleanup of the failed unit
            atexit_command(['systemctl', 'reset-failed', self._service_name])
        return code
