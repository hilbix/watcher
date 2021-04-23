#!/usr/bin/env python3
#
# ./watcher.py -|file|sock...
#
# This Works is placed under the terms of the Copyright Less License,
# see file COPYRIGHT.CLL.  USE AT OWN RISK, ABSOLUTELY NO WARRANTY.

import sys
import os
import fcntl
import stat
import time
import socket
import warnings
import errno
import curses
import termios

BUFSIZ = 4096
MAX_HIST = 102400	# assumed max screen size: 80x24 / 4x3 * 16x10 * 2x2

try:
	debugout = os.fdopen(3, "w")
except:
	debugout = None

def debug(*o):
	if debugout: print("[[ DEBUG:", *(o+("]]\r",)), file=debugout)


# curses bullshit start
#
# curses is a module.
# So wrap everything we need into a proper class
# However, following is plain bullshit,
# as we only have classfunctions called with a class object instead.
# Perhaps in future this will be implemented cleanly, we will see.
# For now it just separates curses from the rest of the code

class CursesColor(object):
	cycle =	[ curses.COLOR_BLUE
		, curses.COLOR_GREEN
		, curses.COLOR_YELLOW
		, curses.COLOR_CYAN
		, curses.COLOR_MAGENTA
		, curses.COLOR_RED
		, curses.COLOR_WHITE
		, curses.COLOR_BLACK
		]

	def __init__(self, parent):
		self.curses	= parent

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

	def toCurses(self, color):
		return color+8

	def pair(self, color):
		return curses.color_pair(self.toCurses(color))


	def normal(self):
		return curses.A_NORMAL

	def underline(self, color):
		return curses.A_UNDERLINE | color

	def reverse(self, color):
		return curses.A_REVERSE | color

	def reverseIf(self, inv, color):
		return inv and (curses.A_REVERSE ^ color) or color

	def underlineIf(self, inv, color):
		return inv and (curses.A_UNDERLINE ^ color) or color

	def get(self, value):
		return self.cycle[value % len(self.cycle)]

	def set(self, color, value):
		bg = self.get(value)
		curses.init_pair(self.toCurses(color), self.contrast(bg), bg)

	def setRed(self, color, value):
		fg = self.get(value)
		curses.init_pair(self.toCurses(color), self.contrastRed(fg), fg)

class Curses(object):

	# I am not happy with this
	def __init__(self, runClass):
		"""duck typing: call runClass.run(scr) from curses"""
		self.color	= CursesColor(self)
		self.main	= runClass
		self.scr	= None
		curses.wrapper(lambda scr:self.run(scr))

	def run(self, scr):
		self.scr	= scr
		self.main.run(self)	# shall become .run(self) in future!

	def print(self, y, x, text, win=None):
		if win == None:
			win = self.scr

		h,w = win.getmaxyx()

		if x < 0: x = w+x - len(text) + 1
		if x < 0: x = 0

		if y < 0: y = h + y
		if y < 0: y = 0

		if x+1 >= w: return	# cannot print anything, out of screen
		if y >= h: return	# ditto

		win.addnstr(y, x, text, w-x)
		if x+len(text)<w:
			win.clrtoeol()

	def BREAK(self):
		return curses.KEY_BREAK

	def isResize(self, c):
		return c == curses.KEY_RESIZE

	# This is based on TERM=dumb for now
	def key_sequence(self, c, esc=0):
		o=[esc]
		def E(a,b):
			if o[0]:
				o[0]	-= 1
				return b
			return a
		if c>=256:
			if c==curses.KEY_BACKSPACE:	c = E(127,8)
			else:	return None	# not yet implemented
		return ("\e"*o[0])+chr(c)

	def saneMode(self):
		"""Do all the usual curses quirx stuff"""
		curses.nonl()
# Does not work.  How can I distinguish CR, LF, Return and Enter with Curses?
#		t = termios.tcgetattr(1)
#		t[0] = t[0] | termios.ICRNL
#		termios.tcsetattr(1, termios.TCSADRAIN, t)

	def showCursor(self, on):
		"""enable/disable hardware cursor"""
		try:
			curses.curs_set(on and 1 or 0)
		except Exception:
			pass

	def nodelay(self, nodelay=True):
		'''disable/enable waiting'''
		if nodelay or not self._timeout:
			curses.cbreak()
			self.scr.nodelay(1 if nodelay else 0)
		elif 0 < self._timeout < 255:
			curses.halfdelay(self._timeout)
		else:
			curses.cbreak()

	def timeout(self, tenth):
		'''
		set timeout for getch() when nodelay(False)
		0:	permanently enable .nodelay()
		1..255:	.getch() timeout in tenth of seconds
		else:	cbreak mode
		'''
		self._timeout = tenth
		self.nodelay(False)

	def charcode(self, c):
		s = ""
		if c == 27: s = "ESC"
		if c == 32: s = "SPC"
		if c > 32 and c < 127: s = "%c" % c
		for k,v in curses.__dict__.items():
			if k.startswith("KEY_") and v == c:
				s = k[4:]
				break
		if s == '[' or s == ']':
			return "%s(%d)" % (s,c)
		return "%s[%d]" % (s,c)

	def getch(self):
		return self.scr.getch()

# curses bullshit ends
# I do not want to see curses.XXX references below anymore
# However we still rely on the curses window concept.
# Perhaps I will wrap this in future, too.

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
		self.name = name
		self.fd = fd
		if fd >= 0:
			nonblocking(fd)

	def sendfd(self):
#		return self.fd
		return -1

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
		self.fd = -1
#		self.remove.remove(self)
		return None

class WatchFile():

	def __init__(self, name):
		self.name = name
		self.init = True
		self.sock = None
		self.pos = 0
		self.fd = -1

	def sendfd(self):
		return self.sock and self.fd or -1

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
			except socket.error as xxx_todo_changeme:
				(e, s) = xxx_todo_changeme.args
				if e != errno.EAGAIN:
					raise
			data = b''.join(data)
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
		self.history = b''
		self.active = False
		self.inactive = False
		self.maxhist = MAX_HIST
		self.highlight = 0

	def name(self):
		return self.file.name

	def hist(self):
		return self.history

	def add(self, data):
		if not data: return
		l = len(data)-self.maxhist
		self.history = l>=0 and data[-self.maxhist:] or ( self.history[max(-len(self.history), l):] + data )

	def send(self, c):
		fd = self.file.sendfd()
		if fd<0:	return False
		return os.write(fd, c) >= 0

class Watcher():

	# Speed of GUI, do reads each WAIT_TENTHS/10 seconds
	WAIT_TENTHS = 3
	# check inode change each WAIT_TENTHS * RECHECK_COUNT / 10 seconds
	RECHECK_COUNT = 20

	scr = None
	allfiles = []
	files = []
	open = {}
	windows = 0
	jump = True
	columns = 2

	colors = [ -1, 3, 1, 5, -1 ]	# -1: unused
	C_BORDER=1	# user preference
	C_CURSOR=2	# user preference
	C_WARN=3	# user preference
	C_RED=4		# computed

	edit_mode = 0
	edit_win = 0
	edit_last = -1

	def __init__(self):
		self.recheck_count = 0
		debug("init")

	def add(self, file):
		self.allfiles.append(FileOb(file))

	def make_inactive(self, f):
		if not f.active: return False
		f.active = False

		self.files.remove(f)

		self.redraw = True
		return True

	def make_active(self, f, highlight=True):
		if f.active: return False
		f.active = True

		f.win = None
		f.highlight = highlight and self.RECHECK_COUNT or 0

		self.files.append(f)

		self.redraw = True
		return True

	def check_files(self, highlight=False):
		cnt = 0
		for a in self.allfiles:
			if a.file.check():
				if a.inactive:
					a.inactive = False
					cnt += 1
					debug("active", a.name())
				self.make_active(a, highlight)
				self.win_title(a)
			elif a.active:
				if not a.inactive:
					a.inactive = True
					cnt += 1
					debug("inactive", a.name())
				#self.make_inactive(a)
				self.win_title(a)
			#else: window is inactive, win_title may fail
		return cnt

	def win_title(self, a):
		a.animate = True
		if not a.win: return
		a.animate = a.highlight != 0
		p = a.inactive and '** ' or 'ok '
		s = a.name()
		w = a.w
		self.scr.addstr(a.ty, a.tx, p)
		self.scr.addstr(a.ty, a.tx+3, s[3-w:])
		l = len(s)+3
		if l<w:
			self.scr.addstr(a.ty, a.tx+l, " "*(w-l))
#		elif l>w:
#			a.animate = True
		att = self.out.color.pair(a.inactive and self.C_RED or self.C_BORDER)
		if self.edit_last==a.nr and self.edit_mode:
			att = self.out.color.reverse(self.out.color.pair(self.C_RED))
		elif a.highlight & 1:
			att = self.out.color.reverse(att)
		self.scr.chgat(a.ty, a.tx, w, att)

		if a.highlight>0:
			a.highlight -= 1

	def new_win(self, a, last=False):
		p = int(self.windows / self.tiles)
		n = self.windows % self.tiles

		a.nr = self.windows
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
		if p<int((len(self.files)-1)/self.tiles):
			h = self.height

		debug("new_win", n, x,y,w,h)
		assert h>0 and w>0 and y>=0 and x>=0
		win = self.scr.subwin(h, w, y, x)
		self.defaults(win)

		if n+1 < self.tiles and self.windows<len(self.files):
			self.scr.attron(self.out.color.pair(self.C_BORDER))
			self.scr.vline(y-1, x+w, 32, h+1)
			self.scr.attroff(self.out.color.pair(self.C_BORDER))
		a.tx = x
		a.ty = y-1
		a.w = w
		a.h = h

		a.win = win
		a.nl = False
		a.jump = False
		a.x = 0
		a.y = 0
		a.warn = self.out.color.normal()

		self.win_title(a)
		self.update(a, a.hist())

	def scroll(self, a):
		if self.jump:
			a.win.move(0, 0)
			a.jump = True
		else:
			a.win.scrollok(1)
			a.win.scroll()
			a.win.scrollok(0)
			a.win.move(a.win.getyx()[0], 0)
			a.jump = False

	def read_files(self):
		changes = 0
		self.recheck_count -= 1
		if self.recheck_count < 0:
			self.recheck_count = self.RECHECK_COUNT
			changes = self.check_files(True)
		for a in self.files:
			data = a.file.read()
			self.update(a, data)
			a.add(data)
		return changes

	def update(self, a, data):
		win = a.win
		if not win: return
		if a.animate:
			self.win_title(a)
		if not data: return
		self.out.nodelay()	# faster update
		win.chgat(a.y, 0, a.warn)
		win.move(a.y, a.x)
		y = a.y
		for c in data:
#			c = ord(c)
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
				a.warn = self.out.color.normal()
			if c == 7:
				a.warn = self.out.color.pair(self.C_WARN)
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
			if a.warn != self.out.color.normal():
				win.chgat(a.y, a.x, self.out.color.underline(a.warn))
			else:
				win.chgat(a.y, a.x, self.out.color.underline(self.out.color.pair(self.C_CURSOR)))
		else:
			if a.jump:
				win.chgat(a.y, 0, self.out.color.reverse(a.warn))
			win.chgat(a.y, a.x, 1, self.out.color.pair(self.C_CURSOR))

		win.noutrefresh()

	def setColor(self):
		self.out.color.set(self.C_BORDER, self.colors[self.C_BORDER])
		self.out.color.set(self.C_CURSOR, self.colors[self.C_CURSOR])
		self.out.color.set(self.C_WARN,   self.colors[self.C_WARN])
		self.out.color.setRed(self.C_RED, self.colors[self.C_BORDER])

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
			self.out.print(1, 0, "Empty commandline: missing files, unix sockets or '-' for stdin.  Press q to quit.")

	def layout(self, columns=None):
		debug("layout", columns)
		self.layout_imp(columns)
		return "(%dx%d)" % (self.height, self.width)

	def edit_off(self):
		n = self.edit_last
		self.edit_last = -1
		if n >= 0:
			self.win_title(self.window[n])
		return "Edit mode off"

	def edit_on(self):
		if self.edit_win == self.edit_last:
			return
		self.edit_off()
		self.edit_last = self.edit_win
		self.win_title(self.window[self.edit_win])

	def edit(self, mode=None):
		if not mode is None:
			self.edit_mode	= mode
		if not self.edit_mode:
			return self.edit_off()
		self.edit_on()
		return ("Edit mode " if self.edit_mode==2 else "Select mode ")+str(self.edit_win)

	def edit_send(self, c):
		return self.window[self.edit_win].send(c)

	def edit_move(self, c):
		if c == curses.KEY_LEFT:	d = -1
		elif c == curses.KEY_RIGHT:	d = 1
		elif c == curses.KEY_UP:	d = -self.columns
		elif c == curses.KEY_DOWN:	d = self.columns
		else:
			return None

		if not self.edit_mode:
			return "Please press Return first to enter selection mode"

		self.edit_win	+= d
		if self.edit_win<0:
			self.edit_win	= self.windows-1 - (self.windows-self.edit_win-1) % abs(d)
		elif self.edit_win>=self.windows:
			self.edit_win	= self.edit_win % abs(d)
		if self.edit_win<0 or self.edit_win>=self.windows:
			self.edit_win	= 0
		self.edit_on()
		return "moved to {}".format(self.edit_win)

	def run(self, out):
		debug("run")
		self.out = out

		# scr shall be superseeded by out (Curses) in future.
		# So following is a hack until Curses class is extended a suitable way:
		scr = out.scr
		self.scr = scr
		# curses, scr and win shall be duck typed in future for similar output
		# so the last argument to "print" will go away

		out.saneMode()
		out.showCursor(False)
		out.timeout(self.WAIT_TENTHS)

		self.check_files()
		self.layout()
		self.redraw = False

		host = socket.getfqdn()
		user = getuser()

		c = 0
		cwd = "CWD " + os.getcwd()
		s = None
		scol = 0
		ticks = 0
		haveMsg = False
		last = None
		loop = True
		c0 = None
		c1 = None
		fast = False
		esc = 0
		escs = 0
		enter = 10
		while loop:
			c2 = c1
			c1 = c0
			c0 = None
			if self.redraw:
				self.redraw = False
				s = (s and s+" " or "") + "Layout" + self.layout()

			if s:
				ticks = 20

			if ticks == 0:
				s = ("{0}", "Select {1} -- Return to Edit", "Edit {1} -- ESC+Return to leave")[self.edit_mode].format(cwd, self.edit_win)
				ticks = -1

			now = int(time.time())
			if now != last:
				ticks -= 1
				out.print(0, -1, " (%s@%s) %s" % (user, host, time.strftime("%a %Y-%m-%d %H:%M:%S %Z")))
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
				out.print(0, 0, s)
				if scol:
					if scol<0:
						scol = -scol;
						self.colors[scol] -= 1
					else:
						self.colors[scol] += 1
					self.setColor()
					scr.chgat(0, 0, -1, out.color.pair(scol))
					scr.chgat(0, 0, 10, out.color.reverse(out.color.pair(scol)))
				else:
					scr.chgat(0, 0, -1, out.color.normal())
				scol = 0
				s = None

			scr.move(0, 0)
			scr.refresh()

			c = out.getch()
#			if c==13: c=10	# CRAP!  How to enable icrnl with curses?

			# special ESC handling
			if c == 27:
				esc = esc + 1
				if esc<5:	# ESC ESC ESC ESC ESC is the same as ESC Return
					continue

			editing = self.edit_mode == 2
			escs = 0
			if esc:
				escs = esc - 1
				if c<0:
					c = 27	# send ESC
				elif c==27 or c==10 or c==13:
					c = out.BREAK()
				# more specials?
				else:
					escs = esc
				esc = 0

			# no key received
			if c<0:
				out.nodelay(False)
				c = self.read_files()
				if c:
					s = str(c) + " file(s) changed status"
				continue

			# if you type something irregular, type to fast processing
			c0 = out.charcode(c)
			if fast and (c1 or c2):
				c0 = (c1 or c2)+c0
				c1 = None
				c2 = None
				s = "typed too fast"
				continue

			fast = False

			# Sequences, which need to be processed by Watcher
			if out.isResize(c):
				s = "Resized " + self.layout()
				continue

			if c == out.BREAK():
				s	= self.edit(0)
				continue

			# Window edit mode
			if editing:	# editing, send to window
				if c==enter:	c=10
				elif c==10:	c=enter
				c = out.key_sequence(c, escs)
				if c is None:
					s = "unknown key to send"
				elif self.edit_send(c):
					s = escs and "sent with %d ESC" % (escs) or "sent"
				else:
					s = "failed to send"
				continue

			s	= self.edit_move(c)
			if s:	continue

			# Window select mode
#			if c == 9:		# Return selects current window
#				if not self.edit_mode:
#					s = "Please press Enter to select window"
#					continue
#				s	= self.edit(2)
#				continue

			if c == 10 or c==13:	# CRAP icrnl
				enter	= c
				s	= self.edit(self.edit_mode and 2 or 1)
#				ticks = 0
				continue

			if c == 12:	# ^L
				self.redraw	= True
				s = "Redraw"
				fast = True

			if c == 99:	# c
				s = "Cursor color"
				scol = self.C_CURSOR

			if c == 67:	# C
				s = "Cursor color"
				scol = -self.C_CURSOR

			if c == 103:	# g
				s = "Grid color"
				scol = self.C_BORDER

			if c == 71:	# G
				s = "Grid color"
				scol = -self.C_BORDER

			if c == 119:	# w
				s = "Warn color"
				scol = self.C_WARN

			if c == 87:	# W
				s = "Warn color"
				scol = -self.C_WARN

			if c >= 48 and c < 58:	# 0-9
				s = "Layout " + self.layout((c-48) or 10)

			if c == 113:	# q
				s = "Quit"
				loop = False

			if c == 73:	# I
				cnt = 0
				for f in self.allfiles:
					if not f.active and self.make_active(f):
						cnt += 1
				s = str(cnt)+" inactive shown"

			if c == 105:	# i
				cnt = 0
				for f in self.allfiles:
					if f.inactive and self.make_inactive(f):
						cnt += 1
				s = str(cnt)+" inactive hidden"

			if c == 106:	# j
				self.jump = True
				s = "Jump mode"

			if c == 115:	# s
				self.jump = False
				s = "Scroll mode"

			if s == None:
				s = "Help: Quit Warn/Grid/Cursor Jump/Scroll 0-9 Ignore"
				fast = True

		scr.refresh()

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
	Curses(w)

