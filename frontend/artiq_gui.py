#!/usr/bin/env python3

import argparse
import asyncio

import gbulb
from gi.repository import Gtk

from artiq.gui.scheduler import SchedulerWindow


def _get_args():
    parser = argparse.ArgumentParser(description="ARTIQ GUI client")
    parser.add_argument(
        "-s", "--server", default="::1",
        help="hostname or IP of the master to connect to")
    parser.add_argument(
        "--port-schedule-control", default=8888, type=int,
        help="TCP port to connect to for schedule control")
    parser.add_argument(
        "--port-schedule-notify", default=8887, type=int,
        help="TCP port to connect to for schedule notifications")
    return parser.parse_args()


def main():
    args = _get_args()

    asyncio.set_event_loop_policy(gbulb.GtkEventLoopPolicy())
    loop = asyncio.get_event_loop()
    try:
        win = SchedulerWindow()
        win.connect("delete-event", Gtk.main_quit)
        win.show_all()

        loop.run_until_complete(win.sub_connect(args.server,
                                                args.port_schedule_notify))
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(win.sub_close())
    finally:
        loop.close()

if __name__ == "__main__":
    main()