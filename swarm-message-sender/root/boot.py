"""CircuitPython Essentials Storage logging boot.py file"""
import board
import digitalio
import storage

# button A
switch = digitalio.DigitalInOut(board.D20)  # pin marked '33' on FeatherS2, tied to button C
switch.direction = digitalio.Direction.INPUT
switch.pull = digitalio.Pull.UP

# If the button is pressed, then CircuitPython cannot write to storage and the computer can.
if (switch.value):
  storage.remount("/", False)
else:
  storage.remount("/", True)
