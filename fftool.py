#!/usr/bin/env python

##################################################################
# fftool.py
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following
# disclaimer in the documentation and/or other materials provided
# with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Copyright (c) 2022 Daniel O'Connor <darius@dons.net.au>
#
##################################################################

import argparse
import datetime
import os.path
import re
import socket
import struct
import tempfile
import time
import urllib.parse
import webbrowser

# Protocol cribbed from:
# https://github.com/Slugger2k/FlashForgePrinterApi
# https://github.com/DanMcInerney/flashforge-finder-api

cmdackre = re.compile(r'CMD [0-9A-Z]+ Received.')

def main():
    parser = argparse.ArgumentParser(epilog = 'Control a Flash Forge printer via network',
                                     formatter_class = argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--verbose', '-v', help = 'Increase debug level', action = 'count', default = 0)

    parser.set_defaults(func = None)
    subparsers = parser.add_subparsers()

    parser_scan = subparsers.add_parser('scan', help = 'Scan the local network for printers')
    parser_scan.set_defaults(func = scan)

    parser_status = subparsers.add_parser('status', help = 'Get status from the printer')
    parser_status.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_status.set_defaults(func = status)

    parser_progress = subparsers.add_parser('progress', help = 'Display progress')
    parser_progress.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_progress.set_defaults(func = progress)

    parser_listfiles = subparsers.add_parser('listfiles', help = 'List files on printer')
    parser_listfiles.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_listfiles.set_defaults(func = listfiles)

    parser_getimage = subparsers.add_parser('getimage', help = 'Get image for file from printer')
    parser_getimage.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_getimage.add_argument('image', help = 'Image path')
    parser_getimage.set_defaults(func = getimage)

    parser_send = subparsers.add_parser('send', help = 'Send G-Code file to printer')
    parser_send.add_argument('--print', help = 'Print file after upload')
    parser_send.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_send.add_argument('file', help = 'G-Code file to send', type = argparse.FileType('rb'))
    parser_send.set_defaults(func = send)

    parser_print = subparsers.add_parser('print', help = 'Print a file printer')
    parser_print.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_print.add_argument('file', help = 'File path')
    parser_print.set_defaults(func = printfile)

    parser_pause = subparsers.add_parser('pause', help = 'Pause printer')
    parser_pause.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_pause.set_defaults(func = pause)

    parser_resume = subparsers.add_parser('resume', help = 'Resume printer')
    parser_resume.add_argument('host', help = 'Host to connect to (IP[:port])')
    parser_resume.set_defaults(func = resume)

    args = parser.parse_args()
    if args.func == None:
        parser.error('No command specified')

    args.func(parser, args)

def scan(parser, args):
    '''Scan for printers on the local network'''
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    s.sendto(b'c0a800de46500000', ('225.0.0.9', 19000))
    tout = datetime.datetime.now() + datetime.timedelta(seconds = 1)

    while datetime.datetime.now() < tout:
        try:
            data, addr = s.recvfrom(1024)
            # No idea what the remaining 108 bytes are, all are 0 except the last 8
            name = data[0:32].decode('ascii').rstrip('\0')
            print('Found %s at %s' % (name, addr[0]))
        except BlockingIOError:
            pass
        time.sleep(0.1)

def connect(hoststr):
    '''Connect to a printer and return a file-like object'''
    tmp = hoststr.split(':', 1)
    ip = tmp[0]
    if len(tmp) == 1:
        port = 8899
    else:
        port = int(tmp[1])

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(None)
    s.connect((ip, port))
    sf = s.makefile('rwb')
    return sf

def sendcmd(s, cmd):
    '''Send command and read back reply until "ok"'''
    s.write(cmd)
    s.flush()
    data = str(s.readline(), 'ascii')
    if cmdackre.match(data) is None:
        raise Exception('Unknown reply to command "%s": %s' % (cmd, data))
    reply = []
    while True:
        line = str(s.readline(), 'ascii').strip()
        if line == 'ok':
            break
        reply.append(line)
    return reply

def status(parser, args):
    '''Fetch and display printer status'''
    s = connect(args.host)
    reply = sendcmd(s, b'~M119\r\n')
    list(map(print, reply))

def progress(parser, args):
    '''Fetch and display progress indication'''
    s = connect(args.host)
    reply = sendcmd(s, b'~M27\r\n')
    list(map(print, reply))

def listfiles(parser, args):
    '''List files stored on printer'''
    s = connect(args.host)
    sendcmd(s, b'~M661\r\n')
    hdr = s.read(8)
    magic, nfiles = struct.unpack('>4sI', hdr)
    assert(magic == b'D\xaa\xaaD')
    for _ in range(nfiles):
        hdr = s.read(8)
        magic, fnamelen = struct.unpack('>4sI', hdr)
        assert(magic == b'::\xa3\xa3')
        fname = s.read(fnamelen)
        print(str(fname, 'ascii'))

def getimage(parser, args):
    '''Fetch preview image for file from printer and view it'''
    s = connect(args.host)
    sendcmd(s, b'~M662 %s\r\n' % (bytes(args.image, 'ascii')))
    hdr = s.read(8)
    magic, imglen = struct.unpack('>4sI', hdr)
    assert(magic == b'**\xa2\xa2')
    data = s.read(imglen)
    with tempfile.NamedTemporaryFile(suffix = '.png') as tmppng:
        tmppng.write(data)
        webbrowser.open('file://' + urllib.parse.quote(tmppng.name))

def send(parser, args):
    s = connect(args.host)
    fname = os.path.basename(args.file.name)
    assert len(fname) <= 36

    flen = args.file.seek(0, 2)
    args.file.seek(0, 0)

    reply = sendcmd(s, bytes('~M28 %d 0:/user/%s\r\n' % (flen, fname), 'ascii'))
    list(map(print, reply))
    BLOCKSIZE = 1024

    bcount = 0
    sent = 0
    while True:
        if bcount % 10 == 0:
            print('\rProgress: %.1f %%' % (sent / flen * 100.0), end = '')
        data = args.file.read(BLOCKSIZE)
        if len(data) == 0:
            break
        s.write(data)
        s.flush()
        sent += BLOCKSIZE
        bcount += 1

    # The M29 needs to go in a separate packet but flush doesn't do the trick
    # Event setting TCP_NODELAY doesn't do it
    s.flush()
    time.sleep(0.1)
    s.flush()
    print('\nFinished transfer')
    reply = sendcmd(s, b'~M29\r\n')
    list(map(print, reply))
    if args.print:
        doprintfile(s, fname)

def printfile(parser, args):
    '''Tell the printer to print a file'''
    s = connect(args.host)
    reply = doprintfile(s, args.file)
    list(map(print, reply))

def doprintfile(s, fname):
    if not fname.startswith('/'):
        fname = '/user/' + fname

    return sendcmd(s, b'~M23 0:%s\r\n' % (bytes(fname, 'ascii')))

def pause(parser, args):
    '''Pause printing'''
    s = connect(args.host)
    reply = sendcmd(s, b'~M25\r\n')
    list(map(print, reply))

def resume(parser, args):
    '''Resume printing'''
    s = connect(args.host)
    reply = sendcmd(s, b'~M24\r\n')
    list(map(print, reply))

if __name__ == '__main__':
    main()
