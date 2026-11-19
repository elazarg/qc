"""python -m lab              the desk, with the Quad Cortex
python -m lab --simulated  the desk with a simulated unit, no hardware
python -m lab <command>    a command line task (`python -m lab --help`, and see `lab/cli.py`)"""
import sys

from .cli import main

sys.exit(main(sys.argv[1:]))
