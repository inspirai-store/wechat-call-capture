import os
import struct
import sys
import time

mode = sys.argv[1]
out = sys.stdout.buffer

def send(width, height, value):
    pixels = bytes([value, value + 1, value + 2, 255]) * (width * height)
    out.write(struct.pack("<IIqI", width, height, value * 10, len(pixels)) + pixels)
    out.flush()

if mode == "frames":
    send(2, 2, 1)
    time.sleep(.3)
    send(3, 2, 2)
    send(3, 2, 3)
    time.sleep(2)
elif mode == "cancel":
    print("Window selection canceled", file=sys.stderr)
    sys.exit(2)
elif mode == "error":
    print("simulated backend error", file=sys.stderr)
    sys.exit(1)
elif mode == "partial":
    out.write(struct.pack("<IIqI", 2, 2, 1, 16) + b"123")
    out.flush()
elif mode == "sleep":
    time.sleep(10)
