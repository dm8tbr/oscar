#!/usr/bin/env python

from datetime import datetime
import struct
import re
import select
import socket
import random
import json
import urllib2
import hashlib
import hmac
import base64
import HTMLParser

import trello
from twilio.rest import TwilioRestClient

import smtplib

from lib import trellodb
from lib import conf


def parse_scanner_data(scanner_data):
    upc_chars = []
    for i in range(0, len(scanner_data), 16):
        chunk = scanner_data[i:i+16]

        # The chunks we care about will match
        # __  __  __  __  __  __  __  __  01  00  __  00  00  00  00  00
        if chunk[8:10] != '\x01\x00' or chunk[11:] != '\x00\x00\x00\x00\x00':
            continue

        digit_int = struct.unpack('>h', chunk[9:11])[0]
        upc_chars.append(str((digit_int - 1) % 10))

    return ''.join(upc_chars)


class UPCAPI:
    BASEURL = 'http://api.upcdatabase.org/json'

    def __init__(self, api_key):
        self._api_key = api_key

    def _signature(self, upc):
        h = hmac.new(self._auth_key, upc, hashlib.sha1)
        return base64.b64encode(h.digest())

    def _url(self, upc):
        return '{0}/{1}/{2}'.format(self.BASEURL, self._api_key, upc)

    def get_description(self, upc):
        """Returns the product description for the given UPC.
           `upc`: A string containing the UPC."""
        url = self._url(upc)
        print url
        json_blob = urllib2.urlopen(url).read()
        print json_blob
        return json.loads(json_blob)['description']


def local_ip():
    """Returns the IP that the local host uses to talk to the Internet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("trello.com", 80))
    addr = s.getsockname()[0]
    s.close()
    return addr


def generate_opp_id():
    return ''.join(random.sample('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', 12))


def opp_url(opp):
    return '{0}/learn-barcode/{1}'.format(conf.get()['base_url'], opp['opp_id'])


def create_barcode_opp(trello_db, barcode, desc=''):
    """Creates a learning opportunity for the given barcode and writes it to Trello.
    
       Returns the dict."""
    opp = {
        'type': 'barcode',
        'opp_id': generate_opp_id(),
        'barcode': barcode,
        'desc': desc,
        'created_dt': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    }

    trello_db.insert('learning_opportunities', opp)
    return opp


def publish_barcode_opp(opp):
    message = '''Hi!\n\nOscar here. You scanned a code ({1}) I didn't recognize.\nCare to fill me in?\n{0}'''.format(opp_url(opp), opp['barcode'])
    subject = '''Didn't Recognize Barcode'''
    communication_method = conf.get()['communication_method']
    if communication_method == 'email':
        send_via_email(message, subject)
    elif communication_method == 'debug':
        print message
    else:
        send_via_twilio(message)

def send_via_twilio(msg):
    client = TwilioRestClient(conf.get()['twilio_sid'], conf.get()['twilio_token'])
    message = client.sms.messages.create(body=msg,
                                         to='+{0}'.format(conf.get()['twilio_dest']),
                                         from_='+{0}'.format(conf.get()['twilio_src']))

def send_via_email(msg, subject):
    to = conf.get()['email_dest']
    mail_user = conf.get()['mail_user'] 
    mail_pwd = conf.get()['mail_password']
    smtpserver = smtplib.SMTP(conf.get()['mail_server'],587)
    smtpserver.ehlo()
    smtpserver.starttls()
    smtpserver.ehlo
    smtpserver.login(mail_user, mail_pwd)
    header = 'To:' + to + '\n' + 'From: ' + mail_user + '\n' + 'Subject: ' + subject + ' \n'
    print '\nSending email...\n'
    message = header + '\n' + msg +'\n'
    smtpserver.sendmail(mail_user, to, message)
    print 'Email sent.'
    smtpserver.close()

def match_barcode_rule(trello_db, barcode):
    """Finds a barcode rule matching the given barcode.

       Returns the rule if it exists, otherwise returns None."""
    rules = trello_db.get_all('barcode_rules')
    for r in rules:
        if r['barcode'] == barcode:
            return r
    return None


def match_description_rule(trello_db, desc):
    """Finds a description rule matching the given product description.

       Returns the rule if it exists, otherwise returns None."""
    rules = trello_db.get_all('description_rules')
    for r in rules:
        if r['search_term'].lower() in desc.lower():
            return r
    return None


def add_grocery_item(trello_api, item):
    """Adds the given item to the grocery list (if it's not already present)."""
    # Get the current grocery list
    grocery_board_id = conf.get()['trello_grocery_board']
    all_lists = trello_api.boards.get_list(grocery_board_id)
    grocery_list = [x for x in all_lists if x['name'] == conf.get()['trello_grocery_list']][0]
    cards = trello_api.lists.get_card(grocery_list['id'])

    print "checking if that item is already on our list"
    for card in cards:
        if card['name'].startswith(item):
            qty = 2
            print card['name']
            matches = re.findall(r'\(\d+\)', card['name'])
            if matches:
                qty = int(matches[0].replace("(","").replace(")",""))
                qty += 1
                print "increasing qty on '{0}' to '{1}'".format(item, qty)
            trello_api.cards.update_name(card['id'], item + " (" + str(qty) + ")")
            return

    print "Adding '{0}' to grocery list".format(item)
    trello_api.lists.new_card(grocery_list['id'], item)

    #card_names = [card['name'] for card in cards]

    # Add item if it's not there already
    #if item not in card_names:
        #print "Adding '{0}' to grocery list".format(item)
        #trello_api.lists.new_card(grocery_list['id'], item)
    #else:
        #print "Item '{0}' is already on the grocery list; not adding".format(item)


trello_api = trello.TrelloApi(conf.get()['trello_app_key'])
trello_api.set_token(conf.get()['trello_token'])
trello_db = trellodb.TrelloDB(trello_api, conf.get()['trello_db_board'])

f = open(conf.get()['scanner_device'], 'rb')
while True:
    print 'Waiting for scanner data'

    # Wait for binary data from the scanner and then read it
    scan_complete = False
    scanner_data = ''
    while True:
        rlist, _wlist, _elist = select.select([f], [], [], 0.1)
        if rlist != []:
            new_data = ''
            while not new_data.endswith('\x01\x00\x1c\x00\x01\x00\x00\x00'):
                new_data = rlist[0].read(16)
                scanner_data += new_data
            # There are 4 more keystrokes sent after the one we matched against,
            # so we flush out that buffer before proceeding:
            [rlist[0].read(16) for i in range(4)]
            scan_complete = True
        if scan_complete:
            break
 
    # Parse the binary data as a barcode
    barcode = parse_scanner_data(scanner_data)
    print "Scanned barcode '{0}'".format(barcode)


    barcode_rule = match_barcode_rule(trello_db, barcode)
    if barcode_rule is not None:
        add_grocery_item(trello_api, barcode_rule['item'])
        continue

    # Get the item's description
    u = UPCAPI(conf.get()['upcdatabase_api_key'])
    try:
        html = HTMLParser.HTMLParser()
        desc = html.unescape(u.get_description(barcode))
        print "Received description '{0}' for barcode {1}".format(desc, repr(barcode))
    except KeyError, e:
        print 'invalid barcode received... adding opportunity to database'
        opp = create_barcode_opp(trello_db, barcode)
        print "Publishing learning opportunity"
        publish_barcode_opp(opp)
        continue
    except urllib2.HTTPError, e:
        if 'UPC/EAN code invalid' in e.msg:
            print "Barcode {0} not recognized as a UPC; creating learning opportunity".format(repr(barcode))
            try:
                opp = create_barcode_opp(trello_db, barcode, desc)
            except:
                opp = create_barcode_opp(trello_db, barcode)
            print "Publishing learning opportunity"
            publish_barcode_opp(opp)
            continue
        elif 'Not found' in e.msg:
            print "Barcode {0} not found in UPC database; creating learning opportunity".format(repr(barcode))
            try:
                opp = create_barcode_opp(trello_db, barcode, desc)
            except:
                opp = create_barcode_opp(trello_db, barcode)
            print "Publishing learning opportunity via SMS"
            publish_barcode_opp(opp)
            continue
        else:
            raise

    add_grocery_item(trello_api, desc)
