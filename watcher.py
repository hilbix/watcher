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
# Revision 1.1  2010-07-15 03:57:11  tino
# First version, it works
#

import curses, sys, os, fcntl, socket, stat

BUFSIZ=4096

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
			self.pos	= os.lseek(self.fd,max(0,self.pos-1000),os.SEEK_SET)

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
		if self.fd>=0:
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
					data.append(self.sock.recv(4096))
			except socket.error,(e,s):
				if e!=11:
					raise
			data=''.join(data)
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
		self.nl		= False
		self.x		= 0
		self.y		= 0
		self.warn	= curses.A_NORMAL

class Watcher():

	scr	= None
	files	= []
	open	= {}
	windows	= 0

	def __init__(self):
		self.count	= 0;

	def add(self, file):
		self.files.append(FileOb(file))

	def checkFiles(self):
		for a in self.files:
			if a.file.check():
				self.scr.chgat(a.ty,a.tx,a.w,curses.color_pair(8))
			else:
				self.scr.chgat(a.ty,a.tx,a.w,curses.color_pair(10))

	def newWin(self,a):
		n	= self.windows % self.tiles
		p	= int(self.windows / self.tiles)
		x	= n*(self.width+1)
		y	= 2+p*(self.height+1)
		win	= curses.newwin(self.height,self.width,y,x)
		if n+1<self.tiles:
			self.scr.attron(curses.color_pair(8))
			self.scr.vline(y-1,x+self.width,32,self.height+1)
			self.scr.attroff(curses.color_pair(8))
		a.tx	= x
		a.ty	= y-1
		a.w	= self.width
		a.h	= self.height
		self.scr.addstr(y-1,x,a.file.name[-self.width:]+" "*max(0,self.width-len(a.file.name)))
		self.scr.chgat(y-1,0,curses.color_pair(8))
		win.scrollok(1)
		win.idcok(1)
		win.idlok(1)
		self.windows	+= 1
		a.win	= win

	def readFiles(self):
		self.count	+= 1
		if self.count>3:
			self.count	= 0
			self.checkFiles()
		for a in self.files:
			data	= a.file.read()
			if data==None: continue
			win	= a.win
			win.chgat(a.y,a.x,a.warn)
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
					except:
						win.scroll()
						win.move(win.getyx()[0],0)
					a.nl	= False
					a.warn	= curses.A_NORMAL
				if c==7:
					a.warn	= curses.color_pair(9)
					win.chgat(win.getyx()[0],0,curses.color_pair(9))
					continue
				try:
					win.addch(c,a.warn)
				except:
					win.scroll()
					win.move(win.getyx()[0],0)
					win.addch(c,a.warn)
			a.y,a.x=win.getyx()
			if a.nl:
				if a.warn!=curses.A_NORMAL:
					win.chgat(a.y,a.x,a.warn|curses.A_UNDERLINE)
				else:
					win.chgat(a.y,a.x,curses.color_pair(8)|curses.A_UNDERLINE)
			else:
				win.chgat(a.y,a.x,1,curses.color_pair(8))
			win.noutrefresh()

	def layout(self):
		h,w=self.scr.getmaxyx()
		d=1
		while h/len(self.files)*d<7:
			d	+= 1
		w	= int((w-d+1)/d)
		n	= int((len(self.files)+d-1)/d)
		h	= int((h-1-n)/n)

		self.width	= w
		self.height	= h
		self.tiles	= d

	def run(self,scr):
		self.scr	= scr
		curses.halfdelay(5)
		scr.clear()
		scr.idcok(1)
		scr.idlok(1)
		scr.leaveok(0)
		curses.init_pair(8,curses.COLOR_WHITE,curses.COLOR_BLUE)
		curses.init_pair(9,curses.COLOR_WHITE,curses.COLOR_RED)
		curses.init_pair(10,curses.COLOR_RED,curses.COLOR_BLUE)
		self.layout()
		for a in self.files:
			self.newWin(a)
		self.checkFiles()

		while 1:
			scr.move(0,0)
			scr.refresh()
			c	= scr.getch()
			if c<0:
				self.readFiles()
			elif c==27:
				break
			else:
				scr.addstr(0,0,"ESC to exit   (%d)  " % c)

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

