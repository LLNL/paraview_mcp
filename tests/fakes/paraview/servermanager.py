"""Fake `paraview.servermanager`: just enough for ping (B-10)."""


class vtkSMProxyManager:
    @staticmethod
    def GetVersionMajor():
        return 6

    @staticmethod
    def GetVersionMinor():
        return 1

    @staticmethod
    def GetVersionPatch():
        return 1


class _Connection:
    def __init__(self, remote=False, host=None, port=None):
        self._remote = remote
        self.ds_host = host
        self.ds_port = port

    def IsRemote(self):
        return self._remote


ActiveConnection = _Connection(remote=False)


def _reset():
    global ActiveConnection
    ActiveConnection = _Connection(remote=False)


def _set_client_server(host="localhost", port=11111):
    global ActiveConnection
    ActiveConnection = _Connection(remote=True, host=host, port=port)
