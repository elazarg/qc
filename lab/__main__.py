"""python -m lab            the desk, with the Quad Cortex
python -m lab --simulated   the desk with a simulated unit, no hardware"""
import sys

from .desk import main

main(sys.argv[1:])
