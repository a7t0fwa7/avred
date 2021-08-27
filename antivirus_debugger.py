import logging
import os
import sys
import random
import base64
import r2pipe
import string
import shutil
import argparse

from copy import deepcopy
from dataclasses import dataclass
from scanner import DockerWindowsDefender
from find import bytes_detection
from find_bad_strings import bissect
from pe_utils import *
from tempfile import NamedTemporaryFile

log_format = '[%(levelname)-8s][%(asctime)s][%(filename)s:%(lineno)3d] %(funcName)s() :: %(message)s'
logging.basicConfig(filename='debug.log',
                            filemode='a',
                            format=format,
                            datefmt='%Y/%m/%d %H:%M',
                            level=logging.DEBUG
                    )


rootLogger = logging.getLogger()
logFormatter = logging.Formatter(log_format)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
rootLogger.addHandler(consoleHandler)

g_scanner = None
BINARY = ""

g_args = None
"""
attempts to locate the part in a PE file that causes the antivirus detection
"""
def locate_signature(pe):

    nb_section_detected = 0
    detected_sections = []

    for section in pe.sections:

        # copy the binary
        new_name = NamedTemporaryFile().name
        shutil.copyfile(pe.filename, new_name)

        # hide the section
        new_pe = deepcopy(pe)
        new_pe.filename = new_name
        hide_section(new_pe, section.name)
        new_pe.md5 = md5(new_name)

        logging.debug(f"Scanning {new_name} md5 = {new_pe.md5}")
        # scan it
        status = not g_scanner.scan(new_pe.filename)

        # record the result
        section.detected = not status

        if status:
            logging.info(f"Section {section.name} triggers the antivirus")
            nb_section_detected += 1
            detected_sections += [section]

    logging.info(f"{nb_section_detected} section(s) trigger the antivirus")
    return nb_section_detected, detected_sections


def bytes_analysis(pe, start_address, end_address):

    # check that masking these bytes is sufficient to evade the antivirus
    # copy the binary
    new_name = NamedTemporaryFile().name
    shutil.copyfile(pe.filename, new_name)

    # hide the byzes
    new_pe = deepcopy(pe)
    new_pe.filename = new_name
    hide_bytes(new_pe, start_address, end_address-start_address)
    status = g_scanner.scan(new_pe.filename)

    if status:
        logging.debug(new_pe.filename)
        spwn_dbg()
        raise Exception("No idea. Your binary is indeed detected but hiding everything but the PE header results in detection anyways. Check PE header.")


    # a signature is present in these bystes, let's binary search

    bytes_detection(new_pe.filename, start_address, end_address)


def strings_analysis(pe):


    # no point in continuing if the binary is not detected as malicious already.
    #assert(scan(sample_file) is True)

    str_refs = pe.strings
    logging.debug(f"Got {len(str_refs)} string objects")

    # mask all strings
    logging.debug("Patching all the strings in the binary")

    # patch the binary (mask the string)
    for str_ref in str_refs:
        str_ref.should_mask = True

        # copy the binary
    new_name = NamedTemporaryFile().name
    shutil.copyfile(pe.filename, new_name)

    # hide the byzes
    new_pe = deepcopy(pe)
    new_pe.filename = new_name

    pipe = r2pipe.open(new_name, flags=["-w"])
    patch_binary_mass(new_name, str_refs, pipe)

    logging.debug("Binary patched")
    detection_result = not g_scanner.scan(new_name)

    # sometimes there are signatures in the .txt sections
    if detection_result:
        logging.info(f"{g_scanner.scanner_name} seems to only detect strings in this binary.")
    else:
        logging.warn(f"Patching all the strings does not evade detection.")

    return detection_result



def multi_signatures_analysis(pe):



    for section in pe.sections:
        new_pe = backup_pe(pe)

        hide_all_sections_except(new_pe, section.name)

        status = g_scanner.scan(new_pe.filename)

        if status:
            logging.info(f"Section {section.name} has a signature")

    return True # TODO


def investigate(pe):

    status = g_scanner.scan(pe.filename)

    if not status:

        logging.error(f"{pe.filename} is not detected by {g_scanner.scanner_name}")
        return

    if not g_args.skip_strings:
        strings_based_detection = strings_analysis(pe)
    else:
        strings_based_detection = False

    if not g_args.skip_sections and not g_args.section:
        nb_sections, detected_sections = locate_signature(pe)
    else:
        nb_sections = 0

    if not strings_based_detection and nb_sections == 0 and not g_args.section:

        if multi_signatures_analysis(pe):

            logging.info(f"Signature in .text section, but also elsewhere")
        else:

            logging.info(f"{g_scanner.scanner_name} seems to have a dumb bytes-based detection engine")
            sections_addr = [section.addr for section in pe.sections]
            start_address = min(sections_addr)
            end_address = max(sections_addr)
            assert(end_address > start_address)
            bytes_analysis(pe, start_address, end_address)

    elif nb_sections == 0 and not g_args.section:
        logging.info("Finding the high score strings will be sufficient to evade the engine")
        bissect(pe.filename)

    elif nb_sections == 1:
        logging.info(f"Launching bytes analysis on section {detected_sections[0].name}")
        bytes_analysis(pe, detected_sections[0].addr, detected_sections[0].addr+detected_sections[0].size)
    elif g_args.section:
        logging.info(f"Launching bytes analysis on section {g_args.section}")

        section = next((sec for sec in pe.sections if sec.name == g_args.section), None)
        bytes_analysis(pe, section.addr, section.addr+section.size)



def parse_pe(sample_file):

    pe = PE()
    pe.filename = sample_file
    pe.sections = get_sections(pe)
    pe.strings = parse_strings(sample_file, g_args.extensive, g_args.length)
    pe.md5 = md5(sample_file)
    return pe


if __name__ == "__main__":

    g_scanner = DockerWindowsDefender()
    parser = argparse.ArgumentParser()

    parser.add_argument("-s", '--skip-strings', help="Skip strings analysis", action="store_true")
    parser.add_argument("-z", "--skip-sections", help="Skip sections analysis", action="store_true")
    parser.add_argument("-f", "--file", help="path to file")
    parser.add_argument("-e", "--extensive", help="search strings in all sections", action="store_true")
    parser.add_argument("-l", "--length", help="minimum length of strings", type=int, default=5)
    parser.add_argument('-c', '--section', help="Analyze provided section")
    g_args = parser.parse_args()

    pe = parse_pe(g_args.file)
    investigate(pe)
