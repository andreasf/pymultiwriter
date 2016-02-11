#!/usr/bin/env python
import pyudev
import argparse
from datetime import datetime
import errno
import time
import os
import sys
import curses
from multiprocessing import Queue, Process
from Queue import Empty
import subprocess


def main():
    parser = argparse.ArgumentParser(description="""
    Writes a disk image to multiple USB drives in parallel. All your disk
    are belong to us!""")
    parser.add_argument("image_file", help="image file to write")
    args = parser.parse_args()
    if not os.path.exists(args.image_file):
        panic("File not found: %s" % args.image_file)
    if os.getuid() != 0:
        panic("Please run as root!")

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by('block')
    tui = ConsoleUI(args)
    handler = BlockEventHandler(tui.queue)
    observer = pyudev.MonitorObserver(monitor, handler.handle_event)
    observer.start()
    tui.main_loop()


class ConnectedEvent(object):
    def __init__(self, device):
        self.device = device.device_node
        self.size = device.attributes.asint("size") * 512
        name = "unknown USB disk"
        if "vendor" in device.parent.attributes.available_attributes:
            name = device.parent.attributes.asstring("vendor").strip()
        if "model" in device.parent.attributes.available_attributes:
            name += " " + device.parent.attributes.asstring("model").strip()
        self.name = name


class DisconnectedEvent(object):
    def __init__(self, device):
        self.device = device.device_node


class ProgressEvent(object):
    def __init__(self, device, bytes_written, seconds):
        self.device = device
        self.bytes_written = bytes_written
        self.seconds = seconds


class ErrorEvent(object):
    def __init__(self, device, msg):
        self.device = device
        self.msg = msg


class QuitEvent(object):
    def __init__(self, device):
        self.device = device


class BlockEventHandler(object):
    def __init__(self, progress_queue):
        self.tui_queue = progress_queue

    def handle_event(self, action, device):
        if device.find_parent("usb") is not None and device.device_type == "disk":
            if action == "add":
                event = ConnectedEvent(device)
                self.tui_queue.put(event)
            elif action == "remove":
                event = DisconnectedEvent(device)
                self.tui_queue.put(event)


class ConsoleUI(object):
    def __init__(self, args):
        self.queue = Queue()
        self.fn = args.image_file
        self.basename = os.path.basename(self.fn)
        self.size = os.stat(self.fn).st_size
        self.messages = []
        self.devices = dict()
        self.processes = dict()
        self.scr = curses.initscr()
        self.progress = dict()
        self.scr.keypad(1)
        self.exited = False
        self.scr.nodelay(True)
        self.selected = None
        self.log("Image file: %s (%s MiB)" % (self.fn, mib(self.size)))

    def add_existing_devices(self):
        ctx = pyudev.Context()
        for device in ctx.list_devices(subsystem="block", DEVTYPE="disk"):
            if device.find_parent("usb") is not None:
                event = ConnectedEvent(device)
                self.devices[device.device_node] = event
                if event.size < self.size:
                    self.progress[event.device] = ":-("
                else:
                    self.progress[event.device] = "idle"
        if len(self.devices) > 0:
            self.selected = sorted(self.devices.keys())[0]

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M")
        self.messages.append("%s %s" % (ts, msg))

    def main_loop(self):
        self.add_existing_devices()
        self.log("Select a device, then press enter to start writing...")
        self.draw()
        try:
            while True:
                try:
                    try:
                        obj = self.queue.get(True, 0.1)
                    except IOError as e:
                        if e.errno != errno.EINTR:
                            raise
                    if isinstance(obj, ConnectedEvent):
                        self.connect(obj)
                    elif isinstance(obj, DisconnectedEvent):
                        self.disconnect(obj)
                    elif isinstance(obj, ProgressEvent):
                        self.set_progress(obj)
                    elif isinstance(obj, ErrorEvent):
                        self.progress[obj.device] = "error"
                        self.log("Error writing to %s: %s" % (obj.device, obj.msg))
                    elif isinstance(obj, QuitEvent):
                        del self.processes[obj.device]
                except Empty:
                    char = self.scr.getch()
                    while char is not curses.ERR:
                        if char == curses.KEY_UP:
                            self.cursor_up()
                            self.draw()
                        elif char == curses.KEY_DOWN:
                            self.cursor_down()
                            self.draw()
                        elif char == curses.KEY_ENTER or char == ord("\n"):
                            self.enter()
                            self.draw()
                        char = self.scr.getch()
                self.draw()
        except KeyboardInterrupt:
            self.exit()
            self.exited = True
            if len(self.processes) > 0:
                print("Exited while writing to the following disks:")
                for dev in sorted(self.processes.keys()):
                    d = self.devices[dev]
                    print("%s (%s, %s MiB): %s" % (d.name, d.device,
                                                   mib(d.size), self.status(dev)))
        finally:
            if not self.exited:
                self.exit()

    def cursor_up(self):
        devs = sorted(self.devices.keys())
        idx = devs.index(self.selected) - 1
        if idx < 0:
            idx = 0
        self.selected = devs[idx]

    def set_progress(self, event):
        speed = mib(event.bytes_written) / event.seconds
        eta = (self.size - event.bytes_written) * (event.seconds / event.bytes_written)
        percent = (100.0 * event.bytes_written) / self.size
        if event.bytes_written == self.size:
            msg = "finished after %s seconds (%.2f MiB/s)" % (event.seconds, speed)
            self.log("finished writing to %s" % event.device)
        else:
            msg = "%.2f%%, %.2f MiB/s, %d seconds remaining" % (percent, speed, eta)
        self.progress[event.device] = msg

    def cursor_down(self):
        devs = sorted(self.devices.keys())
        idx = devs.index(self.selected) + 1
        if idx > len(self.devices) - 1:
            idx = len(self.devices) - 1
        self.selected = devs[idx]

    def enter(self):
        if self.selected is not None:
            self.start_writing(self.selected)

    def start_writing(self, dev):
        if dev not in self.processes:
            if self.devices[dev].size < self.size:
                self.log("cannot write to %s: insufficient disk space!" % dev)
            else:
                self.log("writing image to %s..." % dev)
                p = Process(target=write_to_device, args=(self.fn, dev, self.queue))
                p.start()
                self.processes[dev] = p
                self.progress[dev] = "started writing..."
        else:
            self.log("already writing to %s!" % dev)

    def disconnect(self, event):
        dev = self.devices[event.device]
        self.log("disconnected: %s (%s, %s MiB)" % (dev.name, dev.device, mib(dev.size)))
        if event.device in self.devices:
            del self.devices[event.device]
        if event.device in self.progress:
            del self.progress[event.device]
        if self.selected == event.device:
            if len(self.devices) == 0:
                self.selected = None
            else:
                devs = sorted(self.devices.keys() + [event.device])
                idx = devs.index(event.device) - 1
                if idx < 0:
                    idx = 0
                self.selected = devs[idx]

    def connect(self, event):
        self.log("connected: %s (%s, %s MiB)" % (event.name, event.device, mib(event.size)))
        self.devices[event.device] = event
        if event.size < self.size:
            self.progress[event.device] = ":-("
        else:
            self.progress[event.device] = "idle"
        if len(self.devices) == 1:
            self.selected = event.device

    def draw(self):
        s = self.scr
        height, width = s.getmaxyx()
        num_msgs = height - len(self.devices) - 5
        s.erase()
        title_left = "pymultiwriter: %s" % self.basename
        title_right = "ctrl+c to exit"
        spaces = max(1, width - len(title_left) - len(title_right))
        title = "%s%s%s" % (title_left, spaces * " ", title_right)
        s.addstr(title + (width - len(title)) * " ", curses.A_REVERSE)
        s.addstr("\n")
        for msg in self.messages[-1 * num_msgs:]:
            s.addstr(msg + "\n")
        s.addstr("\n\nConnected devices:\n", curses.A_BOLD)
        lines = []
        for dev_id in sorted(self.devices.keys()):
            dev = self.devices[dev_id]
            if self.selected == dev_id:
                attr = curses.A_REVERSE
            else:
                attr = curses.A_NORMAL
            lines.append(("%s (%s, %s MiB)" % (dev.name, dev.device, mib(dev.size)),
                          self.status(dev_id), attr))
        max_len = 0
        tabwidth = 8
        for line in lines:
            if len(line[0]) > max_len:
                max_len = len(line[0])
        indent = max_len + (tabwidth - max_len % tabwidth)
        for dev, status, attr in lines:
            tabs = (indent - len(dev) - 1) / tabwidth
            s.addstr("%s%s\t%s\n" % (dev, tabs * "\t", status), attr)
        s.refresh()

    def status(self, device):
        return self.progress[device]

    def exit(self):
        curses.nocbreak()
        self.scr.keypad(0)
        curses.echo()
        curses.endwin()


def write_to_device(fn, device, progress_queue):
    try:
        mounted = open("/proc/mounts").read().splitlines()
        for mp in mounted:
            cols = mp.split()
            mount_dev = cols[0]
            mount_mp = cols[1].decode("string-escape")
            if mount_dev.startswith(device):
                subprocess.check_call(["umount", "--force", mount_mp])

        start = time.time()
        bs = 4 * 2 ** 20
        infile = open(fn, "rb")
        outfile = open(device, "wb")
        buf = infile.read(bs)
        written = 0
        while buf != "":
            outfile.write(buf)
            written += len(buf)
            buf = infile.read(bs)
            outfile.flush()
            os.fsync(outfile.fileno())
            progress_queue.put(ProgressEvent(device, written, time.time() - start))
        outfile.close()
        infile.close()
    except subprocess.CalledProcessError as e:
        progress_queue.put(ErrorEvent(device, str(e)))
    except IOError as e:
        progress_queue.put(ErrorEvent(device, str(e)))
    except KeyboardInterrupt:
        pass
    finally:
        progress_queue.put(QuitEvent(device))


def panic(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    sys.exit(1)


def mib(size_bytes):
    return int(round((1.0 * size_bytes) / (1024 * 1024)))


if __name__ == "__main__":
    main()
