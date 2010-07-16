#!/usr/bin/env python2.6
#
# $Header$
#
# ./watcher.py -|file|sock...
#
# This Works is placed under the terms of the Copyright Less License,
# see file COPYRIGHT.CLL.  USE AT OWN RISK, ABSOLUTELY NO WARRANTY.
#
# $Log$
# Revision 1.4  2010-07-16 16:03:26  tino
# keystroke commands, more layout, resize handling, etc.
#
# Revision 1.3  2010-07-16 00:13:35  tino
# See ChangeLog
#
# Revision 1.2  2010-07-15 07:28:46  tino
# See ANNOUNCE

import curses, sys, os, fcntl, socket, stat, time, warnings

BUFSIZ=4096
MAX_HIST=10000

def nonblocking(fd):
	flags	= fcntl.fcntl(fd, fcntl.F_GETFL)
	fcntl.fcntl(fd, fcntl.F_SETFL, flags|os.O_NONBLOCK)

class WatchPipe():

	def __init__(self,fd,name):
		self.name	= name;
		self.fd		= fd
		nonblocking(fd)

	def check(self):
		return self.fd>=0

	def read(self):
		if self.fd<0:	return None

		try:
			data	= os.read(self.fd,BUFSIZ)
		except OSError:
			return None

		if len(data)>0:
			return data

		os.close(self.fd)
		self.fd	= -1;
#		self.remove.remove(self)
		return None

class WatchFile():

	def __init__(self,name):
		self.name	= name
		self.init	= True
		self.sock	= None
		self.pos	= 0
		self.fd		= -1

	def close(self):
		if self.fd<0: return
		if self.sock:
			self.sock.close()
			self.sock	= None
		else:
			os.close(self.fd)
		self.fd	= -1

	def open_socket(self):
		self.sock	= socket.socket(socket.AF_UNIX)
		self.sock.connect(self.name)
		self.fd		= self.sock.fileno()
		nonblocking(self.fd)

	def open_file(self):
		self.fd		= os.open(self.name,os.O_RDONLY|os.O_NONBLOCK)
		if self.init:
			self.init	= False
			self.pos	= os.lseek(self.fd,0,os.SEEK_END)
			self.pos	= os.lseek(self.fd,max(0,self.pos-MAX_HIST),os.SEEK_SET)

	def open(self):
		if self.fd>=0:	return True
		try:
			self.pos	= 0
			self.stat	= os.stat(self.name)
			if stat.S_ISSOCK(self.stat.st_mode):
				self.open_socket()
			else:
				self.open_file()
			return True
		except:
			self.init	= False
			self.close()
		return False

	def reopen(self):
		self.close()
		return self.open()

	def check(self):
		if self.fd>=0 and self.sock==None:
			try:
				st	= os.stat(self.name)
				if st.st_ino!=self.stat.st_ino:
					self.close()
			except:
				self.close()
		if self.fd<0:
			return self.open()
		return True

	def read(self):
		if self.fd<0: return None

		if self.sock:
			data	= []
			try:
				for i in range(1,20):
					data.append(self.sock.recv(BUFSIZ))
			except socket.error,(e,s):
				if e!=11:
					raise
			data=''.join(data)
			if data=='': return None
			self.pos	+= len(data)
			return data

		try:
			st	= os.fstat(self.fd)
		except:
			self.close()
			return None

		if st.st_size<self.pos:
			# File has shrunken
			if not self.reopen():
				return None

		if st.st_size==self.pos:
			return None

		try:
			data	= os.read(self.fd,BUFSIZ)
		except IOError:
			return None
		self.pos	+= len(data)
		return data

class FileOb:
	def __init__(self,file):
		self.file	= file
		self.win	= None
		self.hist	= ""

class Watcher():

	scr	= None
	files	= []
	open	= {}
	windows	= 0
	jump	= False
	minlines= 7
	gridcolor	= 0
	cursorcolor	= 0
	warncolor	= 0

	C_BORDER	= 8
	C_WARN		= 9
	C_RED		= 10
	C_CURSOR	= 11

	def __init__(self):
		self.count	= 0;

	def add(self, file):
		self.files.append(FileOb(file))

	def checkFiles(self):
		for a in self.files:
			if a.file.check():
				self.scr.chgat(a.ty,a.tx,a.w,curses.color_pair(self.C_BORDER))
			else:
				self.scr.chgat(a.ty,a.tx,a.w,curses.color_pair(self.C_RED))

	def newWin(self,a):
		p	= int(self.windows / self.tiles)
		n	= self.windows % self.tiles

		y	= 2+p*(self.height+1)
		x	= n*(self.width+1)

		h,w	= self.scr.getmaxyx()
		assert h>2 and w>2

		h	-= y
		w	-= x
		if h>=self.height*2:
			h	= self.height
		if w>=self.width*2:
			w	= self.width

		assert h>0 and w>0 and y>=0 and x>=0
		win	= self.scr.subwin(h,w,y,x)
		self.defaults(win)

		if n+1<self.tiles:
			self.scr.attron(curses.color_pair(self.C_BORDER))
			self.scr.vline(y-1,x+w,32,h+1)
			self.scr.attroff(curses.color_pair(self.C_BORDER))
		a.tx	= x
		a.ty	= y-1
		a.w	= w
		a.h	= h
		self.scr.addstr(y-1,x,a.file.name[-w:]+" "*max(0,w-len(a.file.name)))
		self.scr.chgat(y-1,0,curses.color_pair(self.C_BORDER))
		win.scrollok(1)
		win.idcok(1)
		win.idlok(1)

		self.windows	+= 1

		a.win	= win
		a.nl	= False
		a.x	= 0
		a.y	= 0
		a.warn	= curses.A_NORMAL

		if len(a.hist):
			self.update(a,a.hist)

	def scroll(self,a):
		if self.jump:
			a.win.move(0,0)
		else:
			a.win.scroll()
			a.win.move(a.win.getyx()[0],0)

	def readFiles(self):
		self.count	+= 1
		if self.count>3:
			self.count	= 0
			self.checkFiles()
		for a in self.files:
			data	= a.file.read()
			if data==None: continue
			self.update(a,data)
			if len(data)>=MAX_HIST:
				a.hist	= data[-MAX_HIST:]
			else:
				a.hist	= a.hist[max(-len(a.hist),len(data)-MAX_HIST):]+data

	def update(self,a,data):
		win	= a.win
		win.chgat(a.y,0,a.warn)
		win.move(a.y,a.x)
		y=a.y
		for c in data:
			c=ord(c)
			if c==13:
				win.move(win.getyx()[0],0)
				continue
			if c==10:
				a.nl	= True
				continue
			if a.nl:
				try:
					win.move(win.getyx()[0]+1,0)
					win.clrtoeol()
				except:
					self.scroll(a)
				a.nl	= False
				a.warn	= curses.A_NORMAL
			if c==7:
				a.warn	= curses.color_pair(self.C_WARN)
				y,x=win.getyx()
				win.chgat(y,0,a.warn)
				win.move(y,x)
				continue
			try:
				win.addch(c,a.warn)
			except:
				self.scroll(a)
				win.addch(c,a.warn)

			a.y,a.x=win.getyx()
			if a.y!=y:
				win.clrtoeol()
				y=a.y
				
		a.y,a.x=win.getyx()
		if a.nl:
			if a.warn!=curses.A_NORMAL:
				win.chgat(a.y,a.x,a.warn|curses.A_UNDERLINE)
			else:
				win.chgat(a.y,a.x,curses.color_pair(self.C_CURSOR)|curses.A_UNDERLINE)
		else:
			if self.jump:
				win.chgat(a.y,0,a.warn|curses.A_REVERSE)
			win.chgat(a.y,a.x,1,curses.color_pair(self.C_CURSOR))

		win.noutrefresh()

	def contrast(self,color):
		if color==curses.COLOR_BLUE or color==curses.COLOR_RED:
			return curses.COLOR_WHITE
		if color==curses.COLOR_BLACK:
			return curses.COLOR_CYAN
		return curses.COLOR_BLACK

	def color(self,value):
		cols	= [curses.COLOR_BLUE,curses.COLOR_GREEN,curses.COLOR_YELLOW,curses.COLOR_CYAN,curses.COLOR_MAGENTA,curses.COLOR_RED,curses.COLOR_WHITE,curses.COLOR_BLACK]
		return cols[value%len(cols)]

	def setSingleColor(self,color,value):
		bg	= self.color(value)
		curses.init_pair(color,self.contrast(bg),bg)

	def setColor(self):
		self.setSingleColor(self.C_BORDER, self.gridcolor)
		self.setSingleColor(self.C_CURSOR, self.cursorcolor+1)
		self.setSingleColor(self.C_WARN,   self.warncolor+5)

		curses.init_pair(self.C_RED,curses.COLOR_RED,curses.COLOR_BLUE)

	def defaults(self,win):
		win.idcok(1)
		win.idlok(1)
		win.leaveok(0)
		win.keypad(1)

	def layoutImp(self, minlines):

		if minlines!=None:
			if self.minlines==minlines: return
			self.minlines	= minlines

		self.scr.clear()
		self.defaults(self.scr)
		self.scr.redrawwin()
		self.setColor()

		h,w	= self.scr.getmaxyx()
		assert h>2 and w>2

		minlines	= self.minlines
		if minlines<1: minlines=1
		if minlines>h-2: minlines=h-2

		d	= int((h-1)/(minlines+1))
		d	= int((len(self.files)+d-1)/d)
		n	= int((len(self.files)+d-1)/d)

		self.tiles	= d
		self.width	= int((w-d+1)/d)
		self.height	= int((h-1-n)/n)

		self.windows	= 0
		for a in self.files:
			self.newWin(a)

	def layout(self,minlines=None):
		self.layoutImp(minlines)
		return "(%dx%d)" % (self.height, self.width)

	def charcode(self,c):
		s=""
		if c==27: s="ESC"
		if c==32: s="SPC"
		if c>32 and c<127: s="%c" % c
		for k,v in curses.__dict__.iteritems():
			if k.startswith("KEY_") and v==c:
				s=k[4:]
				break
		if s=='[' or s==']':
			return "%s(%d)" % (s,c);
		return "%s[%d]" % (s,c);

	def run(self,scr):
		self.scr	= scr

		curses.nonl()
		curses.halfdelay(3)
		try:
			curses.curs_set(0)
		except:
			pass

		self.layout()
		self.checkFiles()

		host	= socket.getfqdn()
		try:
			user	= os.getlogin()
		except:
			user	= os.environ['USERNAME']

		c	= 0
		cwd	= "CWD "+os.getcwd()
		s	= None
		ticks	= 0
		haveMsg	= False
		last	= None
		loop	= True
		c0	= None
		c1	= None
		while loop:
			c2	= c1
			c1	= c0
			c0	= None
			if s:
				ticks	= 20

			if ticks==0:
				s	= cwd
				ticks	= -1

			now	= int(time.time())
			if now!=last:
				ticks	-= 1
				t	= "(%s@%s) %s" % (user, host, time.strftime("%a %Y-%m-%d %H:%M:%S %Z"))
				scr.addstr(0,max(2,scr.getmaxyx()[1]-len(t)),t)
				if last:
					if now<last:
						s	= "Time went backwards"
						ticks	= -1
					elif now>last+10:
						s	= "Time jump"
						ticks	= -1
				last	= now

			if s:
				scr.move(0,0)
				if c0 or c1 or c2:
					scr.addstr(c0 or c1 or c2)
					scr.addstr(" ")
				scr.addstr(s)
				scr.clrtoeol()
				s	= None

			scr.move(0,0)
			scr.refresh()

			c	= scr.getch()
			if c<0:
				self.readFiles()
				continue

			c0=self.charcode(c)
			if c1 or c2:
				c0=(c1 or c2)+c0
				c1=None
				c2=None
				s="typed too fast"
				continue

			s="Quit Color Jump Scroll 1-9"

			if c==curses.KEY_RESIZE:
				s="Resized "+self.layout()
			if c==12:
				s="Redraw "+self.layout()
			if c==99:
				self.cursorcolor+=1
				self.setColor()
				s="Cursor color"
			if c==67:
				self.cursorcolor-=1
				self.setColor()
				s="Cursor color"
			if c==103:
				self.gridcolor+=1
				self.setColor()
				s="Grid color"
			if c==71:
				self.gridcolor-=1
				self.setColor()
				s="Grid color"
			if c==119:
				self.warncolor-=1
				self.setColor()
				s="Warn color"
			if c==87:
				self.warncolor+=1
				self.setColor()
				s="Warn color"

			if c>=49 and c<58:
				s="Layout "+self.layout((c-49)*(c-49)+3)

			if c==113:
				s="Quit"
				loop=False
			if c==106:	#j
				self.jump	= True
				s="Jump mode"
			if c==115:	#s
				self.jump	= False
				s="Scroll mode"
		scr.refresh()

	def main(self):
		curses.wrapper(lambda scr:self.run(scr))


if __name__=="__main__":
	w	= Watcher()
	for a in sys.argv[1:]:
		if a=="-":
			w.add(WatchPipe(os.dup(0),"-stdin-"))
			os.dup2(1,0)
		else:
			w.add(WatchFile(a))
	w.main()

