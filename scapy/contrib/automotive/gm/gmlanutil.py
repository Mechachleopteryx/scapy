import threading
from time import sleep
from scapy.contrib.automotive.gm.gmlan import GMLAN, GMLAN_SA, GMLAN_RD, \
    GMLAN_TD, GMLAN_PM, GMLAN_RMBA
from scapy.config import conf
from scapy.contrib.isotp import ISOTPSocket
from scapy.error import warning

__all__ = ["TesterPresentThread", "InitDiagnostics", "GetSecurityAccess",
           "RequestDownload", "TransferData", "TransferPayload",
           "GMLAN_BroadcastSocket", "ReadMemoryByAddress"]


class TesterPresentThread(threading.Thread):
    """Creates a thread to periodically send the TesterPresent message.

    Args:
        socket: socket to send the message on. When using it on an entire
                network, a socket for broadcasting is recommended.

    Example:
      >>> tp = TesterPresentThread(GMLAN_BroadcastSocket('can0'))
      >>> tp.start()
      >>> tp.stop()
    """
    def __init__(self, socket):
        super(TesterPresentThread, self).__init__()
        self._stop_event = threading.Event()
        self.socket = socket

    def stop(self):
        self._stop_event.set()
        self.join()

    def stopped(self):
        return self._stop_event.is_set()

    def run(self):
        msgTP = GMLAN(b"\x3e")
        # Wakeup
        self.socket.send(msgTP)
        sleep(0.3)
        while not self.stopped():
            self.socket.send(msgTP)
            self._stop_event.wait(3)


def InitDiagnostics(socket, broadcastsocket=None, timeout=None, verbose=None,
                    retry=0):
    """Send messages to put an ECU into an diagnostic/programming state.

    Args:
        socket:     socket to send the message on.
        broadcast:  socket for broadcasting. If provided some message will be
                    sent as broadcast. Recommended when used on a network with
                    several ECUs.
        timeout:    timeout for sending, receiving or sniffing packages.
        verbose:    set verbosity level
        retry:      number of retries in case of failure.

    Returns true on success.
    """
    if verbose is None:
        verbose = conf.verb
    if retry < 0:
        retry = -retry

    while retry >= 0:
        retry -= 1

        # DisableNormalCommunication
        p = GMLAN(b"\x28")
        if broadcastsocket is None:
            if verbose:
                print("Sending DisableNormalCommunication..")
            resp = socket.sr1(p, timeout=timeout, verbose=0)
            if resp is not None:
                if verbose:
                    resp.show()
                if resp.service != 0x68:
                    continue
            else:
                if verbose:
                    print("Timeout.")
                continue
        else:
            if verbose:
                print("Sending DisableNormalCommunication as broadcast..")
            broadcastsocket.send(p)
        sleep(0.05)

        # ReportProgrammedState
        if verbose:
            print("Sending ReportProgrammedState..")
        p = GMLAN(b"\xa2")
        resp = socket.sr1(p, timeout=timeout, verbose=0)
        if resp is not None:
            if verbose:
                resp.show()
            if resp.service != 0xe2:
                continue
        else:
            if verbose:
                print("Timeout.")
            continue

        # ProgrammingMode requestProgramming
        if verbose:
            print("Sending ProgrammingMode requestProgramming..")
        p = GMLAN() / GMLAN_PM(subfunction=0x1)
        resp = socket.sr1(p, timeout=timeout, verbose=0)
        if resp is not None:
            if verbose:
                resp.show()
            if resp.service != 0xe5:
                continue
        else:
            if verbose:
                print("Timeout.")
            continue
        sleep(0.05)

        # InitiateProgramming enableProgramming
        # No response expected
        if verbose:
            print("Sending ProgrammingMode enableProgramming..")
        p = GMLAN() / GMLAN_PM(subfunction=0x3)
        socket.send(p)
        sleep(0.05)
        return True
    return False


def GetSecurityAccess(socket, keyFunction, level=1, timeout=None, verbose=None,
                      retry=0):
    """Authenticate on ECU. Implements Seey-Key procedure.

    Args:
        socket:      socket to send the message on.
        keyFunction: function implementing the key algorithm.
        level:       level of access
        timeout:     timeout for sending, receiving or sniffing packages.
        verbose:     set verbosity level
        retry:       number of retries in case of failure.

    Returns true on success.
    """
    if verbose is None:
        verbose = conf.verb
    if retry < 0:
        retry = -retry

    if level % 2 == 0:
        warning("Parameter Error: Level must be an odd number.")
        return False

    request = GMLAN() / GMLAN_SA(subfunction=level)

    while retry >= 0:
        retry -= 1
        if verbose:
            print("Requesting seed..")
        resp = socket.sr1(request, timeout=timeout, verbose=0)
        if resp is not None:
            if verbose:
                resp.show()
            if resp.service != 0x67:
                if verbose:
                    print("Negative Response.")
                continue
        else:
            if verbose:
                print("Timeout.")
            continue

        seed = resp.securitySeed
        if seed == 0:
            if verbose:
                print("ECU security already unlocked. (seed is 0x0000)")
            return True

        keypkt = GMLAN() / GMLAN_SA(subfunction=level + 1,
                                    securityKey=keyFunction(seed))
        if verbose:
            print("Responding with key..")
        resp = socket.sr1(keypkt, timeout=timeout, verbose=0)
        if resp is not None:
            if verbose:
                resp.show()
            if resp.service == 0x67:
                if verbose:
                    print("SecurityAccess granted.")
                return True
            # Invalid Key
            elif resp.service == 0x7F and resp.returnCode == 0x35:
                if verbose:
                    print("Key invalid")
                continue
        else:
            if verbose:
                print("Timeout.")
            continue
    return False


def RequestDownload(socket, length, timeout=None, verbose=None, retry=0):
    """Send RequestDownload message.

    Usually used before calling TransferData.

    Args:
        socket:     socket to send the message on.
        length:     value for the message's parameter 'unCompressedMemorySize'.
        timeout:    timeout for sending, receiving or sniffing packages.
        verbose:    set verbosity level.
        retry:      number of retries in case of failure.

    Returns true on success.
    """
    if verbose is None:
        verbose = conf.verb
    if retry < 0:
        retry = -retry

    while True:
        # RequestDownload
        pkt = GMLAN() / GMLAN_RD(memorySize=length)
        resp = socket.sr1(pkt, timeout=timeout, verbose=0)
        if resp is None:
            if verbose:
                print("Timeout.")
        else:
            # filter Response Pending
            while (resp.service == 0x7f and resp.returnCode == 0x78 and
                   resp.requestServiceId == 0x34):
                sniffed = socket.sniff(count=1, timeout=timeout,
                                       lfilter=lambda p: p.answers(pkt))
                if len(sniffed) < 1:
                    resp = None
                    break
                resp = sniffed[0]

            if resp is None:
                if verbose:
                    print("Timeout.")
            elif resp.service != 0x74:
                if verbose:
                    resp.show()
                    print("Negative Response.")
            else:
                break

        retry -= 1
        if retry >= 0:
            if verbose:
                print("Retrying..")
        else:
            return False
    return True


def TransferData(socket, addr, payload, maxmsglen=None, timeout=None,
                 verbose=None, retry=0):
    """Send TransferData message.

    Usually used after calling RequestDownload.

    Args:
        socket:     socket to send the message on.
        addr:       destination memory address on the ECU.
        payload:    data to be sent.
        maxmsglen:  maximum length of a single iso-tp message. (default:
                    maximum length)
        timeout:    timeout for sending, receiving or sniffing packages.
        verbose:    set verbosity level.
        retry:      number of retries in case of failure.

    Returns true on success.
    """
    if verbose is None:
        verbose = conf.verb
    if retry < 0:
        retry = -retry
    startretry = retry

    scheme = conf.contribs['GMLAN']['GMLAN_ECU_AddressingScheme']
    if addr < 0 or addr >= 2**(8 * scheme):
        warning("Error: Invalid address " + hex(addr) + " for scheme " +
                str(scheme))
        return False

    # max size of dataRecord according to gmlan protocol
    if maxmsglen is None or maxmsglen <= 0 or maxmsglen > (4093 - scheme):
        maxmsglen = (4093 - scheme)

    for i in range(0, len(payload), maxmsglen):
        retry = startretry
        while True:
            if len(payload[i:]) > maxmsglen:
                transdata = payload[i:i + maxmsglen]
            else:
                transdata = payload[i:]
            pkt = GMLAN() / GMLAN_TD(startingAddress=addr + i,
                                     dataRecord=transdata)
            resp = socket.sr1(pkt, timeout=timeout, verbose=0)

            if resp is None:
                if verbose:
                    print("Timeout.")
            else:
                # filter Response Pending
                while (resp.service == 0x7f and resp.returnCode == 0x78 and
                       resp.requestServiceId == 0x36):
                    sniffed = socket.sniff(count=1, timeout=timeout,
                                           lfilter=lambda p: p.answers(pkt))
                    if len(sniffed) < 1:
                        resp = None
                        break
                    resp = sniffed[0]

                if resp is None:
                    if verbose:
                        print("Timeout.")
                elif resp.service != 0x76:
                    if verbose:
                        resp.show()
                        print("Negative Response.")
                else:
                    break

            retry -= 1
            if retry >= 0:
                if verbose:
                    print("Retrying..")
            else:
                return False

    return True


def TransferPayload(socket, addr, payload, maxmsglen=None, timeout=None,
                    verbose=None, retry=0):
    """Send data by using GMLAN services.

    Args:
        socket:     socket to send the data on.
        addr:       destination memory address on the ECU.
        payload:    data to be sent.
        maxmsglen:  maximum length of a single iso-tp message. (default:
                    maximum length)
        timeout:    timeout for sending, receiving or sniffing packages.
        verbose:    set verbosity level.
        retry:      number of retries in case of failure.

    Returns true on success.
    """
    if not RequestDownload(socket, len(payload), timeout=timeout,
                           verbose=verbose, retry=retry):
        return False
    if not TransferData(socket, addr, payload, maxmsglen=maxmsglen,
                        timeout=timeout, verbose=verbose, retry=retry):
        return False
    return True


def ReadMemoryByAddress(socket, addr, length, timeout=None,
                        verbose=None, retry=0):
    """Read data from ECU memory.

    Args:
        socket:     socket to send the data on.
        addr:       source memory address on the ECU.
        length:     bytes to read
        timeout:    timeout for sending, receiving or sniffing packages.
        verbose:    set verbosity level.
        retry:      number of retries in case of failure.

    Returns the bytes read.
    """
    if verbose is None:
        verbose = conf.verb
    if retry < 0:
        retry = -retry

    scheme = conf.contribs['GMLAN']['GMLAN_ECU_AddressingScheme']
    if addr < 0 or addr >= 2**(8 * scheme):
        warning("Error: Invalid address " + hex(addr) + " for scheme " +
                str(scheme))
        return None

    # max size of dataRecord according to gmlan protocol
    if length <= 0 or length > (4094 - scheme):
        warning("Error: Invalid length " + hex(length) + " for scheme " +
                str(scheme) + ". Choose between 0x1 and " + hex(4094 - scheme))
        return None

    while True:
        # RequestDownload
        pkt = GMLAN() / GMLAN_RMBA(memoryAddress=addr, memorySize=length)
        resp = socket.sr1(pkt, timeout=timeout, verbose=0)
        if resp is None:
            if verbose:
                print("Timeout.")
        else:
            # filter Response Pending
            while (resp.service == 0x7f and resp.returnCode == 0x78 and
                   resp.requestServiceId == 0x23):
                sniffed = socket.sniff(count=1, timeout=timeout,
                                       lfilter=lambda p: p.answers(pkt))
                if len(sniffed) < 1:
                    resp = None
                    break
                resp = sniffed[0]

            if resp is None:
                if verbose:
                    print("Timeout.")
            elif resp.service != 0x63:
                if verbose:
                    resp.show()
                    print("Negative Response.")
            else:
                return resp.dataRecord

        retry -= 1
        if retry >= 0:
            if verbose:
                print("Retrying.")
        else:
            return None


def GMLAN_BroadcastSocket(interface):
    """Returns a GMLAN broadcast socket using interface."""
    return ISOTPSocket(interface, sid=0x101, did=0x0, basecls=GMLAN,
                       extended_addr=0xfe)
