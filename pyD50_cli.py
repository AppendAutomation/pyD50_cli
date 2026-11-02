
'''
The pyD50_cli application encapsulates the pyD50 library in a single directly
executable program for Cutler-Hammer D50 PLCs, in the same way pylogix_cli
does for pylogix.  Using pyinstaller, pyD50_cli can be packaged into an .exe
file that runs on any Windows computer without installing Python.

The PLC is reached through a MOXA DE-311 (or other serial device server) on
the network, or through a local serial port:

    100.100.100.254             MOXA Real COM mode (data port 950, command port 966)
    100.100.100.254:4001        MOXA TCP Server mode (raw TCP on the given port)
    COM3  or  /dev/ttyUSB0      local serial port, 9600 8N1

An optional PLC CPU ID (station number, default 255) may be appended after a
comma, e.g. 100.100.100.254,255.

Single command syntax:
pyD50_cli 100.100.100.254 Read W0

Console app example:
pyD50_cli 100.100.100.254
pyD50_cli> Read W0
W0 1234 Success
pyD50_cli> quit
'''

import sys
import os
import time

from pathlib import Path

# Prefer a pyd50 package placed alongside this script (vendored for
# pyinstaller builds), then an installed one, then the sibling pyD50 project.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
try:
    import pyd50
except ImportError:
    sys.path.insert(1, os.path.join(_here, '..', 'pyD50'))
    import pyd50

from pyd50 import PLC, Program
from pyd50 import d50_address
from pyd50.d50_comm import is_serial_device
from pyd50.d50_response import Response

version = "0.1.0"
comm = PLC()
output_format = "raw"
output_formats = ["raw", "readable", "minimal"]
show_timing = False
# Set True by the Wait command when the user presses 'c' to cancel a running
# script.  RunScript checks and clears it after each command.
cancel_requested = False

#region CONSOLE COMMAND DEFINITIONS
def ipAddress(args):
    """
    IPAddress <address>[:<port>][,<cpuid>]
    The address may also be a serial port (COM3, /dev/ttyUSB0).
    """
    if not args.strip():
        print("ERROR - Please specify an address, e.g. 100.100.100.254 or 100.100.100.254:4001 or COM3.")
        return
    address, port, cpu_id = parseTarget(args)
    comm.Close()
    comm.IPAddress = address
    comm.Port = port
    comm.Transport = None           # choose automatically from the address and port
    if cpu_id is not None:
        comm.CPUID = cpu_id
    print("Target: {} (CPU ID {})".format(comm.ConnectionDescription, comm.CPUID))

def serialPort(args):
    """
    SerialPort <device>[,<cpuid>] - an explicit way to choose a local serial port.
    """
    if not args.strip():
        print("ERROR - Please specify a serial port, e.g. COM3 or /dev/ttyUSB0.")
        return
    device, _, cpu = args.strip().partition(",")
    comm.Close()
    comm.IPAddress = device.strip()
    comm.Port = None
    comm.Transport = "serial"
    if cpu.strip().isdigit():
        comm.CPUID = int(cpu)
    print("Target: {} (CPU ID {})".format(comm.ConnectionDescription, comm.CPUID))

def transport(args):
    """
    Transport <auto | realcom | tcp | serial>
    """
    kind = args.strip().casefold()
    if kind not in ("auto", "realcom", "tcp", "serial"):
        print("ERROR - Transport must be Auto, RealCom, TCP or Serial.")
        return
    comm.Close()
    comm.Transport = None if kind == "auto" else kind
    print("Target: {}".format(comm.ConnectionDescription))

def serialSettings(args):
    """
    SerialSettings <baud> [<parity> <databits> <stopbits>]   e.g. 9600 N 8 1
    Applies to a local serial port and to the MOXA's port in Real COM mode.
    """
    words = args.split()
    try:
        baud = int(words[0])
        parity = words[1].upper() if len(words) > 1 else comm.Parity
        databits = int(words[2]) if len(words) > 2 else comm.DataBits
        stopbits = int(words[3]) if len(words) > 3 else comm.StopBits
    except (IndexError, ValueError):
        print("ERROR - Usage: SerialSettings <baud> [<parity N|E|O> <databits> <stopbits>]")
        return
    if parity not in ("N", "E", "O") or databits not in (7, 8) or stopbits not in (1, 2):
        print("ERROR - Parity must be N, E or O, data bits 7 or 8 and stop bits 1 or 2.")
        return
    comm.Close()
    comm.BaudRate, comm.Parity, comm.DataBits, comm.StopBits = baud, parity, databits, stopbits
    print("Serial settings {} {}{}{}".format(baud, databits, parity, stopbits))

def cpuID(args):
    val = args.strip()
    if not val.isdigit() or not 0 <= int(val) <= 255:
        print("ERROR - Please specify a CPU ID from 0 to 255.")
        return
    comm.CPUID = int(val)
    print("CPU ID {}".format(comm.CPUID))

def connection(args):
    print("Target: {} (CPU ID {}, {} {}{}{}, timeout {}s)".format(
        comm.ConnectionDescription, comm.CPUID, comm.BaudRate, comm.DataBits,
        comm.Parity, comm.StopBits, comm.SocketTimeout))

def timeout(args):
    try:
        comm.SocketTimeout = float(args)
    except ValueError:
        print("ERROR - Please specify the timeout in seconds.")
        return
    print("Timeout {}s".format(comm.SocketTimeout))

def read(args):
    """
    Read <tag> [<count>] [<datatype>]
    """
    words = args.split()
    if len(words) == 0:
        print("ERROR - Usage: Read <tag> [count] [datatype]")
        return
    tag, count, datatype = words[0], 1, None
    for w in words[1:]:
        if w.isdigit():
            count = int(w)
        else:
            datatype = w.upper()
    if not checkTarget():
        return
    printResponse(comm.Read(tag, count, datatype))

def write(args):
    """
    Write <tag> <value>[,<value>...] [<datatype>]
    """
    words = args.split()
    if len(words) < 2:
        print("ERROR - Usage: Write <tag> <value>[,<value>...] [datatype]")
        return
    tag, value = words[0], words[1]
    datatype = words[2].upper() if len(words) > 2 else None
    if not checkTarget():
        return
    printResponse(writeTagValue(tag, value, datatype))

def writeTagValue(tag, value, datatype=None):
    """
    Converts the text of a value (or a comma separated list of values) and
    writes it.  Numbers containing '.' or 'e' are written as floats.
    """
    try:
        values = [convertValue(v.strip(), tag, datatype) for v in value.split(",")]
    except ValueError as e:
        return Response(tag, None, "Invalid parameter value - {}".format(e))
    return comm.Write(tag, values if len(values) > 1 else values[0], datatype)

def convertValue(text, tag, datatype):
    if isBitTag(tag):
        return d50_address.to_bool(text)
    if datatype in ("REAL", "LREAL"):
        return float(text)
    if isInteger(text):
        return int(text)
    if text.casefold().startswith("0x"):
        return int(text, 16)
    raise ValueError("'{}' is not a valid value for {}".format(text, datatype or "WORD"))

def readTagFile(args):
    words = args.split()
    if len(words) == 0:
        print("ERROR - Please specify a filename.")
        return
    if not checkTarget():
        return
    filename = words[0]
    start_time = time.time()
    outData = getTagValuesFromFile(filename)
    if len(outData) > 0:
        outFile = words[1] if len(words) > 1 else ""
        exec_time = time.time() - start_time
        if len(outFile) > 0:
            Path(outFile).write_text("\n".join(outData) + "\n")
        else:
            print("\n".join(outData))
        if show_timing:
            print("Executed in {0:7.3f} seconds.".format(exec_time))

def writeTagFile(args):
    """
    Writes the comma delimited "tag, value[, datatype]" lines of a file.
    Blank lines and lines beginning with '#' are ignored.
    """
    words = args.split()
    if len(words) == 0:
        print("ERROR - Please specify a filename.")
        return
    filename = words[0]
    try:
        lines = Path(filename).read_text().splitlines()
    except Exception as error:
        print("ERROR - Error opening the file {0}. {1}".format(filename, str(error)))
        return
    if not checkTarget():
        return
    start_time = time.time()
    for lineNum, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 2:
            print("Line {0}: ERROR - Expected 'tag, value'.  Skipping '{1}'.".format(lineNum, stripped))
            continue
        datatype = parts[2].upper() if len(parts) > 2 and parts[2] else None
        printResponse(writeTagValue(parts[0], parts[1], datatype))
    if show_timing:
        print("Executed in {0:7.3f} seconds.".format(time.time() - start_time))

def sleep(args):
    """
    Pauses execution for the given number of seconds, e.g. "Sleep 0.5".
    """
    try:
        seconds = float(args.strip())
    except ValueError:
        print("ERROR - Invalid argument.  Please specify the number of seconds to sleep.")
        return
    if seconds < 0:
        print("ERROR - The number of seconds to sleep must not be negative.")
        return
    time.sleep(seconds)

WAIT_CONDITIONALS = (">", "<", ">=", "<=", "==", "!=")

def wait(args):
    """
    Repeatedly reads a tag until it satisfies a condition, then returns.

        wait <tag> <conditional> <value> [echo=y] [interval=1] [timeout=<seconds>] [type=<datatype>]

    While waiting the user may press 'q' to stop waiting (and continue) or 'c'
    to cancel a running script.
    """
    global cancel_requested
    words = args.split()
    if len(words) < 3:
        print("ERROR - Usage: wait <tag> <conditional> <value> "
              "[echo=y] [interval=1] [timeout=<seconds>] [type=<datatype>]")
        return
    tag, conditional, target = words[0], words[1], words[2]
    if conditional not in WAIT_CONDITIONALS:
        print("ERROR - Invalid conditional '{0}'.  Use one of {1}.".format(
            conditional, ", ".join(WAIT_CONDITIONALS)))
        return
    echo, interval, timeout_s, datatype = True, 1.0, None, None
    for opt in words[3:]:
        key, sep, val = opt.partition("=")
        key = key.casefold()
        try:
            if not sep:
                timeout_s = float(opt)
            elif key == "echo":
                echo = val.strip().casefold() in ("y", "yes", "true", "1", "on")
            elif key == "interval":
                interval = float(val)
            elif key == "timeout":
                timeout_s = float(val)
            elif key == "type":
                datatype = val.upper()
            else:
                print("ERROR - Unrecognized option '{0}'.".format(opt))
                return
        except ValueError:
            print("ERROR - Invalid value in '{0}'.".format(opt))
            return
    if interval < 0:
        print("ERROR - The interval must not be negative.")
        return
    if not checkTarget():
        return
    if sys.stdin.isatty():
        print("Waiting for {0} {1} {2} ... (press 'q' to skip, 'c' to cancel script)".format(
            tag, conditional, target), flush=True)
    start_time = time.time()
    old_term = enterCbreak()
    try:
        while True:
            ret = comm.Read(tag, 1, datatype)
            if echo:
                printResponse(ret, flush=True)
            if ret.Value is not None:
                current_cmp, target_cmp = coerceForCompare(ret.Value, target)
                if evalConditional(current_cmp, conditional, target_cmp):
                    print("Condition met: {0} {1} {2} (current {3}).".format(
                        tag, conditional, target, ret.Value), flush=True)
                    return
            if timeout_s is not None and (time.time() - start_time) >= timeout_s:
                print("Wait timed out after {0:g} seconds; condition {1} {2} {3} not met.".format(
                    timeout_s, tag, conditional, target), flush=True)
                return
            key = sleepWithKey(interval)
            if key == "q":
                print("Wait stopped by user.", flush=True)
                return
            elif key == "c":
                print("Script cancelled by user.", flush=True)
                cancel_requested = True
                return
    finally:
        exitCbreak(old_term)

def getDeviceProperties(args):
    if not checkTarget():
        return
    ret = comm.GetDeviceProperties()
    if ret.Status != "Success" or output_format == "minimal":
        print(ret if ret.Status != "Success" else repr(ret.Value))
    else:
        print(ret.Value)

def getPLCMode(args):
    if checkTarget():
        printResponse(comm.GetPLCMode())

def runMode(args):
    if checkTarget():
        printResponse(comm.Run())

def stopMode(args):
    if checkTarget():
        printResponse(comm.Stop())

def getProjectName(args):
    if checkTarget():
        printResponse(comm.GetProjectName())

def verifyPassword(args):
    if checkTarget():
        ret = comm.VerifyPassword(args.strip())
        if ret.Status == "Success":
            print("Password is correct." if ret.Value else "Password is NOT correct.")
        else:
            print(ret)

def getErrorTable(args):
    if not checkTarget():
        return
    ret = comm.GetErrorTable()
    if ret.Status == "Success" and output_format == "readable":
        for i, w in enumerate(ret.Value):
            print("SR{:<3} {:5}  0x{:04X}".format(32 + i, w, w))
    else:
        printResponse(ret)

def upload(args):
    """
    Upload <filename> - reads the program from the PLC and saves it.
    """
    filename = args.strip()
    if not filename:
        print("ERROR - Please specify a filename to save the program to.")
        return
    if not checkTarget():
        return
    start_time = time.time()
    ret = comm.Upload(filename)
    if ret.Status == "Success":
        print("Uploaded {} to {}".format(ret.Value, filename))
    else:
        print(ret)
    if show_timing:
        print("Executed in {0:7.3f} seconds.".format(time.time() - start_time))

def download(args):
    """
    Download <filename> [run|stop] - writes a saved program to the PLC.  The
    CPU is stopped during the download and afterwards returns to the mode it
    was in, unless run or stop is given.
    """
    words = args.split()
    if len(words) == 0:
        print("ERROR - Usage: Download <filename> [run|stop]")
        return
    filename = words[0]
    run = None
    if len(words) > 1:
        mode = words[1].casefold()
        if mode not in ("run", "stop"):
            print("ERROR - The mode after the download must be run or stop.")
            return
        run = mode == "run"
    try:
        program = Program.load(filename)
    except Exception as error:
        print("ERROR - Error opening the program file {0}. {1}".format(filename, str(error)))
        return
    if not checkTarget():
        return
    if sys.stdin.isatty() and not confirm(
            "Download {} to the PLC at {}?  The CPU will be stopped. (y/n) ".format(
                program, comm.ConnectionDescription)):
        print("Download cancelled.")
        return
    start_time = time.time()
    ret = comm.Download(program, run)
    if ret.Status == "Success":
        print("Downloaded {}".format(ret.Value))
    else:
        print(ret)
    if show_timing:
        print("Executed in {0:7.3f} seconds.".format(time.time() - start_time))

def verifyProgram(args):
    """
    VerifyProgram <filename> - compares the PLC's program with a saved one.
    """
    filename = args.strip()
    if not filename:
        print("ERROR - Please specify a program file.")
        return
    if not checkTarget():
        return
    ret = comm.VerifyProgram(filename)
    if ret.Status != "Success":
        print(ret)
    elif not ret.Value:
        print("The PLC program matches {}.".format(filename))
    else:
        print("The PLC program differs from {} in {} words (first at program word {}).".format(
            filename, len(ret.Value), ret.Value[0]))

def readParameter(args):
    """
    ReadParameter <address> [<count>] - raw parameter/program area words.
    """
    words = args.split()
    try:
        address = int(words[0], 0)
        count = int(words[1], 0) if len(words) > 1 else 1
    except (IndexError, ValueError):
        print("ERROR - Usage: ReadParameter <address> [count]   (address may be hex, e.g. 0x48)")
        return
    if not checkTarget():
        return
    ret = comm.ReadParameter(address, count)
    if ret.Status == "Success" and output_format == "readable":
        print(" ".join("{:04X}".format(w) for w in ret.Value))
    else:
        printResponse(ret)

def message(args):
    """
    Message <command> [<hex data>] - sends any D50 command, prints the raw reply.
    """
    words = args.split(maxsplit=1)
    try:
        command = int(words[0], 0)
        data = bytes.fromhex(words[1]) if len(words) > 1 else b""
    except (IndexError, ValueError):
        print("ERROR - Usage: Message <command> [hex data], e.g. Message 0x03 000c05")
        return
    if not checkTarget():
        return
    ret = comm.Message(command, data)
    if ret.Value is not None:
        ret.Value = ret.Value.hex()
    print(ret)

def validateScriptLines(lines):
    """
    Checks that every non-blank, non-comment line in a script begins with a
    recognized command.  Returns a list of error strings (empty when valid).
    """
    errors = []
    for lineNum, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        word = stripped.split()[0]
        cmd = word.casefold()
        if cmd in ("quit", "exit"):
            continue
        if cmd not in COMMANDS:
            errors.append("Line {0}: Unrecognized command '{1}'.".format(lineNum, word))
    return errors

def readScriptLines(filename):
    try:
        return Path(filename).read_text().splitlines()
    except Exception as error:
        print("ERROR - Error opening the file {0}. {1}".format(filename, str(error)))
        return None

def validateScript(args):
    words = args.split()
    if len(words) == 0:
        print("ERROR - Please specify a script filename.")
        return
    filename = words[0]
    lines = readScriptLines(filename)
    if lines is None:
        return
    errors = validateScriptLines(lines)
    if errors:
        print("Script {0} is INVALID:".format(filename))
        for err in errors:
            print("  " + err)
    else:
        print("Script {0} is valid.".format(filename))

def runScript(args):
    """
    Validates and then executes a script file of console commands.  The
    script is not executed at all if any command is unrecognized.
    """
    global cancel_requested
    words = args.split()
    if len(words) == 0:
        print("ERROR - Please specify a script filename.")
        return
    filename = words[0]
    lines = readScriptLines(filename)
    if lines is None:
        return
    errors = validateScriptLines(lines)
    if errors:
        print("ERROR - Script validation failed for {0}:".format(filename))
        for err in errors:
            print("  " + err)
        print("Script not executed.")
        return
    cancel_requested = False
    start_time = time.time()
    for line in lines:
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if stripped.casefold() in ("quit", "exit"):
            break
        print("pyD50_cli> " + stripped)
        parseCommand(stripped)
        if cancel_requested:
            print("Script {0} cancelled.".format(filename))
            cancel_requested = False
            break
    if show_timing:
        print("Script {0} executed in {1:7.3f} seconds.".format(filename, time.time() - start_time))

def getVersion(args):
    print("pyD50_cli v" + version + ", pyD50 v" + pyd50.__version__)

def output(args):
    global output_format
    fmt = args.strip().casefold()
    if fmt in output_formats:
        output_format = fmt
        print("Output format set to {}".format(fmt))
    else:
        print("Invalid output format.  Use Raw, Readable or Minimal.")

def timing(args):
    global show_timing
    show_timing = args.strip().casefold() in ("on", "y", "yes", "true", "1")
    print("Timing {}".format("on" if show_timing else "off"))

def getHelp(args):
    print('''
    Connection Commands: (Not case sensitive.)
        IPAddress <address>[:<port>][,<cpuid>]
                                    - Sets the target PLC.  <address> is the MOXA DE-311 (or other
                                      device server) IP address or a serial port such as COM3:
                                        100.100.100.254        MOXA Real COM mode (ports 950/966)
                                        100.100.100.254:4001   MOXA TCP Server mode (raw TCP)
                                        COM3, /dev/ttyUSB0     local serial port
        SerialPort <device>[,<cpuid>]
                                    - Sets a local serial port as the target.
        SerialSettings <baud> [<parity> <databits> <stopbits>]
                                    - Serial line settings.  Default 9600 N 8 1.
        Transport <Auto|RealCom|TCP|Serial>
                                    - Overrides the automatic choice of connection type.
        CPUID <0-255>               - Sets the PLC station number.  Default 255.
        Timeout <seconds>           - Sets the reply timeout.  Default 2.
        Connection                  - Shows the current target and settings.

    PLC Commands:
        Read <tag> [<count>] [<type>]
                                    - Reads a tag, e.g. Read W0, Read R015.05, Read W4 REAL,
                                      Read W10 20.  Types: WORD (default), INT, DWORD, DINT,
                                      REAL, LREAL.  Bits read as True/False.
        Write <tag> <value>[,<value>...] [<type>]
                                    - Writes a tag, e.g. Write W0 1234, Write M031.15 1,
                                      Write W4 1.5 REAL, Write W10 1,2,3.
        GetDeviceProperties         - CPU ID, project name, memory, mode, watchdog and scan times.
        GetPLCMode                  - Returns RUN or STOP.
        Run (or Start)              - Puts the CPU in RUN.
        Stop                        - Puts the CPU in STOP.
        Upload <filename>           - Reads the program from the PLC into a file.
        Download <filename> [run|stop]
                                    - Writes a program file to the PLC.  The CPU is stopped
                                      during the download.
        VerifyProgram <filename>    - Compares the PLC program with a program file.
        GetProjectName              - Returns the project name stored in the PLC.
        VerifyPassword <password>   - Checks a password against the PLC's.
        GetErrorTable               - Returns the raw words SR32-SR46 WinGPC reads for its
                                      CPU error table (decoding not yet known).
        ReadParameter <address> [<count>]
                                    - Reads raw parameter/program area words.
        Message <command> [<hex data>]
                                    - Sends any D50 command and prints the raw reply.

    Other Commands:
        Help                        - Displays this list of commands.
        Quit                        - Leave console application.
        Sleep <seconds>             - Pauses execution for the given number of seconds.
        Wait <tag> <cond> <value> [echo=y] [interval=1] [timeout=<seconds>] [type=<type>]
                                    - Reads <tag> until it <cond> <value>, where <cond> is
                                      one of >, <, >=, <=, ==, !=.  Press 'q' to skip the
                                      wait or 'c' to cancel a running script.
        Version                     - Returns the version of pyD50_cli and pyD50.
        Output (Raw | Readable | Minimal)
                                    - Sets the output format.  Raw is the default.
        Timing (On | Off)           - Shows how long file and program commands take.

    Multi-Tag Commands: (Filenames are case sensitive.)
        ReadTagFile <filename> [<outfile>]
            - Returns the values of the tags in the file, one "tag [count] [type]" per line.
        WriteTagFile <filename>
            - Writes the comma delimited "tag, value[, type]" lines of the file.

    Script Commands: (Filenames are case sensitive.)
        RunScript <filename>
            - Validates then executes a file of console commands, one per line.
        ValidateScript <filename>
            - Checks a script file for unrecognized commands without executing it.
    ''')

#endregion CONSOLE COMMAND DEFINITIONS

#region COMMAND LOOP
# Maps a (case-insensitive) command word to its handler.  Every handler takes
# a single argument: the text that follows the command word.  This table is
# used both to dispatch commands and to validate script files.
COMMANDS = {
    "help": getHelp,
    "ipaddress": ipAddress,
    "serialport": serialPort,
    "serialsettings": serialSettings,
    "transport": transport,
    "cpuid": cpuID,
    "connection": connection,
    "timeout": timeout,
    "read": read,
    "write": write,
    "readtagfile": readTagFile,
    "writetagfile": writeTagFile,
    "getdeviceproperties": getDeviceProperties,
    "getplcmode": getPLCMode,
    "run": runMode,
    "start": runMode,
    "stop": stopMode,
    "upload": upload,
    "download": download,
    "verifyprogram": verifyProgram,
    "getprojectname": getProjectName,
    "verifypassword": verifyPassword,
    "geterrortable": getErrorTable,
    "readparameter": readParameter,
    "message": message,
    "sleep": sleep,
    "wait": wait,
    "runscript": runScript,
    "validatescript": validateScript,
    "version": getVersion,
    "output": output,
    "timing": timing,
}

def parseCommand(command):
    words = command.split()
    if len(words) > 0:
        handler = COMMANDS.get(words[0].casefold())
        if handler is not None:
            handler(getAdditionalArgs(command))
        else:
            print("ERROR - Unrecognized command.  Enter Help for a list of commands.")

def commandLoop():
    try:
        command = input("pyD50_cli> ").strip()
        while command.casefold() != "quit" and command.casefold() != "exit":
            parseCommand(command)
            command = input("pyD50_cli> ").strip()
        if command.casefold() == "exit":
            print("I'll assume you meant quit.")
    except (EOFError, KeyboardInterrupt):
        print()

#endregion COMMAND LOOP

#region HELPER FUNCTIONS
def parseTarget(value):
    """
    Splits "<address>[:<port>][,<cpuid>]" into (address, port, cpuid).
    port and cpuid are None when not given.
    """
    target, _, cpu = value.strip().partition(",")
    target = target.strip()
    cpu_id = int(cpu) if cpu.strip().isdigit() else None
    port = None
    if not is_serial_device(target) and target.count(":") == 1:
        host, _, text = target.partition(":")
        if text.isdigit():
            target, port = host, int(text)
    return target, port, cpu_id

def isTarget(value):
    """
    True when a command line argument looks like a PLC target rather than a
    command: an IP address, host:port or a serial port name.
    """
    if value.casefold() in COMMANDS:
        return False
    target, port, _ = parseTarget(value)
    if is_serial_device(target):
        return True
    parts = target.split(".")
    return port is not None or (len(parts) == 4 and all(p.isdigit() for p in parts)) or "." in target

def checkTarget():
    if not comm.IPAddress:
        print("ERROR - No target specified.  Use the IPAddress or SerialPort command.")
        return False
    return True

def isBitTag(tag):
    try:
        return d50_address.parse(tag).IsBit
    except ValueError:
        return False

def getAdditionalArgs(command):
    parts = command.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""

def formatResponse(ret):
    if output_format == "minimal":
        return str(ret.Value) if ret.Status == "Success" else "!ERROR! " + ret.Status
    if output_format == "readable":
        if ret.Status == "Success":
            return "{} = {}".format(ret.TagName, ret.Value)
        return "{}: ERROR - {}".format(ret.TagName, ret.Status)
    return str(ret)

def printResponse(ret, flush=False):
    if isinstance(ret, list):
        for r in ret:
            print(formatResponse(r), flush=flush)
    else:
        print(formatResponse(ret), flush=flush)

def getTagValues(lines):
    """
    Reads every "tag [count] [type]" line, single elements in one batch.
    """
    requests = []
    for line in lines:
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        tag, count, datatype = words[0], 1, None
        for w in words[1:]:
            if w.isdigit():
                count = int(w)
            else:
                datatype = w.upper()
        requests.append((tag, count, datatype))
    if not requests:
        return []
    results = comm.Read(requests)
    return ["{}={}".format(r.TagName, r.Value if r.Status == "Success" else "!ERROR!") for r in results]

def getTagValuesFromFile(filename):
    try:
        lines = Path(filename).read_text().splitlines()
    except Exception as error:
        print("ERROR - Error opening the file {0}. {1}".format(filename, str(error)))
        return []
    return getTagValues(lines)

def isInteger(s):
    if s and s[0] in ('-', '+'):
        return s[1:].isdigit()
    return s.isdigit()

def confirm(prompt):
    try:
        return input(prompt).strip().casefold() in ("y", "yes")
    except EOFError:
        return False

def coerceForCompare(current, target):
    """
    Booleans are compared as booleans, numbers as floats, anything else as
    strings.  bool is checked before int because in Python a bool is an int.
    """
    if isinstance(current, bool):
        truthy = target.strip().casefold() in ("1", "true", "y", "yes", "on")
        return current, truthy
    if isinstance(current, (int, float)):
        try:
            return current, float(target)
        except (ValueError, TypeError):
            return str(current), target
    return str(current), target

def evalConditional(current, conditional, target):
    try:
        if conditional == ">":
            return current > target
        elif conditional == "<":
            return current < target
        elif conditional == ">=":
            return current >= target
        elif conditional == "<=":
            return current <= target
        elif conditional == "==":
            return current == target
        elif conditional == "!=":
            return current != target
    except TypeError:
        return False
    return False

def getKeyNonBlocking():
    """
    Returns a single character if a key is waiting on stdin, else None.
    """
    try:
        if os.name == "nt":
            import msvcrt
            if msvcrt.kbhit():
                return msvcrt.getch().decode(errors="ignore")
            return None
        if not sys.stdin.isatty():
            return None
        import select
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            return sys.stdin.read(1)
        return None
    except Exception:
        return None

def enterCbreak():
    if os.name == "nt" or not sys.stdin.isatty():
        return None
    try:
        import termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        return old
    except Exception:
        return None

def exitCbreak(old):
    if old is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
    except Exception:
        pass

def sleepWithKey(seconds):
    end = time.time() + seconds
    while True:
        key = getKeyNonBlocking()
        if key:
            return key.lower()
        remaining = end - time.time()
        if remaining <= 0:
            return None
        time.sleep(min(0.05, remaining))

#endregion HELPER FUNCTIONS

#region MAIN
def main():
    arguments = sys.argv
    if len(arguments) > 1:
        if isTarget(arguments[1]):
            address, port, cpu_id = parseTarget(arguments[1])
            comm.IPAddress = address
            comm.Port = port
            if cpu_id is not None:
                comm.CPUID = cpu_id
            if len(arguments) > 2:
                parseCommand(" ".join(arguments[2:]))
            else:
                commandLoop()
        else:
            # Not a target, so treat the arguments as a command.  This lets a
            # self-contained script that sets its own target be run directly:
            #     pyD50_cli RunScript examples/test_script.txt
            parseCommand(" ".join(arguments[1:]))
    else:
        commandLoop()
    comm.Close()

#endregion MAIN

if __name__ == "__main__":
    main()
