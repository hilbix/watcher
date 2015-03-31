#!/usr/bin/env python2
#
# ./watcher.py -|file|sock...
#
# This Works is placed under the terms of the Copyright Less License,
# see file COPYRIGHT.CLL.  USE AT OWN RISK, ABSOLUTELY NO WARRANTY.

from __future__ import print_function

import sys
import os
import fcntl
import stat
import time
import socket
import warnings
import errno
import curses

BUFSIZ = 4096
MAX_HIST = 10000

try:
	debugout = os.fdopen(3, "w")
except:
	debugout = None

def debug(*o):
	if debugout: print("[[DEBUG: ", *(o+("]]\r",)), file=debugout)

def getuser():
	try:
		return os.getlogin()
	except Exception:
		pass
	# WTF? -> never though this can happen!
	for e in ['USERNAME','LOGNAME','USER']:
		try:
			return os.environ[e]
		except Exception:
			pass
	# WTF!
	return '(unknown user)'

def nonblocking(fd):
	flags = fcntl.fcntl(fd, fcntl.F_GETFL)
	fcntl.fcntl(fd, fcntl.F_SETFL, flags|os.O_NONBLOCK)

class WatchPipe():

	def __init__(self, fd, name):
		self.name = name;
		self.fd = fd
		if fd >= 0:
			nonblocking(fd)

	def check(self):
		return self.fd >= 0

	def read(self):
		if self.fd<0:	return None

		try:
			data = os.read(self.fd, BUFSIZ)
		except OSError:
			return None

		if data:
			return data

		os.close(self.fd)
		self.fd = -1;
#		self.remove.remove(self)
		return None

class WatchFile():

	def __init__(self, name):
		self.name = name
		self.init = True
		self.sock = None
		self.pos = 0
		self.fd = -1

	def close(self):
		if self.fd<0: return
		if self.sock:
			self.sock.close()
			self.sock = None
		else:
			os.close(self.fd)
		self.fd = -1

	def open_socket(self):
		self.sock = socket.socket(socket.AF_UNIX)
		self.sock.connect(self.name)
		self.fd = self.sock.fileno()
		nonblocking(self.fd)

	def open_file(self):
		self.fd = os.open(self.name, os.O_RDONLY|os.O_NONBLOCK)
		if self.init:
			self.init = False
			self.pos = os.lseek(self.fd, 0, os.SEEK_END)
			self.pos = os.lseek(self.fd, max(0, self.pos-MAX_HIST), os.SEEK_SET)

	def open(self):
		if self.fd >= 0:	return True
		try:
			# Yes really catch anything here
			# self.pos may be a failing setter
			self.pos = 0
			self.stat = os.stat(self.name)
			if stat.S_ISSOCK(self.stat.st_mode):
				self.open_socket()
			else:
				self.open_file()
			return True
		except Exception:
			self.init = False
			self.close()
		return False

	def reopen(self):
		self.close()
		return self.open()

	def check(self):
		if self.fd >= 0 and self.sock == None:
			try:
				st = os.stat(self.name)
				if st.st_ino != self.stat.st_ino:
					self.close()
			except Exception:
				self.close()
		if self.fd<0:
			return self.open()
		return True

	def read(self):
		if self.fd<0: return None

		if self.sock:
			data = []
			eof = False
			try:
				for i in range(1,20):
					d = self.sock.recv(BUFSIZ)
					if not d:	eof = True
					data.append(d)
			except socket.error as (e, s):
				if e != errno.EAGAIN:
					raise
			data = ''.join(data)
			if not data:
				if eof:	self.close()
				return None
			self.pos += len(data)
			return data

		try:
			st = os.fstat(self.fd)
		except OSError:
			self.close()
			return None

		if st.st_size < self.pos:
			# File has shrunken
			if not self.reopen():
				return None

		if st.st_size == self.pos:
			return None

		try:
			data = os.read(self.fd, BUFSIZ)
		except IOError:
			return None
		self.pos += len(data)
		return data

class FileOb:
	def __init__(self, file):
		self.file = file
		self.win = None
		self.hist = ""

class Watcher():

	# Speed of GUI, do reads each WAIT_TENTHS/10 seconds
	WAIT_TENTHS = 3
	# check inode change each WAIT_TENTHS * RECHECK_COUNT / 10 seconds
	RECHECK_COUNT = 10

	scr = None
	files = []
	open = {}
	windows = 0
	jump = True
	columns = 2

	gridcolor = 3
	cursorcolor = 1
	warncolor = 5

	C_BORDER = 8
	C_WARN = 9
	C_RED = 10
	C_CURSOR = 11

	edit_mode = False
	edit_win = 0
	edit_last = 0

	def __init__(self):
		self.count = 0;
		debug("init")

	def add(self, file):
		self.files.append(FileOb(file))

	def check_files(self):
		for a in self.files:
			if a.file.check():
				self.scr.addstr(a.ty, a.tx, 'ok ')
				self.scr.chgat(a.ty, a.tx, a.w, curses.color_pair(self.C_BORDER))
			else:
				self.scr.addstr(a.ty, a.tx, '** ')
				self.scr.chgat(a.ty, a.tx, a.w, curses.color_pair(self.C_RED))

	def win_title(self, a, marked=False):
		s = "   " + a.file.name
		w = a.w
		self.scr.addstr(a.ty, a.tx, s[-w:] + " "*max(0, w-len(s)))
		att = curses.color_pair(self.C_BORDER)
		if marked:
			att = curses.color_pair(self.C_RED)
		self.scr.chgat(a.ty, a.tx, w, att)

	def new_win(self, a, last=False):
		p = int(self.windows / self.tiles)
		n = self.windows % self.tiles

		self.window[self.windows] = a
		self.windows += 1

		y = 2+p*(self.height+1)
		x = n*(self.width+1)

		h,w = self.scr.getmaxyx()
		assert h>3 and w>3

		debug("new_win", self.tiles, x,y,w,h)

		h -= y
		w -= x
		if w >= self.width*2 and self.windows<len(self.files):
			w = self.width
		if p+1<int(len(self.files)/self.tiles):
			h = self.height

		debug("new_win", n, x,y,w,h)
		assert h>0 and w>0 and y>=0 and x>=0
		win = self.scr.subwin(h, w, y, x)
		self.defaults(win)

		if n+1 < self.tiles and self.windows<len(self.files):
			self.scr.attron(curses.color_pair(self.C_BORDER))
			self.scr.vline(y-1, x+w, 32, h+1)
			self.scr.attroff(curses.color_pair(self.C_BORDER))
		a.tx = x
		a.ty = y-1
		a.w = w
		a.h = h

		a.win = win
		a.nl = False
		a.x = 0
		a.y = 0
		a.warn = curses.A_NORMAL

		self.win_title(a)

		if a.hist:
			self.update(a, a.hist)

	def scroll(self, a):
		if self.jump:
			a.win.move(0, 0)
		else:
			a.win.scrollok(1)
			a.win.scroll()
			a.win.scrollok(0)
			a.win.move(a.win.getyx()[0], 0)

	def read_files(self):
		self.count += 1
		if self.count > self.RECHECK_COUNT:
			self.count = 0
			self.check_files()
		for a in self.files:
			data = a.file.read()
			if not data: continue
			self.update(a, data)
			if len(data) >= MAX_HIST:
				a.hist = data[-MAX_HIST:]
			else:
				a.hist = a.hist[max(-len(a.hist), len(data)-MAX_HIST):] + data

	def update(self, a, data):
		win = a.win
		win.chgat(a.y, 0, a.warn)
		win.move(a.y, a.x)
		y = a.y
		for c in data:
			c = ord(c)
			if c == 13:
				win.move(win.getyx()[0], 0)
				continue
			if c == 10:
				a.nl = True
				continue
			if a.nl:
				try:
					win.move(win.getyx()[0]+1, 0)
					win.clrtoeol()
				except Exception:
					self.scroll(a)
				a.nl = False
				a.warn = curses.A_NORMAL
			if c == 7:
				a.warn = curses.color_pair(self.C_WARN)
				y,x = win.getyx()
				win.chgat(y, 0, a.warn)
				win.move(y, x)
				continue
			try:
				win.addch(c, a.warn)
			except Exception:
				self.scroll(a)
				win.addch(c, a.warn)

			a.y,a.x = win.getyx()
			if a.y != y:
				# For unknown reason, we need to move to the current point
				# to get clrtoeol() to work on the last line of the window, too.
				# Seems to be a curses bug.
				win.move(a.y,a.x)
				win.clrtoeol()
				y = a.y
				
		a.y,a.x = win.getyx()
		if a.nl:
			if a.warn != curses.A_NORMAL:
				win.chgat(a.y, a.x, a.warn|curses.A_UNDERLINE)
			else:
				win.chgat(a.y, a.x, curses.color_pair(self.C_CURSOR)|curses.A_UNDERLINE)
		else:
			if self.jump:
				win.chgat(a.y, 0, a.warn|curses.A_REVERSE)
			win.chgat(a.y, a.x, 1, curses.color_pair(self.C_CURSOR))

		win.noutrefresh()

	def contrast(self, color):
		if color == curses.COLOR_BLUE or color == curses.COLOR_RED:
			return curses.COLOR_WHITE
		if color == curses.COLOR_BLACK:
			return curses.COLOR_CYAN
		return curses.COLOR_BLACK

	def contrastRed(self, color):
		if color == curses.COLOR_MAGENTA:
			return curses.COLOR_BLUE
		if color == curses.COLOR_RED:
			return curses.COLOR_BLACK
		return curses.COLOR_RED

	def color(self, value):
		cols = [curses.COLOR_BLUE
			, curses.COLOR_GREEN
			, curses.COLOR_YELLOW
			, curses.COLOR_CYAN
			, curses.COLOR_MAGENTA
			, curses.COLOR_RED
			, curses.COLOR_WHITE
			, curses.COLOR_BLACK]
		return cols[value%len(cols)]

	def setSingleColor(self, color, value):
		bg = self.color(value)
		curses.init_pair(color, self.contrast(bg), bg)

	def setColor(self):
		self.setSingleColor(self.C_BORDER, self.gridcolor)
		self.setSingleColor(self.C_CURSOR, self.cursorcolor)
		self.setSingleColor(self.C_WARN,   self.warncolor)

		fg = self.color(self.gridcolor)
		curses.init_pair(self.C_RED, self.contrastRed(fg), fg)

	def defaults(self, win):
		win.idcok(1)
		win.idlok(1)
		win.leaveok(0)
		win.keypad(1)

	def layout_imp(self, columns):
		if columns != None:
			if self.columns == columns: return
			self.columns = columns

		self.scr.clear()
		self.defaults(self.scr)
		self.scr.redrawwin()
		self.setColor()

		h,w = self.scr.getmaxyx()
		assert h>3 and w>3

		n = max(1, len(self.files))
		d = int(n*2/h)+1

		columns = self.columns
		if columns > w/3: columns = int(w/3)
		if columns < d: columns = d
		self.tiles = columns

		n = max(1, len(self.files))
		d = int((w-columns+1) / columns)
		n = int((n+columns-1) / columns)

		self.width = d
		self.height = int((h-1-n)/n)
		debug("layout_imp", columns, len(self.files), self.width, self.height, n)

		self.window = [None for ignored in range(0, n*d)]

		self.windows = 0
		for a in self.files:
			self.new_win(a, (self.windows == len(self.files)-1))

		if not self.files:
			self.out(1, 0, "Empty commandline: missing files, unix sockets or '-' for stdin.  Press q to quit.")

	def layout(self, columns=None):
		debug("layout", columns)
		self.layout_imp(columns)
		return "(%dx%d)" % (self.height, self.width)

	def charcode(self, c):
		s = ""
		if c == 27: s = "ESC"
		if c == 32: s = "SPC"
		if c > 32 and c < 127: s = "%c" % c
		for k,v in curses.__dict__.iteritems():
			if k.startswith("KEY_") and v == c:
				s = k[4:]
				break
		if s == '[' or s == ']':
			return "%s(%d)" % (s,c);
		return "%s[%d]" % (s,c)

	def out(self, y, x, text, win=None):
		if win == None:
			win = self.scr

		h,w = win.getmaxyx()

		if x < 0: x = w+x - len(text) + 1
		if x < 0: x = 0

		if y < 0: y = h + y
		if y < 0: y = 0

		if x+1 >= w: return	# cannot print anything, out of screen
		if y >= h: return # ditto

		win.addnstr(y, x, text, w-x)
		if x+len(text)<w:
			win.clrtoeol()

	def edit_off(self):
		if self.edit_last >= 0:
			self.win_title(self.window[self.edit_last])
		self.edit_last = -1

	def edit_on(self):
		if self.edit_win == self.edit_last:
			return
		self.edit_off()
		self.win_title(self.window[self.edit_win])
		self.edit_last = self.edit_win

	def edit(self):
		if not self.edit_mode:
			self.edit_off()
			return
		self.edit_on()

	def run(self, scr):
		debug("run")
		self.scr = scr

		curses.nonl()
		curses.halfdelay(self.WAIT_TENTHS)
		try:
			curses.curs_set(0)
		except Exception:
			pass

		self.layout()
		self.check_files()

		host = socket.getfqdn()
		user = getuser()

		c = 0
		cwd = "CWD " + os.getcwd()
		s = None
		ticks = 0
		haveMsg = False
		last = None
		loop = True
		c0 = None
		c1 = None
		fast = False
		while loop:
			c2 = c1
			c1 = c0
			c0 = None
			if s:
				ticks = 20

			if ticks == 0:
				s = cwd
				ticks = -1

			now = int(time.time())
			if now != last:
				ticks -= 1
				self.out(0, -1, " (%s@%s) %s" % (user, host, time.strftime("%a %Y-%m-%d %H:%M:%S %Z")))
				if last:
					if now<last:
						s = "Time went backwards"
						ticks = -1
					elif now>last+10:
						s = "Time jump"
						ticks = -1
				last = now

			if s:
				scr.move(0, 0)
				if c0 or c1 or c2:
					s = (c0 or c1 or c2) + " " + s
				self.out(0, 0, s)
				s = None

			scr.move(0, 0)
			scr.refresh()

			c = scr.getch()
			if c<0:
				self.read_files()
				continue

			c0 = self.charcode(c)
			if fast and (c1 or c2):
				c0 = (c1 or c2)+c0
				c1 = None
				c2 = None
				s = "typed too fast"
				continue

			fast = False

			if c == curses.KEY_RESIZE:
				s = "Resized " + self.layout()

			if False and c == 9:
				self.edit_mode = not self.edit_mode
				self.edit()
				s = "Edit mode"
				if not self.edit_mode:
					s = s + " off"

			if c == 12:
				s = "Redraw " + self.layout()
				fast = True

			if c == 99:	# c
				self.cursorcolor += 1
				self.setColor()
				s = "Cursor color"

			if c == 67:	# C
				self.cursorcolor -= 1
				self.setColor()
				s = "Cursor color"

			if c == 103:	# g
				self.gridcolor += 1
				self.setColor()
				s = "Grid color"

			if c == 71:	# G
				self.gridcolor -= 1
				self.setColor()
				s = "Grid color"

			if c == 119:	# w
				self.warncolor -= 1
				self.setColor()
				s = "Warn color"

			if c == 87:	# W
				self.warncolor += 1
				self.setColor()
				s = "Warn color"

			if c >= 48 and c < 58:	# 0-9
				s = "Layout " + self.layout((c-48) or 10)

			if c == 113:
				s = "Quit"
				loop = False

			if c == 106:	#j
				self.jump = True
				s = "Jump mode"

			if c == 115:	#s
				self.jump = False
				s = "Scroll mode"

			if s == None:
				s = "Help: Quit Warn/Grid/Color Jump/Scroll 0-9"
				fast = True

		scr.refresh()

	def main(self):
		debug("main")
		curses.wrapper(lambda scr:self.run(scr))

def move_terminal_to_fd(fd, tty):
	if not os.isatty(tty):	raise Exception("fd %d not a TTY" % tty)
	if os.isatty(fd):
		# Well, we have a tty there, maybe that it is a different PTY?
		if os.fstat(fd).st_ino == os.fstat(tty).st_ino:
			# Well, this did not work
			# return error
			return -1
		# fd and tty refer to different TTYs here

	ret = os.dup(fd)
	os.dup2(tty, fd)
	return ret

if __name__ == "__main__":
	w = Watcher()
	for a in sys.argv[1:]:
		if a == "-":
			w.add(WatchPipe(move_terminal_to_fd(0, 2), "-stdin-"))
		else:
			w.add(WatchFile(a))
	w.main()

