# pyD50_cli

pyD50_cli wraps the [pyD50](https://github.com/AppendAutomation/pyD50) library in a single directly executable
program for Cutler-Hammer D50 PLCs, the way
[pylogix_cli](https://github.com/AppendAutomation/pylogix_cli) wraps pylogix.
With pyinstaller it can be packaged into an .exe that runs on any Windows
computer without installing Python. This is handy where the user does not have
WinGPC but needs to read or change a value, start or stop the CPU, or save or
restore the program.

Warning! PLCs control industrial equipment. Writing values, changing the CPU
mode or downloading a program to a PLC that is operating equipment should be
done with great care and is at your own risk.

Run it as a single command or as a console that accepts several commands.

## Connecting

The first argument (or the `IPAddress` command) is the target. It can be a
MOXA DE-311 (or other serial device server) IP address and port, or a serial
port:

| Target | Connection |
|---|---|
| `100.100.100.254` | MOXA in Real COM mode (ports 950/966) |
| `100.100.100.254:4001` | MOXA in TCP Server mode (raw TCP on the given port) |
| `COM3`, `/dev/ttyUSB0` | local serial port, 9600 8N1 (needs pyserial) |
| `...,7` | any of the above with PLC CPU ID 7 (default 255) |

A Real COM port serves one client at a time, so close any program that has the
MOXA's virtual COM port open (e.g. take WinGPC offline) first.

## Examples of Usage

Single command:
```
pyD50_cli 100.100.100.254 Read W0
W0 1234 Success
```

Console:
```
pyD50_cli 100.100.100.254
pyD50_cli> Read W4 REAL
W4 1.5 Success
pyD50_cli> Write M031.15 1
M031.15 True Success
pyD50_cli> GetPLCMode
Mode RUN Success
pyD50_cli> quit
```

OR
```
pyD50_cli
pyD50_cli> Read W0
ERROR - No target specified.  Use the IPAddress or SerialPort command.
pyD50_cli> IPAddress 100.100.100.254:4001
Target: TCP 100.100.100.254:4001 (CPU ID 255)
pyD50_cli> Read W0
W0 1234 Success
```

Program backup and restore:
```
pyD50_cli 100.100.100.254 Upload line3.json
Uploaded P1020 (351 words, 324 steps) to line3.json
pyD50_cli 100.100.100.254 VerifyProgram line3.json
The PLC program matches line3.json.
pyD50_cli 100.100.100.254 Download line3.json
```
At a terminal, `Download` asks for confirmation first. The CPU is stopped while
the program is written and then returned to its previous mode. Add `run` or
`stop` to choose the mode afterwards.

## Commands

```
    Connection Commands: (Not case sensitive.)
        IPAddress <address>[:<port>][,<cpuid>]
                                    - Sets the target PLC (MOXA address or serial port).
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
                                      Read W10 20.
        Write <tag> <value>[,<value>...] [<type>]
                                    - Writes a tag, e.g. Write W0 1234, Write M031.15 1,
                                      Write W4 1.5 REAL, Write W10 1,2,3.
        GetDeviceProperties         - CPU ID, project name, memory, mode, watchdog and scan times.
        GetPLCMode                  - Returns RUN or STOP.
        Run (or Start)              - Puts the CPU in RUN.
        Stop                        - Puts the CPU in STOP.
        Upload <filename>           - Reads the program from the PLC into a file.
        Download <filename> [run|stop]
                                    - Writes a program file to the PLC.
        VerifyProgram <filename>    - Compares the PLC program with a program file.
        GetProjectName              - Returns the project name stored in the PLC.
        VerifyPassword <password>   - Checks a password against the PLC's.
        GetErrorTable               - Returns the raw words SR32-SR46 (decoding not yet known).
        ReadParameter <address> [<count>]
                                    - Reads raw parameter/program area words.
        Message <command> [<hex data>]
                                    - Sends any D50 command and prints the raw reply.

    Other Commands:
        Help, Quit, Sleep <seconds>, Version,
        Wait <tag> <cond> <value> [echo=y] [interval=1] [timeout=<seconds>] [type=<type>],
        Output (Raw | Readable | Minimal), Timing (On | Off)

    Multi-Tag Commands: (Filenames are case sensitive.)
        ReadTagFile <filename> [<outfile>]   - one "tag [count] [type]" per line
        WriteTagFile <filename>              - comma delimited "tag, value[, type]" lines

    Script Commands: (Filenames are case sensitive.)
        RunScript <filename>
        ValidateScript <filename>
```

Addresses and types are described in the [pyD50 README](https://github.com/AppendAutomation/pyD50#addresses).
Types are `WORD` (the default), `INT`, `DWORD`, `DINT`, `REAL` and `LREAL`.

### Scripts, tag files and Wait

These work as they do in pylogix_cli:

* `RunScript` validates a file of console commands before running any of them.
* `Wait` polls a tag until a condition is met. Press `q` to skip the wait or
  `c` to cancel the script.
* `ReadTagFile` reads its tags in one batched request where it can.

See [`examples/test_script.txt`](examples/test_script.txt):
```
pyD50_cli RunScript examples/test_script.txt
```

## Development Environment

pyD50_cli imports `pyd50` from the first of these places that has it:

1. a `pyd50` folder next to the script (vendored, for a self-contained build);
2. an installed copy (`pip install git+https://github.com/AppendAutomation/pyD50`);
3. a `pyD50` project folder checked out next to this one (`../pyD50`).

```
pip install pyinstaller pyserial
pip install git+https://github.com/AppendAutomation/pyD50
```

### Building the Executable
Build on Windows. For an .exe that also runs on Windows 7, build on Windows 7
with Python 3.8, since Python 3.9+ does not run on Windows 7.
```
pyinstaller -F pyD50_cli.py
```

## License

MIT. See [LICENSE.txt](LICENSE.txt).
