from .hosts import BaseHost


VDSM_MIN_RHV = '4.2.4'  # This has to match VDSM_MIN_VERSION!
VDSM_MIN_VERSION = '4.20.31'  # RC4, final


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
