# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
# Copyright (C) 2022, nootropic design, LLC     All rights reserved.  #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
VERSION = '1.0'
import board
import displayio
import digitalio
import terminalio
import busio
import time
import neopixel
from adafruit_display_text import label
import adafruit_displayio_sh1107
from barbudor_ina3221 import *
import supervisor
import sys
import microcontroller
import json
from binascii import hexlify, a2b_base64
from microcontroller import watchdog as w
from watchdog import WatchDogMode
from adafruit_debouncer import Debouncer
import gc

try:
    import urandom as random
except ImportError:
    import random


tile = None
tileLine = bytearray(800)
tilePtr = 0

TILE_STATE_UNKNOWN = 0
TILE_STATE_REBOOTING = 1
TILE_STATE_2 = 2
TILE_STATE_3 = 3
TILE_STATE_4 = 4
TILE_STATE_5 = 5
TILE_STATE_CONFIGURED = 6


tileStateTable = [('$FV',   '$FV 20',              4, TILE_STATE_2, TILE_STATE_REBOOTING),  # 0 state
                  ('$RS',   '$TILE BOOT,RUNNING', 30, TILE_STATE_2, TILE_STATE_REBOOTING),  # 1 state
                  ('$DT 5', '$DT OK',              4, TILE_STATE_3, TILE_STATE_REBOOTING),  # 2 state
                  ('$GS 5', '$GS OK',              4, TILE_STATE_4, TILE_STATE_REBOOTING),  # 3 state
                  ('$GN 5', '$GN OK',              4, TILE_STATE_5, TILE_STATE_REBOOTING),  # 4 state
                  ('$RT 5', '$RT OK',              4, TILE_STATE_CONFIGURED, TILE_STATE_REBOOTING),  # 5 state
                  (None,     None,                 0, TILE_STATE_CONFIGURED, TILE_STATE_CONFIGURED)]  # 6 state
tileTimeout = 0.0
tileState = TILE_STATE_UNKNOWN
tileMessageFilters = ['$DT', '$RT', '$GS', '$GN', '$MT']

tcpLine = bytearray(800)
tcpPtr = 0
i2c = None

TCPHOST = ""
TCPPORT = 23
TIMEOUT = None
BACKLOG = 2
MAXBUF = 256
TCPSTATE_LISTENING = 1
TCPSTATE_CONNECTED = 2
TCPSTATE = TCPSTATE_LISTENING
tcplistener = None
tcpconn = None

config = None
displayLines = []
inaChannel = 1
inaConnected = False
inaData = {1: (None, None), 2: (None, None), 3: (None, None)}

switchA = None
switchC = None

accumulate = ""
inaTime = 0

pixels = neopixel.NeoPixel(board.IO38, 2, bpp=4, pixel_order=neopixel.GRBW)

mdata = []
lastGN = None
lastDT = None
lastRSSI = None
lastId = None
nextSendTime = 0
nextStatusTime = 0

messagesById = {}
messagesByTileMsgId = {}


def logTCP(s, newline=True):
  if tcpconn != None:
    try:
      if (lastDT is not None):
        tcpconn.send(getISOString(lastDT) + " ")
      tcpconn.send(s)
      if newline:
        tcpconn.send('\n')
    except:
      pass

def log(s, newline=True):
  displayLine(1, s)
  logTCP(s, newline)

def displayInit():
  displayio.release_displays()
  display_bus = displayio.I2CDisplay(i2c, device_address=0x3C)

  WIDTH = 128
  HEIGHT = 64
  BORDER = 0

  display = adafruit_displayio_sh1107.SH1107(display_bus, width=WIDTH, height=HEIGHT)

  # SWARM LOGO
  splash = displayio.Group(max_size=10)
  splash.y = 16
  display.show(splash)
  color_palette = displayio.Palette(1)
  color_palette[0] = 0xFFFFFF

  image_file = open("swarm.bmp", "rb")
  image = displayio.OnDiskBitmap(image_file)
  image_sprite = displayio.TileGrid(image, pixel_shader=image.pixel_shader)
  splash.append(image_sprite)
  time.sleep(1)
  splash.pop()

  STRING = "Swarm Message Sender"
  text_area2 = label.Label( terminalio.FONT, text=STRING, scale=1, color=0xFFFFFF, x=0, y=3)
  splash.append(text_area2)
  time.sleep(1)
  splash.pop()

  # SWARM LOGO

  splash = displayio.Group(max_size=10)
  display.show(splash)

  color_bitmap = displayio.Bitmap(WIDTH, HEIGHT, 1)
  color_palette = displayio.Palette(1)
  color_palette[0] = 0xFFFFFF  # White

  bg_sprite = displayio.TileGrid(color_bitmap, pixel_shader=color_palette, x=0, y=0)
  splash.append(bg_sprite)

  inner_bitmap = displayio.Bitmap(WIDTH - BORDER * 2, HEIGHT - BORDER * 2, 1)
  inner_palette = displayio.Palette(1)
  inner_palette[0] = 0x000000  # Black
  inner_sprite = displayio.TileGrid(inner_bitmap, pixel_shader=inner_palette, x=BORDER, y=BORDER)
  splash.append(inner_sprite)

  LINEHEIGHT = 11
  LINESTART = 4
  for line in range(0, 6):
    text_area = label.Label(terminalio.FONT, text=20*" ", color=0xFFFFFF, x=0, y=LINESTART + line*LINEHEIGHT)
    displayLines.append(text_area)
    splash.append(text_area)


def displayLine(line, text):
  displayLines[line].text = text


def appendChecksum(bytes):
  cs = 0
  for c in bytes[1:]:
    cs = cs ^ c
  return bytes + b'*%02X\n'%cs

def makeTileCmd(cmd):
  cbytes = cmd.encode()
  cs = 0
  for c in cbytes[1:]:
    cs = cs ^ c
  return cbytes + b'*%02X\n'%cs


def wifiInit():
  if config['wifi'] == 'disabled':
    displayLine(0, "Wifi Disabled")
    return
  global pool, TCPHOST
  try:
    if config['mode'] == 'sta':
      displayLine(0, "Connecting to wifi...")
      wifi.radio.connect(config["ssid"], config["password"])
      print("Self IP", wifi.radio.ipv4_address)
      displayLine(0, "IP: " + str(wifi.radio.ipv4_address))
      pool = socketpool.SocketPool(wifi.radio)
      TCPHOST = str(wifi.radio.ipv4_address)
    else:
      displayLine(0, "Starting AP...")
      if(config['ssid'] == 'swarm'): config['ssid'] = 'swarm-' + '%02x%02x'%(wifi.radio.mac_address[4], wifi.radio.mac_address[5])
      wifi.radio.start_ap(config["ssid"], config["password"])
      displayLine(0, "AP: " + str(wifi.radio.ipv4_address_ap))
      TCPHOST = str(wifi.radio.ipv4_address_ap)
      pool = socketpool.SocketPool(wifi.radio)
  except:
    displayLine(0, "wifi failed")


def tileCheck(line):
  global tileTimeout
  if  tileStateTable[tileState][1] in line:
    tileTimeout = -1.0


def tileStart():
  global tileState, tileTimeout
  displayLine(0, "Connecting to modem...")
  tileState = TILE_STATE_UNKNOWN
  while tileState != TILE_STATE_CONFIGURED:
    tile.write(b'\n' + makeTileCmd(tileStateTable[tileState][0]))
    tileTimeout = time.monotonic() + tileStateTable[tileState][2]
    while (tileTimeout > 0.0) and (tileTimeout > time.monotonic()):
      tilePoll()
      w.feed()
    if tileTimeout  < 0.0:
      tileState = tileStateTable[tileState][3]
    else:
      tileState = tileStateTable[tileState][4]


def tileInit():
  global tile
  tile = busio.UART(board.TX,board.RX,baudrate=115200,receiver_buffer_size=8192,timeout=0.0)
  tileStart()


def tileParseLine(line):
  global lastDT, lastGN, lastRSSI
  if len(line) < 4:
    return
  if line[len(line) - 3] != '*':
    return
  cksum1 = 0
  cksum2 = int(line[-2:], 16)
  for c in line[1:-3]:
    cksum1 = cksum1 ^ ord(c)
  if cksum1 != cksum2:
    return
  if tileState != TILE_STATE_CONFIGURED:
    tileCheck(line)
    return
  if line[0:3] == "$TD":
    if len(mdata) > 10:
      mdata.pop(0)
    mdata.append(line)
    if line.startswith("$TD OK"):
      messageAccepted(line)
    if line.startswith("$TD SENT"):
      messageSent(line)
  if line[0:3] == "$DT":
    if line == "$DT OK*34":
      lastDT = None
    else:
      lastDT = line[4:-3]
  if line[0:3] == "$GN":
    if line == "$GN OK*2d":
        lastGN = None
    else:
        lastGN = line
  parse = line[:-3].split(' ')
  if parse[0] == '$RT':
    packetReceived(line)
    if 'RSSI' in parse[1]:
      if ',' in parse[1]:
        rdata = line[4:-3].split(',')
        rtdata = []
        for r in rdata:
          rtdata.append(r.split('='))
        rtdata = dict(rtdata)
        if 'T' in rtdata['TS']:
            d, t = rtdata['TS'].split('T')
        else:
            d, t = rtdata['TS'].split(' ')
        d = d.split('-')
        t = t.split(':')
        dtString = d[0][2:]+d[1]+d[2]+'T'+t[0]+t[1]+t[2]
        print(rtdata)
        displayLine(4, dtString + ' S' + rtdata['DI'][2:])
        displayLine(5, 'R:' + rtdata['RSSI'] + ' S:' + rtdata['SNR'] + ' F:' + rtdata['FDEV'])
      else:
        rssi = parse[1].split('=')
        #displayLine(2, "RSSI: " + rssi[1])
        irssi = int(rssi[1])
        lastRSSI = irssi
        if config['wifi'] == 'enabled':
          if irssi > -91:
            pixels[0] = (16, 0, 0, 0)
          elif irssi < -95:
            pixels[0] = (0, 16, 0, 0)
          else:
            pixels[0] = (16, 16, 0, 0)
          pixels.write()
  if parse[0] == '$MT':
    logTCP(f'unsent messages: {parse[1]}')

def tilePoll():
  global tilePtr
  chars = tile.read(20)
  if chars == None:
    return
  for c in chars:
    if c == 0x0A:
      s = tileLine[:tilePtr].decode()
      shouldLog = True
      for f in tileMessageFilters:
        if s.startswith(f):
          shouldLog = False
      if shouldLog:
        logTCP(s)
      tileParseLine(s)
      tilePtr = 0
    elif c == 0x08 and tilePtr != 0:
      tilePtr = tilePtr - 1
    elif c >= 0x20 and c <= 0x7f and tilePtr < len(tileLine):
      tileLine[tilePtr] = c
      tilePtr = tilePtr + 1
  pass

def inaInit():
  global ina3221, inaConnected, inaData
  try:
    ina3221 = INA3221(i2c, shunt_resistor = (0.01, 0.01, 0.01))
    ina3221.update(reg=C_REG_CONFIG, mask=C_AVERAGING_MASK | C_VBUS_CONV_TIME_MASK | C_SHUNT_CONV_TIME_MASK | C_MODE_MASK,
                                     value=C_AVERAGING_128_SAMPLES | C_VBUS_CONV_TIME_8MS | C_SHUNT_CONV_TIME_8MS | C_MODE_SHUNT_AND_BUS_CONTINOUS)
    ina3221.enable_channel(1)
    ina3221.enable_channel(2)
    ina3221.enable_channel(3)
    inaConnected = True
    # initialize all the values
    for channel in range(1, 4):
      bus_voltage = ina3221.bus_voltage(channel)
      current = ina3221.current(channel)
      inaData[channel] = (bus_voltage, current)
  except:
    displayLine(1, "ina disconnected")
    inaConnected = False


def inaPoll():
  global inaChannel, inaTime, inaConnected, inaData
  if not inaConnected:
    inaInit()
    return
  if time.time() - inaTime > 5:
    try:
      inaChans = {1:'BAT:', 2:'SOL:', 3:'3V3:'}
      bus_voltage = ina3221.bus_voltage(inaChannel)
      current = ina3221.current(inaChannel)

      displayLine(1, "%s %6.3fV %6.3fA"%(inaChans[inaChannel], bus_voltage, current))
      inaData[inaChannel] = (bus_voltage, current)
      inaChannel = inaChannel + 1
      if inaChannel == 4:
        inaChannel = 1
    except:
      inaConnected = False
    inaTime = time.time()


def tcpInit():
  if config['wifi'] == 'disabled':
    return
  if wifi.radio.ipv4_address_ap is None and wifi.radio.ipv4_address is None:
    return
  global TCPSTATE, tcplistener, tcpconn
  print("Create TCP Server socket", (TCPHOST, TCPPORT))
  tcplistener = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
  tcplistener.settimeout(TIMEOUT)
  tcplistener.setblocking(False)
  tcplistener.bind((TCPHOST, TCPPORT))
  tcplistener.listen(BACKLOG)
  print("Listening")


def tcpPoll():
  if config['wifi'] == 'disabled' or (wifi.radio.ipv4_address_ap is None and wifi.radio.ipv4_address is None):
    displayLine(4, "tcpPoll")
    return
  global TCPSTATE, tcplistener, tcpconn, tcpPtr
  if TCPSTATE == TCPSTATE_LISTENING:
    try:
      tcpconn, addr = tcplistener.accept()
      tcpconn.settimeout(0)
      print("Accepted from", addr)
      TCPSTATE = TCPSTATE_CONNECTED
    except:
      pass
  elif TCPSTATE == TCPSTATE_CONNECTED:
    buf = bytearray(MAXBUF)
    try:
      size = tcpconn.recv_into(buf, MAXBUF)
      if size == 0:
        tcpconn.close()
        tcpconn = None
        print("Accepting connections")
        TCPSTATE = TCPSTATE_LISTENING
      else:
        print("Received", buf[:size], size, "bytes")
        for i in range(size):
          if buf[i] == 0x0A:
            if tcpLine[0] == 0x40:
              command = tcpLine[:tcpPtr].decode()
              params = command.split(' ')
              if params[0] == '@reset':
                tcpconn.send("Resetting...")
                microcontroller.reset()
              elif params[0] == '@color':
                if len(params) ==  5:
                  if config['wifi'] == 'enabled':
                    pixels[1] = (int(params[1]),int(params[2]),int(params[3]),int(params[4]))
                    pixels.write()
              elif params[0] == '@set':
                if params[1] == 'mode':
                  if params[2] in ['ap', 'sta']:
                    config['mode'] = params[2]
                    tcpconn.send(f"Successfully set mode to {params[2]}.")
                    writePreferences()
                if params[1] == 'wifi':
                  if params[2] in ['enabled', 'disabled']:
                    config['wifi'] = params[2]
                    if config['wifi'] == 'disabled':
                      pixels[0] = (0,0,0,0)
                      pixels[1] = (0,0,0,0)
                      pixels.write()
                    tcpconn.send(f"Successfully {params[2]} wifi.")
                    tcpconn.send("Resetting...")
                    microcontroller.reset()
                    writePreferences()
                if params[1] == 'ssid':
                  config['ssid'] = command[10:].strip()
                  tcpconn.send(f"Successfully set ssid to {config['ssid']}.")
                  writePreferences()
                if params[1] == 'pw':
                  config['password'] = command[8:].strip()
                  tcpconn.send(f"Successfully set password to {config['password']}.")
                  writePreferences()
                if params[1] == 'interval':
                  if int(params[2]) == 0 or (int(params[2]) >= 15 and int(params[2]) <= 720):
                    if int(params[2]) == 0 and config['interval'] > 0:
                      config['interval'] = config['interval'] * -1
                      tcpconn.send(f"Successfully set interval to off.")
                    else:
                      config['interval'] = int(params[2])
                      tcpconn.send(f"Successfully set interval to {config['interval']}.")
                    writePreferences()
                  else:
                    tcpconn.send("Interval can only be 0 or 15-720 minutes.")
                if params[1] == 'broker':
                  config['broker'] = command[12:].strip()
                  tcpconn.send(f"Successfully set broker to {config['broker']}.")
                  writePreferences()
              elif params[0] == '@show':
                if len(params) == 2:
                  if params[1] == 'battery':
                    tcpconn.send('BAT: ' + str(inaData[1][0]) + 'V ' + str(inaData[1][1]) + 'A')
                  if params[1] == '3v3':
                    tcpconn.send('3V3: ' + str(inaData[3][0]) + 'V ' + str(inaData[3][1]) + 'A')
                  if params[1] == 'solar':
                    tcpconn.send('SOL: ' + str(inaData[2][0]) + 'V ' + str(inaData[2][1]) + 'A')
                else:
                  tcpconn.send('wifi mode:' + config['mode'] + '\n')
                  tcpconn.send('wifi:' + config['wifi'] + '\n')
                  tcpconn.send('wifi ssid:' + config['ssid'] + '\n')
                  tcpconn.send('wifi pw:  ' + config['password'] + '\n')
                  tcpconn.send('gps interval: ' + (str(config['interval']), 'OFF')[config['interval'] <= 0] + '\n')
                  if 'broker' in config:
                      tcpconn.send('broker: ' + config['broker'] + '\n')
              elif params[0] == '@factory':
                microcontroller.nvm[0] = 0
                tcpconn.send("Cleared NVM and Resetting...")
                microcontroller.reset()
              else:
                tcpconn.send("Invalid command. Type @help for help.")
              print("", end='')
            tile.write(tcpLine[:tcpPtr])
            tile.write(bytearray([0x0a]))
            tcpPtr = 0
          elif buf[i] == 0x08 and tcpPtr != 0:
            tcpPtr = tcpPtr - 1
          elif buf[i] >= 0x20 and buf[i] <= 0x7f and tcpPtr < len(tcpLine):
            tcpLine[tcpPtr] = buf[i]
            tcpPtr = tcpPtr + 1
    except Exception as e:
      pass

def getISOString(dt):
  # input timestamp in form 20210408195123
  # output is string in form 2021-04-08T19:51:23
  if dt is not None:
    return dt[0:4] + '-' + dt[4:6] + '-' + dt[6:8] + 'T' + dt[8:10] + ':' + dt[10:12] + ':' + dt[12:14]
  else:
    return ""

def getDateTime(dt):
  # input ISO string in form 2021-04-08T19:51:23
  # output is UNIX timestamp
  if dt is not None:
    t = 946684800 + int(time.mktime((int(dt[0:4]), int(dt[5:7]), int(dt[8:10]), int(dt[11:13]), int(dt[14:16]), int(dt[17:]), -1, -1, -1)))
    return round(t)
  else:
    return 0


def writePreferences():
  configString = json.dumps(config)
  ba = bytearray(configString, 'utf-8')
  microcontroller.nvm[0:len(ba)] = ba
  microcontroller.nvm[len(ba)] = 0


def readPreferences():
  global config
  try:
    x = microcontroller.nvm[0]
  except:
    microcontroller.nvm[0] = 0
  i = 0
  configString = ""
  while microcontroller.nvm[i] is not 0:
    configString += chr(microcontroller.nvm[i])
    i = i + 1
  if configString == "":
    configString = "{}"
  config = json.loads(configString)
  if not 'mode' in config:
    config['mode'] = 'ap'
  if not 'ssid' in config:
    config['ssid'] = 'swarm'
  if not 'password' in config:
    config['password'] = '12345678'
  if not 'interval' in config:
    config['interval'] = 60
  if not 'wifi' in config:
    config['wifi'] = "enabled"
# Add this back in if you want to automatically connect to a broker
  if not 'broker' in config:
    config['broker'] = "nootropicdesign.com"


def watchDogInit():
  w.timeout = 60
  w.mode = WatchDogMode.RESET
  w.feed()


def buttonInit():
  global switchA, switchC

  pinA = digitalio.DigitalInOut(board.D5)
  pinA.direction = digitalio.Direction.INPUT
  pinA.pull = digitalio.Pull.UP
  switchA = Debouncer(pinA)

  pinC = digitalio.DigitalInOut(board.D20)
  pinC.direction = digitalio.Direction.INPUT
  pinC.pull = digitalio.Pull.UP
  switchC = Debouncer(pinC)



def buttonPoll():
  switchA.update()
  if switchA.rose: # just released
    if config['wifi'] == "enabled":
      config['wifi'] = "disabled"
      pixels[0] = (0,0,0,0)
      pixels[1] = (0,0,0,0)
      pixels.write()
    else:
      config['wifi'] = "enabled"
    writePreferences()
    print(f"Successfully {config['wifi']} wifi.")
    print("Resetting...")
    microcontroller.reset()


def factoryResetCheck():
  switchA.update()
  if not switchA.value:
    microcontroller.nvm[0] = 0
    while not switchA.value:
      switchA.update()
    print("Cleared NVM and Resetting...")
    microcontroller.reset()


def messageAccepted(line):
  global messagesById, messagesByTileMsgId
  msg_id = line[line.index(',')+1:line.index('*')]
  time_tx = getISOString(lastDT)
  if lastId is None:
    return
  id = lastId
  messagesById[id] = {
    "tile_msg_id": msg_id,
    "time_tx": time_tx,
  }
  # maintain a mapping between tile message IDs and the main index id
  messagesByTileMsgId[msg_id] = id
  saveMessages()

def messageSent(line):
  global messagesById
  parts = line[9:-3].split(',')
  rssi = parts[0].split('=')[1]
  snr = parts[1].split('=')[1]
  fdev = parts[2].split('=')[1]
  msg_id = parts[3]
  if (msg_id not in messagesByTileMsgId):
    logTCP(f'messageSent: message {msg_id} not known')
    return
  id = messagesByTileMsgId[msg_id]
  time_rx_sat = getISOString(lastDT)
  messagesById[id]["time_rx_sat"] = time_rx_sat
  saveMessages()
  saveStats(id)

def packetReceived(line):
  parts = line[4:-3].split(',')
  if (len(parts) < 5):
    return
  fields = []
  for p in parts:
      fields.append(p.split('='))
  fields = dict(fields)
  if 'T' in fields['TS']:
      d, t = fields['TS'].split('T')
  else:
      d, t = fields['TS'].split(' ')
  d = d.split('-')
  t = t.split(':')

def getRandomId():
  s = str(random.getrandbits(32))
  while len(s) < 10:
    s = '0' + s
  return s

def sendMessage():
  global lastId
  log("Sending message...")
  id = getRandomId()
  payload = getISOString(lastDT)
  appId = 123
  satMessage = {
    "id": id,
    "payload": payload
  }
  satMessageJSON = json.dumps(satMessage)
  satMessageJSON = satMessageJSON.replace(' ', '')
  s = ('$TD AI=' + str(appId) + ',').encode() + hexlify(satMessageJSON.encode())
  s = appendChecksum(s)
  lastId = id
  tile.write(s)

def requestNumberUnsent():
  s = ('$MT C=U').encode()
  s = appendChecksum(s)
  tile.write(s)

def loadMessages():
  global messagesById, messagesByTileMsgId
  try:
    with open("/messages.json", "r") as f:
      messagesById = json.load(f)
  except OSError as e:
    pass

  for id in messagesById.keys():
    message = messagesById[id]
    messagesByTileMsgId[message["tile_msg_id"]] = id;

def saveMessages():
  try:
    with open("/messages.json", "w") as f:
      json.dump(messagesById, f)
      f.flush()
  except OSError as e:  # Typically when the filesystem isn't writeable...
    pass

def saveStats(id):
  stats = f'{id},{messagesById[id]["time_tx"]},'
  time_tx = getDateTime(messagesById[id]['time_tx'])
  time_rx_sat = getDateTime(messagesById[id]['time_rx_sat'])
  stats += f'{str(time_rx_sat-time_tx)}'
  try:
    with open("/stats.csv", "a") as f:
      logTCP(stats)
      print(stats, file=f)
      f.flush()
  except OSError as e:  # Typically when the filesystem isn't writeable...
    pass


def sendPoll():
  global nextSendTime
  if (lastDT is not None) and (time.time() > nextSendTime):
    nextSendTime = (15 * 60) + time.time()
    sendMessage()

def statusPoll():
  global nextStatusTime
  if (lastDT is not None) and (time.time() > nextStatusTime):
    nextStatusTime = (60) + time.time()
    requestNumberUnsent()


### BEGIN
watchDogInit()
buttonInit()
factoryResetCheck()
i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
displayInit()
readPreferences()
if config['wifi'] == 'enabled':
  import wifi
  import socketpool
  import ipaddress

tileInit()
wifiInit()
tcpInit()
loadMessages()

try:
  while True:
    tilePoll()
    inaPoll()
    tcpPoll()
    buttonPoll()
    sendPoll()
    statusPoll()
    w.feed()
    gc.collect()
except Exception as e:
  print(e)
  print("Resetting...")
  microcontroller.reset()


