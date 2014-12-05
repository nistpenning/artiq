#!/usr/bin/env python3

import argparse

from artiq.management.pc_rpc import Client


def _get_args():
    parser = argparse.ArgumentParser(description="ARTIQ client")
    parser.add_argument(
        "-s", "--server", default="::1",
        help="hostname or IP of the master to connect to")
    parser.add_argument(
        "--port", default=8888, type=int,
        help="TCP port to use to connect to the master")
    parser.add_argument(
        "-o", "--run-once", default=[], nargs=3,
        action="append",
        help="run experiment once. arguments: <path> <name> <timeout>")
    return parser.parse_args()


def main():
    args = _get_args()
    remote = Client(args.server, args.port, "master")
    try:
        for path, name, timeout in args.run_once:
            remote.run_once(
                {
                    "path": path,
                    "name": name
                }, int(timeout))
    finally:
        remote.close_rpc()

if __name__ == "__main__":
    main()